"""
Deterministic replay engine (Section 3.3) -- the path an AI agent triggers in
production. No LLM in this file, by design; every decision here is either
"execute this step" or "match one of the artifact's declared expected_outcomes".

Locator resolution order per step (see artifact/schema.py Locator docstring):
role+name -> text -> css. Each step also gets a short bounded retry (wait/
retry, not indefinite polling) before being treated as a hard failure -- this
is the "transient slowness" handling called out in Section 1/3.3, kept
separate from the artifact's named expected_outcomes because it's a generic
robustness behavior, not a business condition.

Outcome classification, every step:
  1. Try to resolve + execute the step's own action.
  2. If that fails OR immediately after it succeeds, check the step's
     expected_outcomes in order: does the `detect` locator resolve on the
     current page? If so, classify per that outcome's outcome_class and act
     per its recovery (dismiss-and-continue re-drives this step; escalate
     hands off to a human via the same InterventionStore as discovery;
     stop_as_business_outcome / stop_as_hard_failure end the run and are
     reported distinctly to the caller -- this is the "no such member is a
     legitimate result, not a crash" distinction from Section 1).
  3. If nothing matches and the action itself failed, it's an
     undifferentiated hard failure -- still reported with step/expected/
     observed detail for debugging, never a silent crash.

Final success requires BOTH "ran out of steps without a stop" AND the
artifact's success_checkpoint resolving on the page -- executing the last
click is not proof the goal was reached (Section 1's "assuming the click
worked" pitfall, named directly in the glossary's Checkpoint entry).

Self-healing note: this file makes NO LLM calls by default, preserving the
deterministic/zero-cost replay guarantee (see dashboard/reliability.py,
dashboard/cost_estimate.py). An OPT-IN self-heal path exists
(self_heal_enabled=True / run_replay.py's --self-heal flag) that calls out
to replay/self_heal.py on a locator hard-failure specifically -- see that
module's docstring for the full design. Plain replay, the default, is
unaffected and remains LLM-free.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from artifact.schema import (
    ActionType, CapabilityArtifact, Locator, LocatorKind, OutcomeClass,
    RecoveryAction, ReplayResult, ReplayStatus, RiskLevel,
)
from evidence.logger import RunLogger
from escalation.models import InterventionStore, wait_for_resume
from guardrails.policy import GuardrailEngine, PolicyViolation

STEP_RETRY_ATTEMPTS = 3
STEP_RETRY_DELAY_S = 1.0
_SELF_HEAL_SUCCEEDED_NO_OUTCOME = object()


def _resolve(page: Page, locator: Locator | None):
    """Try each strategy in order; return the first that resolves to >=1 element.

    Raises a clear LookupError (not an AttributeError) when the step's target
    is None -- this happens today when discovery records a step (e.g. FILL)
    without ever capturing a locator for it. That's a discovery-side gap, but
    replay must still fail legibly rather than crash on `None.strategies`.
    """
    if locator is None:
        raise LookupError(
            "Step has no target locator recorded (target=None in the artifact). "
            "This step cannot be replayed until discovery captures a locator for it."
        )
    last_exc = None
    for strat in locator.strategies:
        try:
            if strat.kind == LocatorKind.ROLE and strat.role:
                loc = page.get_by_role(strat.role, name=strat.value, exact=False).first
            elif strat.kind == LocatorKind.TEXT:
                # Exact match first, substring as fallback -- mirrors the
                # fix in agent/discovery.py's _locate() (see FINDINGS.md
                # Finding #5). This file's docstring already claimed the
                # two matched; they didn't, until now. Root cause of all
                # three target_app_v2 replay hard-failures on 2026-09-10
                # (cap_53043bc026, cap_a65b404837, cap_a9447b47e3): a
                # substring-only match on "Search" resolved to the inert
                # "Package Search" header instead of the real button, so
                # the click had no effect and every step after it failed
                # because the page never navigated off the search form.
                exact_loc = page.get_by_text(strat.value, exact=True)
                if exact_loc.count() > 0:
                    loc = exact_loc.first
                else:
                    loc = page.get_by_text(strat.value, exact=False).first
            elif strat.kind == LocatorKind.CSS:
                loc = page.locator(strat.value).first
            elif strat.kind == LocatorKind.XPATH:
                loc = page.locator(f"xpath={strat.value}").first
            else:
                continue
            if loc.count() > 0:
                return loc, strat
        except Exception as e:
            last_exc = e
            continue
    if last_exc:
        raise last_exc
    raise LookupError(f"No strategy resolved for locator: {locator.description}")


def _fill_params(value: str | None, params: dict) -> str | None:
    if value is None:
        return None
    def sub(m):
        return str(params.get(m.group(1), m.group(0)))
    return re.sub(r"\{(\w+)\}", sub, value)


class ReplayEngine:
    def __init__(self, guardrails: GuardrailEngine, logger: RunLogger, headless: bool = True,
                 escalate_enabled: bool = True, escalate_timeout: float = 900.0,
                 storage_state_path: str | None = None, self_heal_enabled: bool = False,
                 artifacts_dir: Path | None = None):
        self.guardrails = guardrails
        self.logger = logger
        self.headless = headless
        self.escalate_enabled = escalate_enabled
        self.escalate_timeout = escalate_timeout
        self.storage_state_path = storage_state_path
        # Opt-in only -- see replay/self_heal.py's module docstring point 3.
        # Plain ReplayEngine() with no flag makes zero LLM calls, unchanged
        # from before this feature existed.
        self.self_heal_enabled = self_heal_enabled
        self.artifacts_dir = artifacts_dir or Path(__file__).parent.parent / "artifacts"
        self._healer = None

    def replay(self, artifact: CapabilityArtifact, params: dict) -> ReplayResult:
        self._validate_params(artifact, params)
        self.guardrails.check_url(artifact.contract.entry_url)
        self.logger.log("replay_start", artifact_id=artifact.artifact_id,
                         version=artifact.version, params=params)

        outputs: dict = {}
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(storage_state=self.storage_state_path) if self.storage_state_path \
                else browser.new_context()
            page = context.new_page()
            page.goto(artifact.contract.entry_url)

            for step in artifact.steps:
                result = self._run_step(page, artifact, step, params, outputs)
                if result is not None:
                    browser.close()
                    self.logger.save_result(result.model_dump_json(indent=2))
                    return result

            # All steps executed without an early stop -- verify the checkpoint.
            try:
                _resolve(page, artifact.success_checkpoint)
                checkpoint_ok = True
            except Exception:
                checkpoint_ok = False

            browser.close()

            if checkpoint_ok:
                result = ReplayResult(
                    status=ReplayStatus.SUCCESS, artifact_id=artifact.artifact_id,
                    artifact_version=artifact.version, outputs=outputs,
                    message="Goal reached; success checkpoint verified.",
                )
            else:
                result = ReplayResult(
                    status=ReplayStatus.HARD_FAILURE, artifact_id=artifact.artifact_id,
                    artifact_version=artifact.version, outputs=outputs,
                    expected=artifact.success_checkpoint.description,
                    observed="checkpoint not found after all steps executed",
                    message="All steps ran but the success checkpoint could not be verified.",
                )
            self.logger.save_result(result.model_dump_json(indent=2))
            return result

    # ------------------------------------------------------------------ #

    def _validate_params(self, artifact: CapabilityArtifact, params: dict) -> None:
        for spec in artifact.contract.input_schema:
            if spec.required and spec.name not in params:
                raise ValueError(f"Missing required input parameter '{spec.name}'.")

    def _run_step(self, page: Page, artifact: CapabilityArtifact, step, params: dict, outputs: dict) -> ReplayResult | None:
        """Returns a ReplayResult if the run should stop here (outcome/failure), else None."""
        self.guardrails.check_action_type(step.action.value)
        self.guardrails.authorize(step.risk, confirmed=(step.risk != RiskLevel.IRREVERSIBLE), mode="replay")

        attempt = 0
        while True:
            attempt += 1
            try:
                self._execute_step(page, step, params, outputs)
                self.logger.log("step_executed", step_id=step.step_id, action=step.action.value)
                break
            except Exception as ex:
                # Check declared expected_outcomes before treating this as generic failure.
                outcome_result = self._check_expected_outcomes(page, artifact, step, params, ex)
                if outcome_result is not None:
                    return outcome_result
                if attempt < STEP_RETRY_ATTEMPTS:
                    self.logger.log("step_retry", step_id=step.step_id, attempt=attempt, error=str(ex))
                    time.sleep(STEP_RETRY_DELAY_S)
                    continue
                # Self-heal: one extra attempt, opt-in only (self.self_heal_enabled),
                # and only for locator-resolution failures specifically (LookupError),
                # not arbitrary exceptions like a navigation timeout -- those aren't
                # "the locator was wrong," they're a different class of problem
                # self-healing a locator can't fix. See replay/self_heal.py's module
                # docstring for the full design rationale.
                if self.self_heal_enabled and isinstance(ex, LookupError) and step.target is not None:
                    healed_outcome = self._attempt_self_heal(page, artifact, step, params, outputs, ex)
                    if healed_outcome is _SELF_HEAL_SUCCEEDED_NO_OUTCOME:
                        # Step completed via the healed locator, no special
                        # outcome matched -- same as an ordinary successful
                        # step. Break out of the retry loop and let _run_step
                        # continue to its own post-loop outcome check / the
                        # caller's next step, exactly like the normal
                        # success path a few lines below this whole block.
                        break
                    if healed_outcome is not None:
                        return healed_outcome
                    # Healing didn't produce a working result -- fall through to the
                    # normal hard-failure path below, unchanged.
                self._screenshot(page, f"{step.step_id}_hard_failure")
                return ReplayResult(
                    status=ReplayStatus.HARD_FAILURE, artifact_id=artifact.artifact_id,
                    artifact_version=artifact.version, outputs=outputs,
                    failed_step_id=step.step_id,
                    expected=step.target.description if step.target else step.action.value,
                    observed=f"{type(ex).__name__}: {ex}",
                    message=f"Step {step.step_id} ({step.action.value}) failed after {attempt} attempts.",
                )

        # After a successful execute, also check outcomes that don't raise (e.g. a
        # business-outcome banner that renders on a 200 page, not an exception).
        outcome_result = self._check_expected_outcomes(page, artifact, step, params, None)
        return outcome_result
    
    def _attempt_self_heal(self, page: Page, artifact: CapabilityArtifact, step, params: dict,
                            outputs: dict, original_exc: Exception) -> ReplayResult | None:
        """One extra, clearly-logged attempt using an LLM-proposed locator
        before giving up. Returns a ReplayResult if the healed locator
        worked (SUCCESS-shaped, same as a normal step pass), or None if
        healing didn't help -- in which case the caller falls through to
        the normal hard-failure path unchanged. Never raises: any failure
        in the healing attempt itself (no API key, LLM error, proposed
        locator also doesn't resolve) is treated as "healing didn't help",
        not a new error path.
        """
        from replay.self_heal import SelfHealer, apply_heal_and_save  # local import: only pull in
        # the anthropic dependency when self-healing is actually enabled and triggered.
 
        self.logger.log("self_heal_attempt", step_id=step.step_id,
                         original_error=f"{type(original_exc).__name__}: {original_exc}")
        try:
            if self._healer is None:
                self._healer = SelfHealer()
            heal_result = self._healer.propose_locator(
                page, step.target.description, step.target.strategies,
            )
        except Exception as heal_exc:
            self.logger.log("self_heal_error", step_id=step.step_id, error=str(heal_exc))
            return None
 
        if not heal_result.healed:
            self.logger.log("self_heal_no_proposal", step_id=step.step_id,
                             reasoning=heal_result.reasoning)
            return None
 
        # Try the proposed strategy directly against the LIVE page first, before
        # touching any file -- if it doesn't actually work, there's no point
        # writing a new artifact version for a locator that doesn't resolve.
        try:
            healed_locator = Locator(
                description=step.target.description,
                strategies=list(step.target.strategies) + [heal_result.new_strategy],
            )
            loc, used_strategy = _resolve(page, healed_locator)
            self._execute_step_with_locator(page, step, params, outputs, loc)
        except Exception as retry_exc:
            self.logger.log("self_heal_proposal_failed", step_id=step.step_id,
                             proposed_kind=heal_result.new_strategy.kind.value,
                             error=str(retry_exc))
            return None
 
        # It worked live -- persist the healed artifact as a new version so
        # future replays benefit too (module docstring point 2: additive,
        # never overwrites the original).
        out_path = apply_heal_and_save(artifact, step, heal_result.new_strategy, self.artifacts_dir)
        self.logger.log("self_heal_succeeded", step_id=step.step_id,
                         proposed_kind=heal_result.new_strategy.kind.value,
                         proposed_value=heal_result.new_strategy.value,
                         reasoning=heal_result.reasoning,
                         new_artifact_path=str(out_path))
 
        self.logger.log("step_executed", step_id=step.step_id, action=step.action.value, self_healed=True)
 
        # The step itself succeeded via the healed locator. Check for any
        # expected_outcome that might apply (same as a normal successful
        # step -- see the bottom of _run_step). Crucially: _check_expected_
        # outcomes returning None here means "no special outcome, proceed
        # normally" -- NOT "healing failed". Returning that None directly
        # from this method would be indistinguishable from a failed heal to
        # the caller, which was a real bug caught in testing: the caller
        # (_run_step) treats any None from this method as "fall through to
        # hard failure", so a successful-but-outcome-less heal would have
        # been incorrectly reported as hard_failure. Use a sentinel instead.
        outcome_result = self._check_expected_outcomes(page, artifact, step, params, None)
        return outcome_result if outcome_result is not None else _SELF_HEAL_SUCCEEDED_NO_OUTCOME
 
    def _execute_step_with_locator(self, page: Page, step, params: dict, outputs: dict, loc) -> None:
        """Same action-dispatch logic as _execute_step, but against an
        already-resolved locator (the healed one) rather than re-resolving
        step.target from scratch. Kept as a separate small method rather
        than duplicating _execute_step's full body inline in the healing
        path."""
        action = step.action
        if action == ActionType.CLICK:
            loc.click(timeout=8000)
        elif action == ActionType.FILL:
            loc.fill(_fill_params(step.value, params) or "", timeout=8000)
        elif action == ActionType.SELECT:
            loc.select_option(_fill_params(step.value, params), timeout=8000)
        elif action == ActionType.ASSERT_STATE:
            loc.wait_for(timeout=8000, state="visible")
        elif action == ActionType.EXTRACT:
            text = loc.inner_text(timeout=8000).strip()
            if step.extract_as:
                outputs[step.extract_as] = text
        else:
            raise ValueError(f"Self-heal doesn't support action {action}")
        page.wait_for_load_state("networkidle", timeout=5000)

    def _execute_step(self, page: Page, step, params: dict, outputs: dict) -> None:
        action = step.action
        if action == ActionType.NAVIGATE:
            url = _fill_params(step.value, params)
            self.guardrails.check_url(url)
            page.goto(url)
        elif action == ActionType.CLICK:
            loc, _ = _resolve(page, step.target)
            loc.click(timeout=8000)
        elif action == ActionType.FILL:
            loc, _ = _resolve(page, step.target)
            loc.fill(_fill_params(step.value, params) or "", timeout=8000)
        elif action == ActionType.SELECT:
            loc, _ = _resolve(page, step.target)
            loc.select_option(_fill_params(step.value, params), timeout=8000)
        elif action == ActionType.ASSERT_STATE:
            loc, _ = _resolve(page, step.target)
            loc.wait_for(timeout=8000, state="visible")
        elif action == ActionType.EXTRACT:
            loc, _ = _resolve(page, step.target)
            text = loc.inner_text(timeout=8000).strip()
            if step.extract_as:
                outputs[step.extract_as] = text
        else:
            raise ValueError(f"Unsupported action {action}")
        page.wait_for_load_state("networkidle", timeout=5000)

    def _check_expected_outcomes(self, page: Page, artifact: CapabilityArtifact, step, params: dict, exc: Exception | None) -> ReplayResult | None:
        for outcome in step.expected_outcomes:
            try:
                _resolve(page, outcome.detect)
            except Exception:
                continue  # this outcome's condition is not present; check next

            self.logger.log("expected_outcome_matched", step_id=step.step_id, outcome=outcome.name,
                             outcome_class=outcome.outcome_class.value)

            if outcome.recovery == RecoveryAction.STOP_AS_BUSINESS_OUTCOME:
                self._screenshot(page, f"{step.step_id}_{outcome.name}")
                return ReplayResult(
                    status=ReplayStatus.BUSINESS_OUTCOME, artifact_id=artifact.artifact_id,
                    artifact_version=artifact.version, result_code=outcome.result_code,
                    message=outcome.message_template, failed_step_id=step.step_id,
                )
            if outcome.recovery == RecoveryAction.STOP_AS_HARD_FAILURE:
                self._screenshot(page, f"{step.step_id}_{outcome.name}")
                return ReplayResult(
                    status=ReplayStatus.HARD_FAILURE, artifact_id=artifact.artifact_id,
                    artifact_version=artifact.version, result_code=outcome.result_code,
                    message=outcome.message_template, failed_step_id=step.step_id,
                    expected=step.target.description if step.target else None,
                    observed=outcome.name,
                )
            if outcome.recovery == RecoveryAction.DISMISS_AND_CONTINUE and outcome.recovery_locator:
                try:
                    loc, _ = _resolve(page, outcome.recovery_locator)
                    loc.click(timeout=8000)
                    page.wait_for_load_state("networkidle", timeout=5000)
                    self.logger.log("recoverable_dismissed", step_id=step.step_id, outcome=outcome.name)
                    return self._run_step(page, artifact, step, params, {})  # re-drive this step
                except Exception as ex2:
                    self.logger.log("recovery_failed", step_id=step.step_id, error=str(ex2))
                    continue
            if outcome.recovery in (RecoveryAction.WAIT_AND_RETRY, RecoveryAction.RETRY):
                time.sleep(STEP_RETRY_DELAY_S)
                continue
            if outcome.recovery == RecoveryAction.ESCALATE_TO_HUMAN:
                if not self._escalate(page, artifact, step, outcome.name):
                    return ReplayResult(
                        status=ReplayStatus.HARD_FAILURE, artifact_id=artifact.artifact_id,
                        artifact_version=artifact.version, result_code=outcome.result_code,
                        message=f"Escalation required ({outcome.name}) but was not resolved.",
                        failed_step_id=step.step_id,
                    )
                return self._run_step(page, artifact, step, params, {})
        return None

    def _escalate(self, page: Page, artifact: CapabilityArtifact, step, reason: str) -> bool:
        if not self.escalate_enabled:
            return False
        shot = self.logger.screenshot_path(f"escalation_{step.step_id}")
        try:
            page.screenshot(path=str(shot))
        except Exception:
            shot = None
        store = InterventionStore(self.logger.dir / "intervention.json")
        req = store.create(
            run_id=self.logger.run_id, goal=artifact.contract.goal_description,
            step_id=step.step_id, reason=reason,
            screenshot_path=str(shot) if shot else None, current_url=page.url,
        )
        self.logger.log("intervention_created", intervention_id=req.intervention_id, reason=reason)
        print(f"\n[ESCALATION] Replay stuck at step {step.step_id}: {reason}")
        print(f"[ESCALATION] Run: python -m escalation.resolve {self.logger.run_id} \"notes\" once resolved.")
        try:
            wait_for_resume(store, timeout=self.escalate_timeout)
        except TimeoutError:
            return False
        return True

    def _screenshot(self, page: Page, label: str) -> None:
        try:
            page.screenshot(path=str(self.logger.screenshot_path(label)))
        except Exception:
            pass
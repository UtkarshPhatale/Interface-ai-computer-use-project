"""
Trace -> CapabilityArtifact.

This is the "decoupled from the raw model transcript" step (Section 3.2): it
takes the mechanical discovery trace (what actions were taken and against
which role/name targets) and produces the reviewable, agent-invocable
artifact -- adding the things a raw transcript doesn't have:

  1. Parameterization: literal values that came from the caller's input
     params are replaced with '{param_name}' placeholders, so the artifact
     is reusable across different member IDs, not just the one used during
     discovery.
  2. Locator strategy ranking: role+name promoted to the primary strategy,
     with a text fallback appended, per the robustness reasoning in
     artifact/schema.py.
  3. Risk classification: any step that submits data which creates/mutates a
     record (a `fill`/`click` inside the open-sub-account flow) is tagged
     IRREVERSIBLE; everything else SAFE. This mapping is target-app-specific
     knowledge -- see REPORT.md ("Cuts") for why this isn't yet learned
     automatically.
  4. Expected outcomes: attaches the known runtime conditions for this
     target app (member not found, restricted/permission-denied, validation
     error, re-auth interstitial) to the steps where they can occur. This is
     also target-app-specific enrichment, not something the LLM discovery
     loop is asked to infer during a single happy-path run -- see REPORT.md.
  5. Contract: derives input_schema from `params`, output_schema from
     extract_as fields actually populated during discovery, and sets the
     success_checkpoint from the final assert_state step (or the last step
     if the model never explicitly asserted one).
"""
from __future__ import annotations

import uuid

from agent.discovery import DiscoveryTrace, TraceStep
from artifact.schema import (
    ActionType, CapabilityArtifact, CapabilityContract, ExpectedOutcome,
    Locator, LocatorKind, LocatorStrategy, OutcomeClass, OutputSpec,
    ParamSpec, ParamType, RecoveryAction, RiskLevel, Step,
)

# Target-app-specific knowledge: which steps are irreversible, and what
# runtime conditions can occur where. Kept as a small table rather than
# inferred, per the module docstring above.
IRREVERSIBLE_STEP_NAMES = {"submit", "continue", "open sub-account"}

MEMBER_NOT_FOUND_OUTCOME = ExpectedOutcome(
    name="member_not_found",
    detect=Locator(
        description="Not-found banner",
        strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value="No member record")],
    ),
    outcome_class=OutcomeClass.BUSINESS_OUTCOME,
    recovery=RecoveryAction.STOP_AS_BUSINESS_OUTCOME,
    result_code="MEMBER_NOT_FOUND",
    message_template="No member found for the given ID.",
)

RESTRICTED_OUTCOME = ExpectedOutcome(
    name="member_restricted",
    detect=Locator(
        description="Access restricted banner",
        strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value="Access Restricted")],
    ),
    outcome_class=OutcomeClass.BUSINESS_OUTCOME,
    recovery=RecoveryAction.STOP_AS_BUSINESS_OUTCOME,
    result_code="MEMBER_RESTRICTED",
    message_template="Member is restricted; this action requires elevated permissions.",
)

VALIDATION_ERROR_OUTCOME = ExpectedOutcome(
    name="validation_error",
    detect=Locator(
        description="Inline form validation error",
        strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value="Initial deposit must be")],
    ),
    outcome_class=OutcomeClass.HARD_FAILURE,
    recovery=RecoveryAction.STOP_AS_HARD_FAILURE,
    result_code="VALIDATION_ERROR",
    message_template="The submitted form data failed validation.",
)

REAUTH_INTERSTITIAL_OUTCOME = ExpectedOutcome(
    name="reauth_interstitial",
    detect=Locator(
        description="Unexpected re-auth security check dialog",
        strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value="Security Check")],
    ),
    outcome_class=OutcomeClass.RECOVERABLE,
    recovery=RecoveryAction.DISMISS_AND_CONTINUE,
    result_code="REAUTH_INTERSTITIAL_DISMISSED",
    message_template="An unexpected re-auth prompt was dismissed automatically.",
    recovery_locator=Locator(
        description="Continue button on the security-check interstitial",
        strategies=[LocatorStrategy(kind=LocatorKind.ROLE, value="Continue", role="button"),
                    LocatorStrategy(kind=LocatorKind.TEXT, value="Continue")],
    ),
)

SESSION_TIMEOUT_OUTCOME = ExpectedOutcome(
    name="session_timeout",
    detect=Locator(
        description="Redirected back to login (session expired)",
        strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value="CoreServ Servicing Console - Login")],
    ),
    outcome_class=OutcomeClass.HARD_FAILURE,
    recovery=RecoveryAction.ESCALATE_TO_HUMAN,
    result_code="SESSION_EXPIRED",
    message_template="The operator session expired mid-flow.",
)


def _param_key_for_value(value: str, params: dict[str, str]) -> str | None:
    for k, v in params.items():
        if v and value and v == value:
            return k
    return None


def _to_locator(role: str | None, name: str | None) -> Locator | None:
    if not name:
        return None
    strategies = []
    if role and role != "text":
        strategies.append(LocatorStrategy(kind=LocatorKind.ROLE, value=name, role=role))
    if role in ("textbox", "combobox", "searchbox"):
        # Deliberately skip the generic TEXT strategy for form controls: it
        # would match the label's own text node (e.g. a <td>) rather than
        # the input beside it. Go straight to XPath "nearest input following
        # this label text" -- see agent/discovery.py's _locate() for the
        # matching discovery-time logic.
        strategies.append(LocatorStrategy(
            kind=LocatorKind.XPATH,
            value=f"//*[contains(normalize-space(text()), '{name}')]/following::input[1]",
        ))
    else:
        strategies.append(LocatorStrategy(kind=LocatorKind.TEXT, value=name))
    return Locator(strategies=strategies, description=f"{role or 'text'} '{name}'")


def _risk_for(step: TraceStep) -> RiskLevel:
    if step.action_type == "click" and step.locator_name and \
            step.locator_name.strip().lower() in IRREVERSIBLE_STEP_NAMES:
        return RiskLevel.IRREVERSIBLE
    if step.action_type in ("fill", "select"):
        return RiskLevel.REVIEW
    return RiskLevel.SAFE


def build_artifact(trace: DiscoveryTrace, name: str, vendor_product: str = "coreserv") -> CapabilityArtifact:
    if not trace.success:
        raise ValueError("Cannot build an artifact from a discovery trace that did not succeed.")

    steps: list[Step] = []
    outputs: list[OutputSpec] = []

    for i, ts in enumerate(trace.steps):
        step_id = f"s{i:02d}"
        action = ActionType(ts.action_type)
        target = _to_locator(ts.locator_role, ts.locator_name)

        value = ts.value
        if action in (ActionType.FILL, ActionType.NAVIGATE) and value:
            param_key = _param_key_for_value(value, trace.params)
            if param_key:
                value = "{" + param_key + "}"

        expected_outcomes = []
        if action == ActionType.FILL and target and "member id" in (target.description or "").lower():
            expected_outcomes = []  # handled at the subsequent assert/navigate step
        if action == ActionType.CLICK and target and "look up" in (target.description or "").lower():
            expected_outcomes = [MEMBER_NOT_FOUND_OUTCOME]
        if action == ActionType.CLICK and target and target.description and \
                any(k in target.description.lower() for k in ("view record", "open sub-account")):
            expected_outcomes = [RESTRICTED_OUTCOME]
        if action == ActionType.CLICK and target and target.description and \
                any(k in target.description.lower() for k in ("submit", "continue")):
            expected_outcomes = [VALIDATION_ERROR_OUTCOME, REAUTH_INTERSTITIAL_OUTCOME]
        # Session timeout can, in principle, surface after any navigation/click; attach broadly.
        if action in (ActionType.CLICK, ActionType.NAVIGATE):
            expected_outcomes = expected_outcomes + [SESSION_TIMEOUT_OUTCOME]

        step = Step(
            step_id=step_id,
            action=action,
            target=target,
            value=value,
            risk=_risk_for(ts),
            expected_outcomes=expected_outcomes,
            extract_as=ts.extract_label,
        )
        if action in (ActionType.CLICK, ActionType.FILL, ActionType.SELECT,
                      ActionType.ASSERT_STATE, ActionType.EXTRACT) and target is None:
            raise ValueError(
                f"Step {step_id} ({action.value}) has no resolvable target -- the discovery trace "
                f"recorded an empty locator_name, which would produce an unusable artifact step."
            )
        steps.append(step)

        if ts.extract_label:
            outputs.append(OutputSpec(name=ts.extract_label, type=ParamType.STRING,
                                       description=f"Value extracted at step {step_id}."))

    # success checkpoint = last assert_state step's target, else last step's target/description
    assert_steps = [s for s in steps if s.action == ActionType.ASSERT_STATE]
    if assert_steps:
        checkpoint = assert_steps[-1].target
    elif steps and steps[-1].target:
        checkpoint = steps[-1].target
    else:
        checkpoint = Locator(strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value=trace.done_summary or "done")],
                              description="Fallback checkpoint from done_summary")

    input_schema = [
        ParamSpec(name=k, type=ParamType.STRING, required=True, description=f"Input parameter '{k}'.")
        for k in trace.params
    ]

    contract = CapabilityContract(
        goal_description=trace.goal,
        target_app="coreserv-legacy-terminal",
        entry_url=trace.entry_url,
        input_schema=input_schema,
        output_schema=outputs,
        preconditions=["authenticated operator session (session cookie present)"],
    )

    return CapabilityArtifact(
        artifact_id=f"cap_{uuid.uuid4().hex[:10]}",
        name=name,
        contract=contract,
        steps=steps,
        success_checkpoint=checkpoint,
        vendor_product=vendor_product,
    )

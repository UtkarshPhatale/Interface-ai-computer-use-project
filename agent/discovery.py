"""
Discovery agent: the observe -> decide -> act loop (Section 3.1).

Mechanism choice: Playwright driving a real browser, perceiving the page via
its ACCESSIBILITY TREE (role + accessible name) rather than raw DOM/CSS. This
is deliberate: the brief asks to "bias toward an approach that would still
work when the surface has no clean DOM" (Section 3.1) -- the accessibility
tree is available even on legacy server-rendered markup with no test IDs,
and (per the glossary) is available on desktop apps too, which matters for
the generalization story in REPORT.md Section 4.

The LLM only ever sees: the accessibility tree (compact text form), the
current URL, and the goal. It never sees raw HTML. It responds via a single
structured tool call (`take_action`) so every decision is a typed action,
not free text we have to parse -- this is what keeps the recorded trace
mechanically translatable into artifact Steps later.

This module produces a DISCOVERY TRACE, not the final artifact. See
artifact/builder.py for the trace -> CapabilityArtifact transformation. This
split is what lets the artifact be "decoupled from the raw model transcript"
(Section 3.2) while keeping the LLM loop itself simple: it only has to
accomplish the goal, not simultaneously author error-handling metadata.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from playwright.sync_api import sync_playwright, Page

from evidence.logger import RunLogger
from guardrails.policy import GuardrailEngine, PolicyViolation
from escalation.models import InterventionStore, wait_for_resume

MAX_STEPS = 15
MODEL = "claude-sonnet-4-6"

TOOL_SCHEMA = {
    "name": "take_action",
    "description": "Decide the single next action to take on the page, or declare the goal done/stuck.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string", "description": "Brief reasoning for this action."},
            "action_type": {
                "type": "string",
                "enum": ["navigate", "click", "fill", "select", "extract", "assert_state", "done", "stuck"],
            },
            "locator_role": {"type": "string", "description": "Accessibility role, e.g. 'button', 'textbox', 'link'. Required for click/fill/select/assert_state/extract."},
            "locator_name": {"type": "string", "description": "Accessible name/label of the target element, or the exact visible text for assert_state. Required for click/fill/select/assert_state/extract."},
            "value": {"type": "string", "description": "For navigate: URL. For fill: text to type. For select: option value/label."},
            "extract_label": {"type": "string", "description": "For extract: a short field name for the captured value, e.g. 'savings_balance'."},
            "done_summary": {"type": "string", "description": "For action_type=done: what was accomplished."},
            "stuck_reason": {"type": "string", "description": "For action_type=stuck: why the agent cannot safely proceed."},
        },
        "required": ["reasoning", "action_type"],
    },
}

SYSTEM_PROMPT = """You are an operator driving a legacy internal bank servicing web application \
on behalf of an automation system. You act ONLY through the `take_action` tool -- one action per turn.

You perceive the page only via its accessibility tree (role + name), not raw HTML. Legacy apps like \
this one have no test IDs, so always target elements by role + visible accessible name.

Rules:
- One action per turn. Wait for the next observation before deciding the following action.
- ALWAYS supply both locator_role and a non-empty locator_name for click/fill/select/assert_state/extract -- \
never leave locator_name blank. If an element has no accessible name in the tree (common in legacy markup), \
use the nearest visible label text you can see near it instead (e.g. an adjacent table cell's text like \
"Member ID") -- this is required for the action to be recorded correctly, even though it isn't a strict \
accessibility-tree name.
- Use action_type="extract" to record a data value from the page you need to return (e.g. a balance).
- Use action_type="assert_state" to confirm you've reached an expected checkpoint (e.g. a confirmation page). \
Use locator_role="text" and locator_name=<the exact heading/text you expect to see> for this.
- Use action_type="done" only once the goal is fully accomplished, and summarize what was achieved.
- Use action_type="stuck" if you cannot find a way to proceed safely or the page is in an unexpected state \
you don't recognize -- do not guess wildly or click things you're unsure about.
- Never invent data. Only use the exact goal parameters given to you.
"""


@dataclass
class TraceStep:
    action_type: str
    locator_role: Optional[str] = None
    locator_name: Optional[str] = None
    value: Optional[str] = None
    extract_label: Optional[str] = None
    reasoning: str = ""
    resulting_url: str = ""


@dataclass
class DiscoveryTrace:
    goal: str
    entry_url: str
    params: dict[str, str]
    steps: list[TraceStep] = field(default_factory=list)
    extracted: dict[str, str] = field(default_factory=dict)
    done_summary: Optional[str] = None
    stuck_reason: Optional[str] = None
    success: bool = False


def _ax_tree_text(page: Page, max_nodes: int = 120) -> str:
    """
    Render the accessibility tree as a compact, LLM-friendly text listing.

    Playwright's accessibility surface changed across versions: older releases
    exposed `page.accessibility.snapshot()` (a node tree we'd walk by hand);
    current releases (~1.5x+) removed that in favor of `aria_snapshot()`,
    which returns a YAML-style text rendering of roles/names directly. We
    prefer the latter when available since it's what's actually maintained,
    and fall back to the legacy walker for older Playwright installs.
    """
    try:
        text = page.locator("body").aria_snapshot()
        if text and text.strip():
            lines = text.strip().splitlines()
            return "\n".join(lines[:max_nodes])
    except Exception:
        pass

    # Fallback for Playwright versions that still expose the legacy API.
    try:
        snapshot = page.accessibility.snapshot(interesting_only=True)
    except Exception:
        return "(accessibility tree unavailable on this Playwright version)"

    lines: list[str] = []

    def walk(node: dict, depth: int):
        if len(lines) >= max_nodes or node is None:
            return
        role = node.get("role", "")
        name = node.get("name", "")
        value = node.get("value", "")
        if role and role not in ("generic", "none"):
            piece = f"{'  ' * depth}{role}"
            if name:
                piece += f" '{name}'"
            if value:
                piece += f" [value={value}]"
            lines.append(piece)
        for child in node.get("children", []) or []:
            walk(child, depth + 1)

    if snapshot:
        walk(snapshot, 0)
    return "\n".join(lines) if lines else "(empty accessibility tree)"


def _locate(page: Page, role: str, name: str):
    """
    Resolve a role+name pair to a Playwright locator, trying strategies in
    order of robustness and checking each one actually matches before
    committing to it -- mirrors replay/engine.py's _resolve() so discovery
    and replay locator semantics stay identical.

    For form-control roles (textbox/combobox/searchbox), generic text
    matching is deliberately SKIPPED as a fallback: a plain-text match for
    "Member ID" resolves to the label's own <td>, not the input next to it
    -- matching but wrong. Instead we go straight to an XPath "nearest input
    following this label text" fallback, which covers the common legacy
    pattern where a field's label is an unassociated table cell (no
    <label for=...>, no aria-label).

    Text fallback tries EXACT match before substring match. Root cause this
    guards against (found via target_app_v2, evidence/runs/discovery-
    20260909T235011-3c5c35): a page with both "Package Search" (a header)
    and "Search" (a clickable div) on it -- get_by_text("Search",
    exact=False).first silently matched the inert header text first (DOM
    order), because "Search" is a substring of "Package Search". The click
    executed without error and had zero effect, and the caller had no way
    to tell the difference from a real click, since Playwright doesn't
    raise just because a click landed on the "wrong" (but real) element.
    Preferring an exact match first resolves this whenever the target
    element's full text is exactly the name being searched for, which is
    the common case for short button/link labels.
    """
    def _text_locator(text_name: str):
        exact_loc = page.get_by_text(text_name, exact=True)
        if exact_loc.count() > 0:
            return exact_loc.first
        return page.get_by_text(text_name, exact=False).first

    if role == "text":
        return _text_locator(name)

    try:
        loc = page.get_by_role(role, name=name, exact=False).first
        if loc.count() > 0:
            return loc
    except Exception:
        pass

    if role in ("textbox", "combobox", "searchbox"):
        try:
            xp = f"xpath=//*[contains(normalize-space(text()), '{name}')]/following::input[1]"
            loc = page.locator(xp).first
            if loc.count() > 0:
                return loc
        except Exception:
            pass
        # Nothing matched -- return the role-based locator anyway so the
        # caller's own timeout/error path produces a clear failure.
        return page.get_by_role(role, name=name, exact=False).first

    try:
        loc = _text_locator(name)
        if loc.count() > 0:
            return loc
    except Exception:
        pass

    return _text_locator(name)


class DiscoveryAgent:
    def __init__(self, guardrails: GuardrailEngine, logger: RunLogger, headless: bool = True,
                 escalate_enabled: bool = True, escalate_timeout: float = 900.0,
                 storage_state_path: str | None = None):
        self.guardrails = guardrails
        self.logger = logger
        self.headless = headless
        self.escalate_enabled = escalate_enabled
        self.escalate_timeout = escalate_timeout
        self.storage_state_path = storage_state_path
        try:
            import anthropic
            self.client = anthropic.Anthropic()
        except Exception as e:
            raise RuntimeError(
                "Anthropic SDK not available or ANTHROPIC_API_KEY not set. "
                "Install with `pip install anthropic` and set the env var."
            ) from e

    def run(self, goal: str, entry_url: str, params: dict[str, str]) -> DiscoveryTrace:
        trace = DiscoveryTrace(goal=goal, entry_url=entry_url, params=params)
        self.guardrails.check_url(entry_url)
        self.logger.log("discovery_start", goal=goal, entry_url=entry_url, params=params)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(storage_state=self.storage_state_path) if self.storage_state_path \
                else browser.new_context()
            page = context.new_page()
            page.goto(entry_url)
            trace.steps.append(TraceStep(action_type="navigate", value=entry_url, resulting_url=page.url))
            self._snapshot(page, "step_00_navigate")

            messages: list[dict[str, Any]] = [{
                "role": "user",
                "content": self._observation_block(page, goal, params, first=True),
            }]

            for i in range(1, MAX_STEPS + 1):
                response = self.client.messages.create(
                    model=MODEL,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    tools=[TOOL_SCHEMA],
                    tool_choice={"type": "tool", "name": "take_action"},
                    messages=messages,
                )
                tool_use = next(b for b in response.content if b.type == "tool_use")
                action = tool_use.input
                self.logger.log("llm_decision", step=i, action=action)

                if action["action_type"] == "done":
                    trace.done_summary = action.get("done_summary", "")
                    trace.success = True
                    self.logger.log("discovery_done", summary=trace.done_summary)
                    break

                if action["action_type"] == "stuck":
                    reason = action.get("stuck_reason", "unspecified")
                    if not self._escalate_and_wait(page, trace, i, reason):
                        trace.stuck_reason = reason
                        break
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_use.id,
                                     "content": self._observation_block(page, goal, params, first=False)}],
                    })
                    continue

                try:
                    self._execute(page, action, trace, i)
                except PolicyViolation as pv:
                    self.logger.log("guardrail_blocked", step=i, reason=str(pv))
                    trace.stuck_reason = f"guardrail_blocked: {pv}"
                    break
                except Exception as ex:
                    self.logger.log("execution_error", step=i, error=str(ex))
                    self._snapshot(page, f"step_{i:02d}_error")
                    if not self._escalate_and_wait(page, trace, i, f"execution_error: {ex}"):
                        trace.stuck_reason = f"execution_error: {ex}"
                        break
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_use.id,
                                     "content": self._observation_block(page, goal, params, first=False)}],
                    })
                    continue

                messages.append({"role": "assistant", "content": response.content})
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": self._observation_block(page, goal, params, first=False),
                    }],
                })

            browser.close()

        trace.extracted = {s.extract_label: s.value for s in trace.steps if s.extract_label and s.value}
        self.logger.log("discovery_end", success=trace.success, n_steps=len(trace.steps))
        return trace

    def _execute(self, page: Page, action: dict, trace: DiscoveryTrace, step_idx: int) -> None:
        atype = action["action_type"]
        role = action.get("locator_role")
        name = action.get("locator_name")
        value = action.get("value")

        if atype in ("click", "fill", "select", "assert_state", "extract") and not (name and name.strip()):
            raise ValueError(
                f"Model omitted locator_name for a '{atype}' action -- refusing to execute with an "
                f"ambiguous target, since the recorded step would be unusable in the final artifact."
            )

        if atype == "navigate":
            self.guardrails.check_url(value)
            page.goto(value)
        elif atype == "click":
            loc = _locate(page, role, name)
            loc.click(timeout=8000)
        elif atype == "fill":
            loc = _locate(page, role, name)
            loc.fill(value or "", timeout=8000)
        elif atype == "select":
            loc = _locate(page, role, name)
            loc.select_option(value, timeout=8000)
        elif atype == "assert_state":
            loc = page.get_by_text(name, exact=False).first
            loc.wait_for(timeout=8000, state="visible")
        elif atype == "extract":
            loc = _locate(page, role, name)
            extracted_value = loc.inner_text(timeout=8000)
            value = extracted_value.strip()
        else:
            raise ValueError(f"Unknown action_type {atype}")

        page.wait_for_load_state("networkidle", timeout=5000)
        step = TraceStep(
            action_type=atype, locator_role=role, locator_name=name,
            value=value, extract_label=action.get("extract_label"),
            reasoning=action.get("reasoning", ""), resulting_url=page.url,
        )
        trace.steps.append(step)
        self._snapshot(page, f"step_{step_idx:02d}_{atype}")
        self.logger.log("step_executed", step=step_idx, action_type=atype,
                         role=role, name=name, resulting_url=page.url)

    def _escalate_and_wait(self, page: Page, trace: DiscoveryTrace, step_idx: int, reason: str) -> bool:
        """Raise an intervention request, block until a human hands control back,
        then return True to resume the loop. Returns False if escalation is
        disabled (e.g. unattended CI runs) so the caller falls back to stopping."""
        if not self.escalate_enabled:
            return False
        shot = self.logger.screenshot_path(f"escalation_{step_idx:02d}")
        try:
            page.screenshot(path=str(shot))
        except Exception:
            shot = None
        store = InterventionStore(self.logger.dir / "intervention.json")
        req = store.create(
            run_id=self.logger.run_id, goal=trace.goal, step_id=f"discovery_step_{step_idx}",
            reason=reason, screenshot_path=str(shot) if shot else None, current_url=page.url,
        )
        self.logger.log("intervention_created", intervention_id=req.intervention_id, reason=reason)
        print(f"\n[ESCALATION] Automation is stuck: {reason}")
        print(f"[ESCALATION] The browser window is still open at {page.url}.")
        print(f"[ESCALATION] Complete the step manually, then run:\n"
              f"    python -m escalation.resolve {self.logger.run_id} \"what you did\"\n"
              f"[ESCALATION] Waiting for hand-back...")
        try:
            wait_for_resume(store, timeout=self.escalate_timeout)
        except TimeoutError:
            self.logger.log("intervention_timeout", intervention_id=req.intervention_id)
            return False
        self.logger.log("intervention_resolved", intervention_id=req.intervention_id)
        return True

    def _snapshot(self, page: Page, label: str) -> None:
        try:
            path = self.logger.screenshot_path(label)
            page.screenshot(path=str(path))
        except Exception:
            pass

    def _observation_block(self, page: Page, goal: str, params: dict, first: bool):
        ax = _ax_tree_text(page)
        text = (
            f"GOAL: {goal}\nPARAMETERS: {json.dumps(params)}\n"
            f"CURRENT URL: {page.url}\n\nACCESSIBILITY TREE:\n{ax}"
        )
        if first:
            return text
        return text
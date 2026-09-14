"""
Self-healing re-discovery (deferred Week 2 feature, FINDINGS.md "Open
items"; built after metrics/README per the deliberate scoping decision
logged there).

Design decisions, made explicit rather than left implicit in the code:

1. TRIGGER: only on ReplayStatus.HARD_FAILURE from an exhausted-retries
   locator resolution (a `LookupError` from _resolve). Business outcomes
   are usually correct behavior, not something to "heal."

2. WHAT HEALING PRODUCES: a NEW artifact version (e.g. 1.0.0 -> 1.0.1),
   never an in-place mutation of the original file. The healed locator is
   ADDED as an extra strategy on the failing step -- appended after the
   existing strategies, never replacing them -- so a step that started
   working again for an unrelated reason (e.g. a flaky network blip)
   still works via its original strategies first. This keeps healing
   strictly additive and reviewable as a diff, matching this project's
   existing "artifact is a reviewable PR artifact" design goal
   (artifact/schema.py's module docstring).

3. LLM USAGE: strictly OPT-IN via a separate flag (run_replay.py's
   --self-heal), OFF by default. Plain `run_replay.py` with no flag makes
   ZERO LLM calls and behaves exactly as before -- this preserves the
   project's core, now-metric-backed claim (see dashboard/reliability.py,
   dashboard/cost_estimate.py) that replay is deterministic and free.
   Self-healing is a deliberately distinct, clearly-logged mode for when
   a human wants automatic recovery instead of just failing to escalation
   -- not a silent behavior change to default replay.

4. IF HEALING ALSO FAILS: falls through to the existing hard-failure /
   escalation path unchanged. No new failure mode is introduced; healing
   only ever ADDS a chance of success, never removes an existing one.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page

from artifact.schema import (
    CapabilityArtifact, Locator, LocatorKind, LocatorStrategy, Step,
)

MODEL = "claude-sonnet-4-6"

HEAL_TOOL_SCHEMA = {
    "name": "propose_locator",
    "description": "Propose a new way to find the described target element on the current page, "
                    "given that all previously-recorded locator strategies failed to resolve it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string", "description": "Brief reasoning for this proposal, "
                          "including why the previous strategies likely failed."},
            "found": {"type": "boolean", "description": "True if a plausible new locator was found; "
                      "false if the element genuinely doesn't seem to exist on this page."},
            "kind": {"type": "string", "enum": ["role", "text", "xpath", "css"],
                     "description": "Required if found=true."},
            "value": {"type": "string", "description": "The selector/role-name/text/xpath itself. Required if found=true."},
            "role": {"type": "string", "description": "ARIA role, only when kind=role."},
        },
        "required": ["reasoning", "found"],
    },
}

HEAL_SYSTEM_PROMPT = """You are diagnosing why an automated UI step failed to find its target \
element, using only the page's accessibility tree (role + name), the same perception mode the \
original discovery agent uses -- not raw HTML.

You will be given: a description of what the step was trying to find, the locator strategies that \
were already tried and failed, and a snapshot of the current page's accessibility tree.

Propose ONE new locator strategy that might succeed where the previous ones didn't. Common real \
causes (see FINDINGS.md in this repo for actual examples): the accessible name changed slightly \
(e.g. copy edit), a role assumption was wrong (e.g. it's a link now, not a button), or the true \
target is a sibling element near a text label rather than the label itself. If nothing in the \
snapshot plausibly matches the description at all, set found=false rather than guessing -- a wrong \
guess that "resolves" to an unrelated element is worse than admitting failure, since it can silently \
click or fill the wrong thing.
"""


@dataclass
class HealResult:
    healed: bool
    new_strategy: Optional[LocatorStrategy] = None
    reasoning: str = ""


class SelfHealer:
    def __init__(self):
        try:
            import anthropic
            self.client = anthropic.Anthropic()
        except Exception as e:
            raise RuntimeError(
                "Anthropic SDK not available or ANTHROPIC_API_KEY not set. "
                "Self-healing requires an LLM call by design (see module docstring) -- "
                "this is expected to fail without a key; plain replay does not need one."
            ) from e

    def propose_locator(self, page: Page, target_description: str, tried_strategies: list[LocatorStrategy]) -> HealResult:
        tried_desc = "; ".join(f"{s.kind.value}='{s.value}'" for s in tried_strategies)
        snapshot_yaml = page.aria_snapshot(mode="ai")[:8000]  # bound prompt size

        message = self.client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=HEAL_SYSTEM_PROMPT,
            tools=[HEAL_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "propose_locator"},
            messages=[{
                "role": "user",
                "content": (
                    f"Target element description: {target_description}\n"
                    f"Strategies already tried and failed: {tried_desc}\n\n"
                    f"Current page accessibility tree (YAML, possibly truncated):\n{snapshot_yaml}"                ),
            }],
        )

        tool_use = next((b for b in message.content if b.type == "tool_use"), None)
        if tool_use is None:
            return HealResult(healed=False, reasoning="Model did not return a tool call.")

        args = tool_use.input
        if not args.get("found"):
            return HealResult(healed=False, reasoning=args.get("reasoning", ""))

        kind_str = args.get("kind")
        value = args.get("value")
        if not kind_str or not value:
            return HealResult(healed=False, reasoning="Model said found=true but omitted kind/value.")

        try:
            kind = LocatorKind(kind_str)
        except ValueError:
            return HealResult(healed=False, reasoning=f"Model proposed an unrecognized kind: {kind_str}")

        strategy = LocatorStrategy(kind=kind, value=value, role=args.get("role"))
        return HealResult(healed=True, new_strategy=strategy, reasoning=args.get("reasoning", ""))


def bump_patch_version(version: str) -> str:
    """1.0.0 -> 1.0.1. Deliberately simple (patch-only bump) since a
    self-heal is additive, not a design change -- doesn't warrant a
    minor/major bump under normal semver conventions."""
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return f"{version}-healed"
    major, minor, patch = parts
    return f"{major}.{minor}.{int(patch) + 1}"


def apply_heal_and_save(
    artifact: CapabilityArtifact, step: Step, new_strategy: LocatorStrategy, artifacts_dir: Path,
) -> Path:
    """Returns the path of the newly-written, versioned artifact. Never
    overwrites the original file -- see module docstring point 2."""
    healed = artifact.model_copy(deep=True)
    healed.version = bump_patch_version(artifact.version)

    for s in healed.steps:
        if s.step_id == step.step_id and s.target is not None:
            s.target.strategies = list(s.target.strategies) + [new_strategy]
            break

    out_path = artifacts_dir / f"{healed.artifact_id}_v{healed.version}.json"
    out_path.write_text(healed.model_dump_json(indent=2))
    return out_path
"""
The Capability Artifact schema.

This is the load-bearing data model of the whole system (Section 3.2 / 7.2 of the
brief). It is the thing a discovery run produces, a human reviews, and an AI agent
invokes in production via the replay engine -- decoupled entirely from the raw LLM
transcript that produced it.

Design goals, in priority order:
1. A calling agent must be able to understand what the capability does, what it
   needs, and what it returns WITHOUT reading the step list -- that's the
   `CapabilityContract` (goal, inputs, outputs, preconditions).
2. Every step's target element must carry enough locator redundancy to survive
   minor markup change, in priority order (role/name > text > css), because in the
   real environment there are no test IDs.
3. The result contract must distinguish success / expected business outcome /
   recoverable condition / hard failure -- so replay callers never mistake "member
   not found" for a system failure, and vice versa (Section 3.3).
4. Everything is JSON-serializable (pydantic) so it can be stored, diffed,
   versioned in git, and reviewed as a PR artifact.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Locator strategy
# --------------------------------------------------------------------------- #

class LocatorKind(str, Enum):
    ROLE = "role"            # accessibility role + accessible name (most robust)
    TEXT = "text"            # visible text match
    LABEL = "label"          # form label association
    CSS = "css"              # CSS selector (least robust, last resort)
    XPATH = "xpath"          # XPath -- for correctly-scoped row/cell targeting in table-based legacy markup
    URL_PATTERN = "url"      # canonicalized URL/route pattern, for navigation steps


class Locator(BaseModel):
    """
    How to find a control on screen.

    A locator is a ranked list of strategies, not a single selector. Robustness
    reasoning: role/name survives markup rewrites and CSS refactors (it reflects
    what a human/screen-reader perceives); text survives most changes except
    copy edits; CSS is a last-resort fallback because it's the most coupled to
    implementation detail and the thing legacy apps are least likely to keep
    stable. The replay engine tries strategies in order and records which one
    actually worked (see ExecutionEvidence.locator_used) -- that telemetry is
    what surfaces drift (Section 3.7) over time.
    """
    strategies: list["LocatorStrategy"] = Field(
        description="Ordered list, most robust first. Replay tries each in order."
    )
    description: str = Field(
        description="Human-readable description of the target, e.g. "
                    "'Look Up button on the member search form'."
    )


class LocatorStrategy(BaseModel):
    kind: LocatorKind
    value: str = Field(description="The selector/role-name/text/pattern itself.")
    role: Optional[str] = Field(
        default=None, description="ARIA/accessibility role, when kind=role (e.g. 'button', 'textbox')."
    )


# --------------------------------------------------------------------------- #
# Steps / actions
# --------------------------------------------------------------------------- #

class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    WAIT_FOR = "wait_for"
    EXTRACT = "extract"
    ASSERT_STATE = "assert_state"   # a checkpoint: verify we reached expected state


class RiskLevel(str, Enum):
    SAFE = "safe"               # read-only / fully reversible (navigate, extract, click "back")
    REVIEW = "review"           # reversible but consequential (form fill, navigation into a flow)
    IRREVERSIBLE = "irreversible"  # creates/modifies a record; cannot be trivially undone


class Step(BaseModel):
    step_id: str
    action: ActionType
    target: Optional[Locator] = Field(
        default=None, description="Required for click/fill/select/wait_for/assert_state; "
                                   "omitted for navigate (uses `value` as URL) and pure extracts "
                                   "that read page-level text."
    )
    value: Optional[str] = Field(
        default=None,
        description="Literal value, OR a '{param_name}' placeholder resolved from "
                    "input_schema at replay time (e.g. '{member_id}')."
    )
    risk: RiskLevel = RiskLevel.SAFE
    timeout_ms: int = 5000
    expected_outcomes: list["ExpectedOutcome"] = Field(
        default_factory=list,
        description="Runtime conditions this step might legitimately hit besides "
                    "the happy path (validation error, not-found, interstitial, "
                    "session timeout), and how to classify + handle each."
    )
    extract_as: Optional[str] = Field(
        default=None, description="If action=EXTRACT, the output field name this populates."
    )


class OutcomeClass(str, Enum):
    BUSINESS_OUTCOME = "business_outcome"   # legitimate result, not a crash (e.g. "not found")
    RECOVERABLE = "recoverable"             # transient/known; replay handles and continues
    HARD_FAILURE = "hard_failure"           # stop and surface a debuggable error


class RecoveryAction(str, Enum):
    RETRY = "retry"
    DISMISS_AND_CONTINUE = "dismiss_and_continue"
    WAIT_AND_RETRY = "wait_and_retry"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    STOP_AS_BUSINESS_OUTCOME = "stop_as_business_outcome"
    STOP_AS_HARD_FAILURE = "stop_as_hard_failure"


class ExpectedOutcome(BaseModel):
    """
    A named runtime condition a step might hit, detected via a locator/pattern,
    classified, and paired with a recovery action. This is how the artifact
    encodes 'the interesting failures aren't layout drift, they're runtime
    conditions' (Section 1) as data rather than replay-engine special casing.
    """
    name: str                      # e.g. "member_not_found", "validation_error"
    detect: Locator                # how replay recognizes this condition occurred
    outcome_class: OutcomeClass
    recovery: RecoveryAction
    result_code: str = Field(
        description="Stable machine-readable code returned to the caller, "
                    "e.g. 'MEMBER_NOT_FOUND'."
    )
    message_template: Optional[str] = None
    recovery_locator: Optional[Locator] = Field(
        default=None,
        description="For recovery=DISMISS_AND_CONTINUE: what to click to dismiss "
                    "the interstitial (e.g. a 'Continue' button)."
    )


# --------------------------------------------------------------------------- #
# Contract: inputs / outputs / preconditions
# --------------------------------------------------------------------------- #

class ParamType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ENUM = "enum"


class ParamSpec(BaseModel):
    name: str
    type: ParamType
    required: bool = True
    description: str = ""
    enum_values: Optional[list[str]] = None
    sensitive: bool = Field(
        default=False,
        description="If true, this value is redacted from logs/evidence (Section 3.4)."
    )


class OutputSpec(BaseModel):
    name: str
    type: ParamType
    description: str = ""


class CapabilityContract(BaseModel):
    """
    The agent-facing surface: what an AI agent needs to know to CALL this
    capability, without reading a single step. Section 3.2: 'needs a clear
    contract, not just a step list.'
    """
    goal_description: str
    target_app: str
    entry_url: str
    input_schema: list[ParamSpec]
    output_schema: list[OutputSpec]
    preconditions: list[str] = Field(
        default_factory=list,
        description="e.g. 'authenticated operator session'; not enforced by the "
                    "artifact itself, but declared so callers/guardrails can check."
    )


# --------------------------------------------------------------------------- #
# Top-level artifact
# --------------------------------------------------------------------------- #

class ArtifactStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class CapabilityArtifact(BaseModel):
    """
    The full, versioned, serializable capability. This is what gets saved to
    artifacts/*.json, reviewed by a human, and loaded by the replay engine.
    """
    artifact_id: str
    name: str
    version: str = "1.0.0"
    status: ArtifactStatus = ArtifactStatus.DRAFT
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: Literal["llm_discovery", "manual", "generalized"] = "llm_discovery"

    contract: CapabilityContract
    steps: list[Step]

    success_checkpoint: Locator = Field(
        description="Final assertion that confirms the goal was actually reached "
                    "-- not just that the last click didn't throw."
    )

    # Multi-tenant reuse fields (Section 3.7) -- see REPORT.md for the full design.
    vendor_product: Optional[str] = Field(
        default=None, description="Underlying vendor product this artifact targets, "
                                   "for cross-tenant reuse grouping."
    )
    tenant_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional per-tenant override map (locator/value patches) "
                    "keyed by tenant id. See REPORT.md Section 4."
    )

    model_config = ConfigDict(use_enum_values=False)


# --------------------------------------------------------------------------- #
# Replay result contract
# --------------------------------------------------------------------------- #

class ReplayStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    RECOVERABLE_HANDLED = "recoverable_handled"   # hit but auto-resolved; still succeeded
    ESCALATED = "escalated"                       # handed off to a human, run paused
    HARD_FAILURE = "hard_failure"


class ReplayResult(BaseModel):
    status: ReplayStatus
    artifact_id: str
    artifact_version: str
    outputs: dict[str, Any] = Field(default_factory=dict)
    result_code: Optional[str] = None
    message: Optional[str] = None
    failed_step_id: Optional[str] = None
    expected: Optional[str] = None
    observed: Optional[str] = None
    evidence_path: Optional[str] = None
    intervention_id: Optional[str] = None

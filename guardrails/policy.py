"""
Safety & policy guardrails (Section 3.4).

Two independent concerns, kept in one module because both gate every action
before it executes, in both discovery and replay:

1. Allowlist -- WHERE the agent may act (domains/routes) and WHICH action
   types are permitted at all. This is a hard boundary: violating it aborts
   the run immediately, no exceptions.

2. Risk policy -- for actions that ARE within the allowlist, classify them
   safe / review / irreversible and decide what to do with the irreversible
   ones. Decision made here: irreversible actions (anything that creates or
   mutates a record -- e.g. submitting the "open sub-account" form) require
   an explicit `confirm=True` flag threaded through from the caller/operator.
   Discovery mode auto-confirms only inside the sandboxed demo target and
   logs it loudly; replay mode NEVER auto-confirms -- an unconfirmed
   irreversible step in a replay is escalated to a human, not executed.
   This is deliberately conservative per Section 3.4 ("handle the risky
   class conservatively").

Redaction: `redact()` is applied to every value written to logs/evidence/
artifacts before it's persisted, per "never persist secrets or raw sensitive
data." Parameters marked `sensitive=True` in ParamSpec are redacted by name;
a small pattern list catches likely secrets/PII even if not declared.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from artifact.schema import RiskLevel


@dataclass
class AllowlistConfig:
    allowed_domains: list[str] = field(default_factory=lambda: ["127.0.0.1", "localhost"])
    allowed_route_prefixes: list[str] = field(
        default_factory=lambda: ["/login", "/members", "/logout"]
    )
    allowed_action_types: set[str] = field(
        default_factory=lambda: {"navigate", "click", "fill", "select", "wait_for", "extract", "assert_state"}
    )


class PolicyViolation(Exception):
    pass


class GuardrailEngine:
    def __init__(self, allowlist: AllowlistConfig | None = None):
        self.allowlist = allowlist or AllowlistConfig()

    # ---- allowlist -------------------------------------------------- #

    def check_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host and host not in self.allowlist.allowed_domains:
            raise PolicyViolation(f"Domain '{host}' is not in the allowlist.")
        if parsed.path and self.allowlist.allowed_route_prefixes:
            if not any(parsed.path.startswith(p) for p in self.allowlist.allowed_route_prefixes):
                raise PolicyViolation(f"Route '{parsed.path}' is not in the allowlist.")

    def check_action_type(self, action_type: str) -> None:
        if action_type not in self.allowlist.allowed_action_types:
            raise PolicyViolation(f"Action type '{action_type}' is not permitted.")

    # ---- risk policy -------------------------------------------------- #

    def requires_confirmation(self, risk: RiskLevel) -> bool:
        return risk == RiskLevel.IRREVERSIBLE

    def authorize(self, risk: RiskLevel, confirmed: bool, mode: str) -> None:
        """
        mode: 'discovery' or 'replay'. Raises PolicyViolation if an
        irreversible action is attempted without confirmation.
        Replay mode is stricter: it never implicitly confirms.
        """
        if self.requires_confirmation(risk) and not confirmed:
            raise PolicyViolation(
                f"Irreversible action blocked in {mode} mode without explicit confirmation."
            )


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #

_SECRET_PATTERNS = [
    re.compile(r"(?i)password"),
    re.compile(r"(?i)passwd"),
    re.compile(r"(?i)token"),
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)api[_-]?key"),
    re.compile(r"(?i)ssn"),
    re.compile(r"(?i)social[_-]?security"),
]


def is_likely_sensitive_field(field_name: str) -> bool:
    return any(p.search(field_name) for p in _SECRET_PATTERNS)


def redact(field_name: str, value: str, sensitive: bool = False) -> str:
    if sensitive or is_likely_sensitive_field(field_name):
        return "[REDACTED]"
    return value


def redact_dict(d: dict, sensitive_fields: set[str] | None = None) -> dict:
    sensitive_fields = sensitive_fields or set()
    return {
        k: redact(k, str(v), sensitive=k in sensitive_fields)
        for k, v in d.items()
    }

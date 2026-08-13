from artifact.schema import RiskLevel
from guardrails.policy import AllowlistConfig, GuardrailEngine, PolicyViolation, redact, redact_dict


def test_allowlist_blocks_disallowed_domain():
    g = GuardrailEngine(AllowlistConfig(allowed_domains=["127.0.0.1"]))
    try:
        g.check_url("https://evil.example.com/steal")
        assert False, "should have raised"
    except PolicyViolation:
        pass


def test_allowlist_permits_allowed_domain_and_route():
    g = GuardrailEngine(AllowlistConfig(allowed_domains=["127.0.0.1"], allowed_route_prefixes=["/members"]))
    g.check_url("http://127.0.0.1/members/12345")  # should not raise


def test_allowlist_blocks_disallowed_route():
    g = GuardrailEngine(AllowlistConfig(allowed_domains=["127.0.0.1"], allowed_route_prefixes=["/members"]))
    try:
        g.check_url("http://127.0.0.1/admin/danger")
        assert False, "should have raised"
    except PolicyViolation:
        pass


def test_irreversible_action_requires_confirmation():
    g = GuardrailEngine(AllowlistConfig())
    try:
        g.authorize(RiskLevel.IRREVERSIBLE, confirmed=False, mode="replay")
        assert False, "should have raised"
    except PolicyViolation:
        pass
    g.authorize(RiskLevel.IRREVERSIBLE, confirmed=True, mode="replay")  # should not raise
    g.authorize(RiskLevel.SAFE, confirmed=False, mode="replay")  # safe never needs confirmation


def test_redact_hides_likely_secrets():
    assert redact("password", "hunter2") == "[REDACTED]"
    assert redact("member_name", "Jordan Alvarez") == "Jordan Alvarez"
    assert redact("api_key", "sk-abc123") == "[REDACTED]"


def test_redact_dict_marks_explicit_sensitive_fields():
    out = redact_dict({"member_id": "12345", "ssn": "111-22-3333"}, sensitive_fields={"member_id"})
    assert out["member_id"] == "[REDACTED]"
    assert out["ssn"] == "[REDACTED]"  # caught by pattern too

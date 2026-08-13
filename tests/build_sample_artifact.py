"""
Builds a sample CapabilityArtifact by hand, structurally identical to what
artifact/builder.py would produce from a real discovery trace of the "look
up a member and read their savings balance" goal. Used to test the replay
engine without requiring a live LLM call (see tests/test_replay.py).
"""
from artifact.builder import MEMBER_NOT_FOUND_OUTCOME, RESTRICTED_OUTCOME, SESSION_TIMEOUT_OUTCOME
from artifact.schema import (
    ActionType, CapabilityArtifact, CapabilityContract, Locator, LocatorKind,
    LocatorStrategy, OutputSpec, ParamSpec, ParamType, RiskLevel, Step,
)


def sample_lookup_balance_artifact() -> CapabilityArtifact:
    contract = CapabilityContract(
        goal_description="Look up a member by ID and read their current savings balance.",
        target_app="coreserv-legacy-terminal",
        entry_url="http://127.0.0.1:5055/members/search",
        input_schema=[ParamSpec(name="member_id", type=ParamType.STRING, required=True,
                                 description="The member ID to look up.")],
        output_schema=[OutputSpec(name="savings_balance", type=ParamType.STRING,
                                   description="The member's savings balance as displayed.")],
        preconditions=["authenticated operator session (session cookie present)"],
    )

    steps = [
        Step(
            step_id="s00", action=ActionType.FILL,
            target=Locator(description="textbox 'Member ID'",
                            strategies=[LocatorStrategy(kind=LocatorKind.ROLE, value="Member ID", role="textbox"),
                                        LocatorStrategy(kind=LocatorKind.CSS, value="input[name=member_id]")]),
            value="{member_id}", risk=RiskLevel.SAFE,
        ),
        Step(
            step_id="s01", action=ActionType.CLICK,
            target=Locator(description="button 'Look Up'",
                            strategies=[LocatorStrategy(kind=LocatorKind.ROLE, value="Look Up", role="button")]),
            risk=RiskLevel.SAFE,
            expected_outcomes=[MEMBER_NOT_FOUND_OUTCOME, SESSION_TIMEOUT_OUTCOME],
        ),
        Step(
            step_id="s02", action=ActionType.CLICK,
            target=Locator(description="link 'View Record'",
                            strategies=[LocatorStrategy(kind=LocatorKind.ROLE, value="View Record", role="link")]),
            risk=RiskLevel.SAFE,
            expected_outcomes=[RESTRICTED_OUTCOME],
        ),
        Step(
            step_id="s03", action=ActionType.EXTRACT,
            target=Locator(description="savings balance cell",
                            strategies=[LocatorStrategy(
                                kind=LocatorKind.XPATH,
                                value="//tr[td[1][contains(., 'Savings Balance')]]/td[2]")]),
            extract_as="savings_balance", risk=RiskLevel.SAFE,
        ),
    ]

    checkpoint = Locator(description="Member Record heading",
                          strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value="Member Record")])

    return CapabilityArtifact(
        artifact_id="cap_sample0001", name="lookup_savings_balance",
        contract=contract, steps=steps, success_checkpoint=checkpoint,
        vendor_product="coreserv",
    )


if __name__ == "__main__":
    a = sample_lookup_balance_artifact()
    print(a.model_dump_json(indent=2))

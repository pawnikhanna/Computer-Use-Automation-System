import json
from artifacts.schema import (
    Capability, ArtifactMeta, ParameterSpec, ParamType, OutputSpec,
    Step, ActionType, RiskLevel, Locator, LocatorStrategy, LocatorStrategyKind,
    BusinessOutcome, Checkpoint,
)


def _sample_capability() -> Capability:
    return Capability(
        capability_id="lookup_member_savings_balance",
        name="Look up member and read savings balance",
        description="Logs in, looks up a member by ID, reads their savings balance.",
        meta=ArtifactMeta(discovered_by_model="claude-haiku-4-5-20251001", discovery_run_id="run_test123"),
        target_base_url="http://127.0.0.1:5055",
        entry_path="/login",
        requires_auth=True,
        input_params=[
            ParameterSpec(name="operator_username", type=ParamType.STRING, example="operator"),
            ParameterSpec(name="member_id", type=ParamType.STRING, example="12345"),
        ],
        outputs=[OutputSpec(name="savings_balance", type=ParamType.STRING)],
        steps=[
            Step(step_id="s0", action=ActionType.NAVIGATE, url="/login"),
            Step(
                step_id="s1", action=ActionType.FILL, input_binding="operator_username",
                target=Locator(description="Username", strategies=[
                    LocatorStrategy(kind=LocatorStrategyKind.LABEL_TEXT, value="Username:", robustness_note="row label"),
                ]),
            ),
            Step(
                step_id="s2", action=ActionType.CLICK, risk_level=RiskLevel.SAFE_REVERSIBLE,
                target=Locator(description="Log In", strategies=[
                    LocatorStrategy(kind=LocatorStrategyKind.ROLE_NAME, value="Log In", robustness_note="accessible name"),
                ]),
            ),
            Step(
                step_id="s3", action=ActionType.EXTRACT, extract_as="savings_balance",
                target=Locator(description="Savings Balance", strategies=[
                    LocatorStrategy(kind=LocatorStrategyKind.LABEL_TEXT, value="Savings Balance", robustness_note="table row field"),
                ]),
            ),
        ],
        business_outcomes=[
            BusinessOutcome(name="member_not_found", detect_text="No member found matching ID", result_shape={"found": False}),
        ],
        success_checkpoint=Checkpoint(kind="text_present", value="Savings Balance"),
    )


def test_capability_round_trips_through_json():
    cap = _sample_capability()
    raw = cap.model_dump_json()
    restored = Capability.model_validate(json.loads(raw))
    assert restored.capability_id == cap.capability_id
    assert len(restored.steps) == len(cap.steps)
    assert restored.steps[1].input_binding == "operator_username"
    assert restored.steps[3].extract_as == "savings_balance"
    assert restored.business_outcomes[0].result_shape == {"found": False}


def test_no_secrets_hardcoded_in_login_step():
    # The username fill step must be parameter-bound, never a literal value --
    # this is the concrete guardrail-3.4 check: credentials never persist
    # into the artifact itself.
    cap = _sample_capability()
    login_fill_step = cap.steps[1]
    assert login_fill_step.input_binding is not None
    assert login_fill_step.literal_value is None
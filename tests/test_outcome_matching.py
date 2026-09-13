from artifacts.schema import BusinessOutcome
from replay.outcome_matching import match_business_outcome

OUTCOMES = [
    BusinessOutcome(name="member_not_found", detect_text="No member found matching ID",
                     result_shape={"found": False}),
    BusinessOutcome(name="member_access_denied", detect_status_code=403,
                     result_shape={"found": True, "access_denied": True}),
    BusinessOutcome(name="record_archived", detect_url_contains="/archived",
                     result_shape={"found": True, "archived": True}),
]


def test_matches_on_text():
    result = match_business_outcome(
        body_text='No member found matching ID "00000".', status_code=200,
        current_url="/members/00000", outcomes=OUTCOMES,
    )
    assert result.name == "member_not_found"


def test_matches_on_status_code():
    result = match_business_outcome(
        body_text="Access to member 99999 is restricted.", status_code=403,
        current_url="/members/99999", outcomes=OUTCOMES,
    )
    assert result.name == "member_access_denied"


def test_matches_on_url():
    result = match_business_outcome(
        body_text="Some page", status_code=200,
        current_url="/members/12345/archived", outcomes=OUTCOMES,
    )
    assert result.name == "record_archived"


def test_no_match_returns_none_on_happy_path():
    result = match_business_outcome(
        body_text="Savings Balance: [REDACTED_AMOUNT]", status_code=200,
        current_url="/members/12345", outcomes=OUTCOMES,
    )
    assert result is None


def test_first_declared_outcome_wins_when_multiple_could_match():
    # If a page happened to match two patterns, declaration order decides --
    # documented behavior, not accidental.
    outcomes = [
        BusinessOutcome(name="first", detect_text="ERROR", result_shape={"a": 1}),
        BusinessOutcome(name="second", detect_status_code=500, result_shape={"a": 2}),
    ]
    result = match_business_outcome(body_text="ERROR occurred", status_code=500,
                                     current_url="/x", outcomes=outcomes)
    assert result.name == "first"
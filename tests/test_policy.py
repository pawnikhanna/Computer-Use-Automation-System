import pytest
from guardrails.policy import default_policy, PolicyViolation, redact


def test_allowed_navigation_passes():
    policy = default_policy()
    policy.check_navigation("/members/search")  # should not raise


def test_disallowed_host_blocked():
    policy = default_policy()
    with pytest.raises(PolicyViolation):
        policy.check_navigation("http://evil.example.com/steal")


def test_disallowed_path_blocked():
    policy = default_policy()
    with pytest.raises(PolicyViolation):
        policy.check_navigation("/admin/delete-everything")


def test_disallowed_action_type_blocked():
    policy = default_policy()
    with pytest.raises(PolicyViolation):
        policy.check_action_type("delete_database")


def test_risky_click_text_detected():
    policy = default_policy()
    assert policy.is_risky("Confirm and Open Account") is True
    assert policy.is_risky("Search") is False
    assert policy.is_risky(None) is False


def test_redact_masks_dollar_amounts_and_long_numbers():
    text = "Savings Balance: $4231.50, Member ID: 123456789"
    redacted = redact(text)
    assert "$4231.50" not in redacted
    assert "123456789" not in redacted
    assert "[REDACTED_AMOUNT]" in redacted
    assert "[REDACTED_NUMBER]" in redacted


def test_redact_leaves_short_numbers_alone():
    # A 5-digit member ID like 12345 should NOT be redacted -- only long runs
    # (account numbers, SSN-like) are, per the redaction module's threshold.
    text = "Member ID: 12345"
    redacted = redact(text)
    assert "12345" in redacted
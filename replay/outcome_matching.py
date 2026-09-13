from __future__ import annotations

from typing import Optional
from artifacts.schema import BusinessOutcome


def match_business_outcome(
    body_text: str,
    status_code: Optional[int],
    current_url: str,
    outcomes: list[BusinessOutcome],
) -> Optional[BusinessOutcome]:
    """Return the first BusinessOutcome whose detection pattern matches the
    current page state, or None if none match. Checked in declaration order."""
    for outcome in outcomes:
        if outcome.detect_text and outcome.detect_text in body_text:
            return outcome
        if outcome.detect_status_code is not None and outcome.detect_status_code == status_code:
            return outcome
        if outcome.detect_url_contains and outcome.detect_url_contains in current_url:
            return outcome
    return None
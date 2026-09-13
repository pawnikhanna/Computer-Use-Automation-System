from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


class PolicyViolation(Exception):
    """Raised when an action would step outside the configured allowlist."""


@dataclass
class Policy:
    allowed_host: str
    allowed_path_prefixes: list[str]
    allowed_action_types: set[str]
    # Substrings (case-insensitive) on a control's visible text that mark the
    # action as risky/irreversible -- these require explicit confirmation
    # rather than silent auto-execution, in BOTH discovery and replay.
    risky_text_patterns: list[str] = field(default_factory=lambda: [
        "confirm and open", "confirm", "delete", "close account",
        "transfer", "submit payment",
    ])

    def check_navigation(self, url: str):
        parsed = urlparse(url)
        host = parsed.netloc or self.allowed_host  # relative URLs assume allowed host
        if host and host != self.allowed_host:
            raise PolicyViolation(f"Navigation to disallowed host: {host}")
        # NOTE: root "/" is allowed as an EXACT match only (it just redirects to
        # /login) -- it must never be included as a *prefix*, since every path
        # starts with "/" and that would silently defeat this allowlist entirely.
        is_root = parsed.path == "/"
        is_allowed_prefix = any(
            parsed.path.startswith(p) for p in self.allowed_path_prefixes
        )
        if not (is_root or is_allowed_prefix):
            raise PolicyViolation(f"Navigation to disallowed path: {parsed.path}")

    def check_action_type(self, action_type: str):
        if action_type not in self.allowed_action_types:
            raise PolicyViolation(f"Action type not permitted by policy: {action_type}")

    def is_risky(self, control_text: str | None) -> bool:
        if not control_text:
            return False
        lowered = control_text.lower()
        return any(pat in lowered for pat in self.risky_text_patterns)


# --- Redaction ---------------------------------------------------------------

_DOLLAR_AMOUNT = re.compile(r"\$\s?\d[\d,]*\.\d{2}")
_LONG_DIGIT_RUN = re.compile(r"\b\d{6,}\b")  # catches account numbers, SSN-like runs


def redact(text: str) -> str:
    """Redact sensitive-looking values before writing to logs/evidence.
    Conservative on purpose: over-redacting a log is a minor debugging cost,
    under-redacting regulated financial data is not acceptable."""
    if not text:
        return text
    text = _DOLLAR_AMOUNT.sub("[REDACTED_AMOUNT]", text)
    text = _LONG_DIGIT_RUN.sub("[REDACTED_NUMBER]", text)
    return text


def default_policy() -> Policy:
    return Policy(
        allowed_host="127.0.0.1:5055",
        allowed_path_prefixes=["/login", "/dashboard", "/members"],
        allowed_action_types={"navigate", "fill", "select", "click", "extract"},
    )
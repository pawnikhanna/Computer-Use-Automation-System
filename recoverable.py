from __future__ import annotations

from artifacts.schema import RecoverableCondition, Locator, LocatorStrategy, LocatorStrategyKind

KNOWN_CONDITIONS: list[RecoverableCondition] = [
    RecoverableCondition(
        name="system_notice_interstitial",
        detect_text="SYSTEM NOTICE",
        dismiss_action=Locator(
            description="Acknowledge button on the system notice banner",
            strategies=[
                LocatorStrategy(
                    kind=LocatorStrategyKind.ROLE_NAME,
                    value="Acknowledge",
                    robustness_note=(
                        "Banner is injected non-deterministically by the app; "
                        "targeting by accessible button name is stable across "
                        "appearances since the banner's markup doesn't change."
                    ),
                ),
                LocatorStrategy(
                    kind=LocatorStrategyKind.TEXT_EXACT,
                    value="Acknowledge",
                    robustness_note="Fallback: exact visible text match.",
                ),
            ],
        ),
    ),
]


def try_dismiss(page, conditions: list[RecoverableCondition], body_text: str) -> str | None:
    """Check body_text for any known recoverable condition; if found, perform
    its dismissal action on the live page and return the condition's name.
    Returns None if no known condition is present."""
    for cond in conditions:
        if cond.detect_text and cond.detect_text in body_text:
            for strategy in cond.dismiss_action.strategies:
                try:
                    if strategy.kind == LocatorStrategyKind.ROLE_NAME:
                        page.get_by_role("button", name=strategy.value).click(timeout=2000)
                    elif strategy.kind == LocatorStrategyKind.TEXT_EXACT:
                        page.get_by_text(strategy.value, exact=True).click(timeout=2000)
                    else:
                        continue
                    return cond.name
                except Exception:
                    continue  # try next fallback strategy
    return None
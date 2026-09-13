from __future__ import annotations

from artifacts.schema import Locator, LocatorStrategyKind
from replay.errors import HardFailure


def resolve(page, locator: Locator, purpose: str, step_id: str):
    """Try each strategy in order; return (playwright_locator, strategy_used).
    Raises HardFailure if every strategy fails."""
    attempts = []
    for strategy in locator.strategies:
        try:
            candidate = None

            if strategy.kind == LocatorStrategyKind.LABEL_TEXT:
                # Legacy convention: <tr><td>Label</td><td>[control or text]</td></tr>
                row_cell = page.locator(
                    f'xpath=//tr[td[normalize-space(text())="{strategy.value}"]]/td[2]'
                )
                if purpose == "control":
                    candidate = row_cell.locator("input, select").first
                else:
                    candidate = row_cell.first

            elif strategy.kind == LocatorStrategyKind.ROLE_NAME:
                for role in ("button", "link"):
                    try:
                        c = page.get_by_role(role, name=strategy.value)
                        if c.count() > 0:
                            candidate = c.first
                            break
                    except Exception:
                        continue

            elif strategy.kind == LocatorStrategyKind.TEXT_EXACT:
                candidate = page.get_by_text(strategy.value, exact=True).first

            elif strategy.kind == LocatorStrategyKind.CSS_FALLBACK:
                candidate = page.locator(strategy.value).first

            elif strategy.kind == LocatorStrategyKind.XPATH_FALLBACK:
                candidate = page.locator(f"xpath={strategy.value}").first

            if candidate is None:
                attempts.append(f"{strategy.kind.value}={strategy.value!r}: no match")
                continue

            candidate.wait_for(state="attached", timeout=2000)
            return candidate, strategy

        except Exception as e:
            attempts.append(f"{strategy.kind.value}={strategy.value!r}: {e}")
            continue

    raise HardFailure(
        step_id=step_id,
        expected=f"one of the locator strategies for '{locator.description}' to resolve",
        observed="; ".join(attempts) if attempts else "no strategies defined",
    )
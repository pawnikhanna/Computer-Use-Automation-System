from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from artifacts.schema import Capability, ActionType, RiskLevel, ReplayStatus, ReplayResult
from guardrails.policy import default_policy, PolicyViolation, redact
from replay.errors import HardFailure, BusinessOutcomeMatch, RequiresEscalation
from replay.locators import resolve
from replay.outcome_matching import match_business_outcome
from escalation.handoff import InterventionRequest, escalate_and_wait, write_control_state
import recoverable
from dotenv import load_dotenv
load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
CDP_DEBUG_PORT = 9222


def _page_body_text(page) -> str:
    try:
        return page.inner_text("body")[:3000]
    except Exception:
        return ""


def _settle(page, first_timeout=4000, retry_timeout=9000, step_id="", log=None):
    """Wait for the page to finish loading. Legacy apps sometimes respond
    slowly (a real, legitimate runtime condition -- see mock_app's
    SLOW_MEMBER_ID) -- treat one slow load as RECOVERABLE by retrying with a
    longer timeout before treating it as a hard failure."""
    try:
        page.wait_for_load_state("networkidle", timeout=first_timeout)
    except PWTimeoutError:
        if log is not None:
            log({"event": "slow_load_detected", "step_id": step_id, "retrying_with_ms": retry_timeout})
        try:
            page.wait_for_load_state("networkidle", timeout=retry_timeout)
        except PWTimeoutError:
            raise HardFailure(step_id, "page to finish loading", "load timed out twice (transient-slowness retry exhausted)")


def _run_extract(page, step, outputs: dict, locator_log: list, log) -> None:
    locator, strategy = resolve(page, step.target, purpose="text", step_id=step.step_id)
    locator_log.append({"step_id": step.step_id, "strategy_used": strategy.kind.value,
                         "target": step.target.description})
    raw_text = locator.inner_text()
    value = raw_text
    if step.extract_pattern:
        m = re.search(step.extract_pattern, raw_text)
        value = m.group(1) if m else raw_text
    outputs[step.extract_as] = value.strip()
    log({"event": "extract", "step_id": step.step_id, "output_name": step.extract_as, "value": redact(value)})


def _dismiss_all_recoverable(page, conditions, log, max_rounds=3):
    for _ in range(max_rounds):
        body = _page_body_text(page)
        dismissed = recoverable.try_dismiss(page, conditions, body)
        if not dismissed:
            return
        log({"event": "recoverable_condition_dismissed", "condition": dismissed})
        _settle(page, log=log)


def replay_capability(capability: Capability, params: dict, allow_risky_actions: bool = False) -> ReplayResult:
    run_id = f"replay_{capability.capability_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    evidence_dir = REPO_ROOT / "evidence" / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)

    policy = default_policy()
    transcript = []
    locator_log = []
    outputs = {}

    def log(event: dict):
        event["ts"] = datetime.now(timezone.utc).isoformat()
        transcript.append(event)

    # --- Validate inputs against the artifact's declared contract ---
    for p in capability.input_params:
        if p.required and p.name not in params:
            raise ValueError(f"Missing required parameter: {p.name}")

    result = None
    last_status_code = None
    human_confirmed_steps: set[str] = set()
    write_control_state(evidence_dir, "agent", detail="replay started")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[f"--remote-debugging-port={CDP_DEBUG_PORT}", "--remote-debugging-address=0.0.0.0"],
        )
        page = browser.new_page()

        human_completed_early = False
        human_completed_notes = None
        try:
            i = 0
            while i < len(capability.steps):
                step = capability.steps[i]
                _dismiss_all_recoverable(page, capability.recoverable_conditions, log)

                if step.action == ActionType.NAVIGATE:
                    policy.check_navigation(step.url)
                    resp = page.goto(capability.target_base_url + step.url)
                    last_status_code = resp.status if resp else last_status_code
                    _settle(page, step_id=step.step_id, log=log)
                    log({"event": "navigate", "step_id": step.step_id, "path": step.url,
                         "status_code": last_status_code})

                elif step.action in (ActionType.FILL, ActionType.SELECT):
                    value = params.get(step.input_binding) if step.input_binding else step.literal_value
                    if value is None:
                        raise HardFailure(step.step_id, f"a value for '{step.description}'", "none provided")
                    locator, strategy = resolve(page, step.target, purpose="control", step_id=step.step_id)
                    locator_log.append({"step_id": step.step_id, "strategy_used": strategy.kind.value,
                                         "target": step.target.description})
                    if step.action == ActionType.FILL:
                        locator.fill(str(value))
                    else:
                        locator.select_option(str(value))
                    log({"event": step.action.value, "step_id": step.step_id,
                         "target": step.target.description, "value": redact(str(value))})

                elif step.action == ActionType.CLICK:
                    if (step.risk_level == RiskLevel.RISKY_IRREVERSIBLE
                            and not allow_risky_actions
                            and step.step_id not in human_confirmed_steps):
                        # Escalate INLINE (not via exception) so we can decide,
                        # right here, whether to retry/continue/abandon based
                        # on what the human tells us.
                        shot_path = evidence_dir / f"escalation_{step.step_id}.png"
                        page.screenshot(path=str(shot_path), full_page=True)
                        request = InterventionRequest(
                            run_id=evidence_dir.name, capability_id=capability.capability_id,
                            step_id=step.step_id,
                            reason=f"Risky/irreversible action requires confirmation: '{step.target.description}'",
                            page_url=page.url, screenshot_path=str(shot_path),
                            cdp_inspect_url=f"http://127.0.0.1:{CDP_DEBUG_PORT}",
                        )
                        log({"event": "escalation_required", "step_id": step.step_id, "reason": request.reason})
                        outcome = escalate_and_wait(request, evidence_dir)
                        log({"event": "escalation_outcome", "action": outcome.action,
                             "notes": redact(outcome.operator_notes)})

                        if outcome.action == "retry":
                            human_confirmed_steps.add(step.step_id)
                            continue  # re-run THIS step now that a human confirmed it
                        elif outcome.action == "human_completed":
                            human_completed_early = True
                            human_completed_notes = outcome.operator_notes
                            break  # fall through to catch-up extracts + checkpoint check
                        else:
                            raise RequiresEscalation(step.step_id, "Operator abandoned the run.",
                                                      operator_notes=outcome.operator_notes)

                    locator, strategy = resolve(page, step.target, purpose="clickable", step_id=step.step_id)
                    locator_log.append({"step_id": step.step_id, "strategy_used": strategy.kind.value,
                                         "target": step.target.description})
                    try:
                        with page.expect_navigation(wait_until="networkidle", timeout=9000):
                            locator.click()
                    except PWTimeoutError:
                        pass  # not every click necessarily navigates; proceed
                    log({"event": "click", "step_id": step.step_id, "target": step.target.description})

                elif step.action == ActionType.EXTRACT:
                    _run_extract(page, step, outputs, locator_log, log)

                # After any state-changing step, check for a business outcome
                # before continuing -- no point running the remaining steps
                # against a page the flow never expected to reach.
                body = _page_body_text(page)
                outcome = match_business_outcome(body, last_status_code, page.url, capability.business_outcomes)
                if outcome:
                    raise BusinessOutcomeMatch(outcome)

                i += 1

            # If a human said they completed the rest manually, we still run
            # any not-yet-executed EXTRACT steps ourselves (the human doesn't
            # need to relay data back verbally -- we just read it off the page
            # they left us in), then fall through to the same checkpoint check
            # as the normal path. "The human says so" is never taken on faith
            # alone -- the checkpoint is still verified below either way.
            if human_completed_early:
                for remaining in capability.steps[i + 1:]:
                    if remaining.action == ActionType.EXTRACT:
                        _run_extract(page, remaining, outputs, locator_log, log)

            # --- All steps done (or human took over): verify the success checkpoint ---
            body = _page_body_text(page)
            cp = capability.success_checkpoint
            checkpoint_ok = (
                (cp.kind == "text_present" and cp.value in body)
                or (cp.kind == "url_contains" and cp.value in page.url)
                or (cp.kind == "element_visible" and page.get_by_text(cp.value).first.is_visible())
            )
            if not checkpoint_ok:
                raise HardFailure(
                    "checkpoint", cp.value,
                    redact(body)[:300] + (" [after human-reported manual completion]" if human_completed_early else ""),
                )

            page.screenshot(path=str(evidence_dir / "success_final_state.png"), full_page=True)
            result = ReplayResult(
                status=ReplayStatus.SUCCESS, capability_id=capability.capability_id,
                outputs=outputs, evidence_dir=str(evidence_dir), locator_resolution_log=locator_log,
                escalation_notes=human_completed_notes if human_completed_early else None,
            )

        except BusinessOutcomeMatch as e:
            page.screenshot(path=str(evidence_dir / "business_outcome_state.png"), full_page=True)
            log({"event": "business_outcome", "name": e.outcome.name})
            result = ReplayResult(
                status=ReplayStatus.BUSINESS_OUTCOME, capability_id=capability.capability_id,
                business_outcome_name=e.outcome.name, outputs=e.outcome.result_shape,
                evidence_dir=str(evidence_dir), locator_resolution_log=locator_log,
            )

        except RequiresEscalation as e:
            page.screenshot(path=str(evidence_dir / "escalation_state.png"), full_page=True)
            log({"event": "escalation_abandoned", "step_id": e.step_id, "reason": e.reason,
                 "notes": redact(e.operator_notes)})
            result = ReplayResult(
                status=ReplayStatus.ESCALATED, capability_id=capability.capability_id,
                failure_step_id=e.step_id, failure_expected=e.reason,
                evidence_dir=str(evidence_dir), locator_resolution_log=locator_log,
                escalation_notes=e.operator_notes or None,
            )

        except HardFailure as e:
            page.screenshot(path=str(evidence_dir / "failure_state.png"), full_page=True)
            log({"event": "hard_failure", "step_id": e.step_id, "expected": e.expected, "observed": e.observed})
            result = ReplayResult(
                status=ReplayStatus.HARD_FAILURE, capability_id=capability.capability_id,
                failure_step_id=e.step_id, failure_expected=e.expected, failure_observed=redact(e.observed),
                evidence_dir=str(evidence_dir), locator_resolution_log=locator_log,
                escalation_notes=human_completed_notes if human_completed_early else None,
            )

        except Exception as e:
            try:
                page.screenshot(path=str(evidence_dir / "unexpected_failure_state.png"), full_page=True)
            except Exception:
                pass
            log({"event": "unexpected_error", "error": str(e)})
            result = ReplayResult(
                status=ReplayStatus.HARD_FAILURE, capability_id=capability.capability_id,
                failure_step_id="unknown", failure_expected="no unexpected exception",
                failure_observed=redact(str(e))[:300],
                evidence_dir=str(evidence_dir), locator_resolution_log=locator_log,
            )

        finally:
            browser.close()

    (evidence_dir / "replay_transcript.json").write_text(json.dumps(transcript, indent=2))
    (evidence_dir / "replay_result.json").write_text(result.model_dump_json(indent=2))
    return result


def load_capability(capability_id: str) -> Capability:
    path = REPO_ROOT / "artifacts" / "store" / f"{capability_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No saved artifact for capability_id={capability_id!r} at {path}")
    return Capability.model_validate_json(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capability-id", required=True)
    parser.add_argument("--param", action="append", default=[],
                         help="key=value, repeatable, e.g. --param member_id=12345")
    parser.add_argument("--allow-risky-actions", action="store_true")
    args = parser.parse_args()

    params = dict(kv.split("=", 1) for kv in args.param)
    capability = load_capability(args.capability_id)
    result = replay_capability(capability, params, allow_risky_actions=args.allow_risky_actions)

    print(f"Status: {result.status.value}")
    if result.status == ReplayStatus.SUCCESS:
        print(f"Outputs: {result.outputs}")
    elif result.status == ReplayStatus.BUSINESS_OUTCOME:
        print(f"Business outcome: {result.business_outcome_name} -> {result.outputs}")
    else:
        print(f"Failure at step {result.failure_step_id}: expected {result.failure_expected!r}, "
              f"observed {result.failure_observed!r}")
    print(f"Evidence: {result.evidence_dir}")


if __name__ == "__main__":
    main()
from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from playwright.sync_api import sync_playwright

from agent.dom_extract import extract_observation
from agent.prompts import build_system_prompt, TOOLS
from artifacts.schema import (
    Capability, ArtifactMeta, ParameterSpec, ParamType, OutputSpec,
    Step, ActionType, RiskLevel, Locator, LocatorStrategy, LocatorStrategyKind,
    BusinessOutcome, Checkpoint,
)
from guardrails.policy import default_policy, PolicyViolation, redact
import recoverable
from dotenv import load_dotenv
load_dotenv()
import re

BASE_URL = "http://127.0.0.1:5055"
MODEL = os.environ.get("CUA_DISCOVERY_MODEL", "claude-haiku-4-5-20251001")
MAX_STEPS = 20

REPO_ROOT = Path(__file__).resolve().parent.parent

def _sanitize_checkpoint_text(raw: str) -> str:
    """Discovery instructs the model to use STATIC checkpoint text, but LLMs
    sometimes include a dynamic value anyway (e.g. an auto-incrementing
    account number). Rather than trust the model's judgment blindly, walk
    the text clause by clause (split on sentence boundaries) and return the
    first one that's either already static, or becomes static once a
    trailing "Label: <value>" suffix is stripped. We deliberately return a
    SINGLE clean fragment, not a patched-together string -- checkpoint
    verification is a plain substring match against the live page, so a
    reconstructed multi-clause string would rarely match the page's actual
    whitespace/line-break layout."""
    dollar = re.compile(r"\$\s?\d[\d,]*\.\d{2}")
    longnum = re.compile(r"\b\d{6,}\b")

    def has_dynamic(s: str) -> bool:
        return bool(dollar.search(s) or longnum.search(s))

    clauses = re.split(r"(?<=[.!?])\s+", raw.strip())
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        if not has_dynamic(clause):
            return clause.rstrip(".").strip()
        stripped = re.sub(
            r"\s*:\s*(?:\$\s?\d[\d,]*\.\d{2}|\d{6,})\s*[.!?]?\s*$", "", clause
        ).strip()
        if stripped and not has_dynamic(stripped):
            return stripped

    return raw  # nothing static survived; fall back rather than return empty

def _locator_for(el: dict, prefer: LocatorStrategyKind) -> Locator:
    """Build a multi-strategy Locator from a dom_extract element record."""
    strategies = []
    label = el.get("label_text")
    text = el.get("text")
    css_path = el.get("css_path")

    if prefer == LocatorStrategyKind.LABEL_TEXT and label:
        strategies.append(LocatorStrategy(
            kind=LocatorStrategyKind.LABEL_TEXT, value=label,
            robustness_note="Legacy table-row layout convention: control sits in the "
                             "cell following the cell containing this label text.",
        ))
    if prefer == LocatorStrategyKind.ROLE_NAME and text:
        strategies.append(LocatorStrategy(
            kind=LocatorStrategyKind.ROLE_NAME, value=text,
            robustness_note="Accessible name is stable even if visual styling changes.",
        ))
        strategies.append(LocatorStrategy(
            kind=LocatorStrategyKind.TEXT_EXACT, value=text,
            robustness_note="Fallback: exact visible text match.",
        ))
    if css_path:
        strategies.append(LocatorStrategy(
            kind=LocatorStrategyKind.CSS_FALLBACK, value=css_path,
            robustness_note="Structural fallback only -- brittle to layout changes, "
                             "last resort if semantic strategies fail.",
        ))
    return Locator(description=label or text or css_path or "unlabeled element", strategies=strategies)


def run_discovery(capability_id: str, goal: str, entry_path: str, evidence_dir: Path) -> Capability:
    run_id = f"{capability_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    run_evidence_dir = evidence_dir / run_id
    run_evidence_dir.mkdir(parents=True, exist_ok=True)

    policy = default_policy()
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    transcript_log = []          # redacted, human-readable log for /evidence
    steps: list[Step] = []
    input_params: dict[str, ParameterSpec] = {}
    outputs: list[OutputSpec] = []

    def log(event: dict):
        event["ts"] = datetime.now(timezone.utc).isoformat()
        transcript_log.append(event)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()

        policy.check_navigation(entry_path)
        page.goto(BASE_URL + entry_path)
        steps.append(Step(step_id="s0", action=ActionType.NAVIGATE, url=entry_path,
                           description=f"Navigate to entry point {entry_path}"))
        log({"event": "navigate", "path": entry_path})

        messages = []
        system_prompt = build_system_prompt(goal)
        success = None
        checkpoint = None
        step_counter = 1

        while step_counter <= MAX_STEPS:
            # --- Observe, and handle any recoverable condition BEFORE the LLM sees it ---
            obs = extract_observation(page)
            dismissed = recoverable.try_dismiss(page, recoverable.KNOWN_CONDITIONS, obs["body_text"])
            if dismissed:
                log({"event": "recoverable_condition_dismissed", "condition": dismissed})
                obs = extract_observation(page)  # re-observe after dismissal

            log({"event": "observation", "url": obs["url"],
                 "body_text": redact(obs["body_text"])[:500],
                 "n_elements": len(obs["elements"])})

            user_content = [{
                "type": "text",
                "text": f"Current URL: {obs['url']}\n\nVisible elements/fields (JSON):\n"
                        f"{json.dumps(obs['elements'], indent=2)}\n\n"
                        f"Visible page text:\n{obs['body_text']}",
            }]
            messages.append({"role": "user", "content": user_content})

            response = client.messages.create(
                model=MODEL, max_tokens=1024, system=system_prompt,
                tools=TOOLS, messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_use = next((b for b in response.content if b.type == "tool_use"), None)
            if tool_use is None:
                log({"event": "no_tool_call", "raw": str(response.content)})
                break

            name, tool_input, tool_id = tool_use.name, tool_use.input, tool_use.id
            log({"event": "tool_call", "tool": name, "input": redact(json.dumps(tool_input))})

            step_id = f"s{step_counter}"
            tool_result_content = None

            try:
                policy.check_action_type(name if name != "finish" else "navigate")

                if name == "navigate":
                    path = tool_input["path"]
                    policy.check_navigation(path)
                    page.goto(BASE_URL + path)
                    steps.append(Step(step_id=step_id, action=ActionType.NAVIGATE, url=path,
                                       description=f"Navigate to {path}"))
                    tool_result_content = "navigated"

                elif name in ("fill", "select"):
                    el = obs["elements"][tool_input["element_id"]]
                    param_name = tool_input["param_name"]
                    value = tool_input["value"]
                    locator_kind = LocatorStrategyKind.LABEL_TEXT
                    loc = _locator_for(el, locator_kind)
                    action_type = ActionType.FILL if name == "fill" else ActionType.SELECT

                    playwright_locator = page.locator(f'[data-cua-tmp-id="{el["element_id"]}"]')
                    if name == "fill":
                        playwright_locator.fill(value)
                    else:
                        playwright_locator.select_option(value)

                    steps.append(Step(step_id=step_id, action=action_type, target=loc,
                                       input_binding=param_name,
                                       description=f"{name} '{loc.description}' from parameter {param_name}"))
                    input_params[param_name] = ParameterSpec(
                        name=param_name, type=ParamType.STRING, required=True,
                        description=f"Value for control labeled '{loc.description}'",
                        example=redact(value),
                    )
                    tool_result_content = f"{name} succeeded"

                elif name == "click":
                    el = obs["elements"][tool_input["element_id"]]
                    text = el.get("text") or el.get("label_text")
                    loc = _locator_for(el, LocatorStrategyKind.ROLE_NAME)
                    risk = RiskLevel.RISKY_IRREVERSIBLE if policy.is_risky(text) else RiskLevel.SAFE_REVERSIBLE

                    playwright_locator = page.locator(f'[data-cua-tmp-id="{el["element_id"]}"]')
                    playwright_locator.click()

                    steps.append(Step(step_id=step_id, action=ActionType.CLICK, target=loc,
                                       risk_level=risk, description=f"Click '{loc.description}'"))
                    tool_result_content = "click succeeded"

                elif name == "extract":
                    el = obs["elements"][tool_input["element_id"]]
                    output_name = tool_input["output_name"]
                    loc = _locator_for(el, LocatorStrategyKind.LABEL_TEXT)
                    steps.append(Step(step_id=step_id, action=ActionType.EXTRACT, target=loc,
                                       extract_as=output_name,
                                       description=f"Extract '{loc.description}' as {output_name}"))
                    outputs.append(OutputSpec(name=output_name, type=ParamType.STRING,
                                               description=f"Value labeled '{loc.description}'"))
                    tool_result_content = f"extracted (redacted): {redact(el.get('text') or '')}"

                elif name == "finish":
                    success = tool_input["success"]
                    if success:
                        raw_checkpoint = tool_input.get("checkpoint_text", "")
                        sanitized = _sanitize_checkpoint_text(raw_checkpoint)
                        if sanitized and sanitized in obs["body_text"]:
                            checkpoint_value = sanitized
                        else:
                            last_extract = next((s for s in reversed(steps) if s.action == ActionType.EXTRACT), None)
                            if last_extract and last_extract.target.description in obs["body_text"]:
                                checkpoint_value = last_extract.target.description
                                log({"event": "checkpoint_fallback_used",
                                     "original": redact(raw_checkpoint), "fallback": checkpoint_value})
                            else:
                                checkpoint_value = sanitized or raw_checkpoint
                                log({"event": "checkpoint_verification_failed", "value": redact(checkpoint_value)})
                        checkpoint = Checkpoint(kind="text_present", value=checkpoint_value)
                    log({"event": "finish", "success": success, "reason": tool_input.get("reason")})
                    break

            except (PolicyViolation, Exception) as e:
                tool_result_content = f"ERROR: {e}"
                log({"event": "action_error", "tool": name, "error": str(e)})

            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": tool_result_content}],
            })
            step_counter += 1

        try:
            page.screenshot(path=str(run_evidence_dir / "final_state.png"), full_page=True)
        except Exception:
            pass
        browser.close()

    (run_evidence_dir / "discovery_transcript.json").write_text(json.dumps(transcript_log, indent=2))

    if not success:
        raise RuntimeError(f"Discovery run did not succeed; see {run_evidence_dir}/discovery_transcript.json")

    capability = Capability(
        capability_id=capability_id,
        name="Look up member and read savings balance",
        description="Logs into the admin console, looks up a member by ID, and reads their "
                    "current savings balance.",
        meta=ArtifactMeta(discovered_by_model=MODEL, discovery_run_id=run_id),
        target_base_url=BASE_URL,
        entry_path=entry_path,
        requires_auth=True,
        input_params=list(input_params.values()),
        outputs=outputs,
        recoverable_conditions=recoverable.KNOWN_CONDITIONS,
        steps=steps,
        business_outcomes=[
            BusinessOutcome(
                name="member_not_found", detect_text="No member found matching ID",
                result_shape={"found": False},
            ),
            BusinessOutcome(
                name="member_access_denied", detect_status_code=403,
                result_shape={"found": True, "access_denied": True},
            ),
        ],
        success_checkpoint=checkpoint,
    )

    store_path = REPO_ROOT / "artifacts" / "store" / f"{capability_id}.json"
    store_path.write_text(capability.model_dump_json(indent=2))
    (run_evidence_dir / "artifact_snapshot.json").write_text(capability.model_dump_json(indent=2))
    print(f"Discovery succeeded. Artifact saved to {store_path}")
    print(f"Evidence saved to {run_evidence_dir}")
    return capability


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--member-id", default="12345")
    parser.add_argument("--capability-id", default=None)
    parser.add_argument("--flow", choices=["lookup_balance", "open_subaccount"], default="lookup_balance")
    parser.add_argument("--account-type", default="SAVINGS")
    parser.add_argument("--initial-deposit", default="100")
    args = parser.parse_args()

    if args.flow == "lookup_balance":
        capability_id = args.capability_id or "lookup_member_savings_balance"
        goal = (
            f"Log in to the admin console using operator credentials (username 'operator', "
            f"password 'demo123' -- treat both as reusable parameters). Then go to Member Lookup, "
            f"search for member ID {args.member_id} (treat the member ID as a reusable parameter), "
            f"open their record, and extract their savings balance as output 'savings_balance'."
        )
    else:  # open_subaccount
        capability_id = args.capability_id or "open_subaccount_for_member"
        goal = (
            f"Log in to the admin console using operator credentials (username 'operator', "
            f"password 'demo123' -- treat both as reusable parameters). Then go to Member Lookup, "
            f"search for member ID {args.member_id} (treat as a reusable parameter), open their record, "
            f"then open a sub-account for them: choose account type {args.account_type} (treat as reusable "
            f"parameter 'account_type'), enter an initial deposit of {args.initial_deposit} (treat as "
            f"reusable parameter 'initial_deposit'), continue to the review/confirmation screen, and click "
            f"'Confirm and Open Account' to complete it. Extract the confirmation message's account number "
            f"as output 'new_account_number'."
        )

    run_discovery(
        capability_id=capability_id,
        goal=goal,
        entry_path="/login",
        evidence_dir=REPO_ROOT / "evidence",
    )


if __name__ == "__main__":
    main()
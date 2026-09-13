"""
escalation/handoff.py
========================

Human-in-the-loop escalation and control-transfer.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class InterventionRequest(BaseModel):
    run_id: str
    capability_id: str
    step_id: str
    reason: str
    page_url: str
    screenshot_path: str
    cdp_inspect_url: str
    created_at: str = ""


class InterventionOutcome(BaseModel):
    action: str  # "retry" | "human_completed" | "abandon"
    operator_notes: str = ""


def write_control_state(evidence_dir: Path, controller: str, detail: str = ""):
    """Single source of truth for 'who is in control right now' -- readable
    by anything that wants to check without parsing the whole transcript."""
    state = {
        "controller": controller,  # "agent" | "human"
        "detail": detail,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (evidence_dir / "control_state.json").write_text(json.dumps(state, indent=2))


def escalate_and_wait(request: InterventionRequest, evidence_dir: Path) -> InterventionOutcome:
    """Block for a human decision. This is the swappable seam: in production
    this would notify an operator queue and await a real console's response;
    here it's a CLI prompt, but the request/outcome contract either side
    talks through is the same either way."""
    request.created_at = datetime.now(timezone.utc).isoformat()
    (evidence_dir / "intervention_request.json").write_text(request.model_dump_json(indent=2))
    write_control_state(evidence_dir, "human", detail=f"escalated at step {request.step_id}: {request.reason}")

    print("\n" + "=" * 70)
    print("HUMAN INTERVENTION REQUESTED")
    print("=" * 70)
    print(f"Capability : {request.capability_id}")
    print(f"Step       : {request.step_id}")
    print(f"Reason     : {request.reason}")
    print(f"Screenshot : {request.screenshot_path}")
    print(f"Live session (Chrome DevTools Protocol): open this in your own Chrome:")
    print(f"    {request.cdp_inspect_url}")
    print("You will see a list of inspectable targets -- click the one showing this page")
    print("to get a live, interactive DevTools view of the SAME browser tab the automation")
    print("was driving. You can inspect and interact with it directly from there.")
    print("=" * 70)

    print("\nWhat would you like to do?")
    print("  [r] Retry the step now (you've confirmed it should proceed)")
    print("  [c] I completed the rest of the flow manually in the live session")
    print("  [a] Abandon this run")
    choice = input("> ").strip().lower()
    notes = input("Briefly describe what you did (for the evidence record): ").strip()

    action = {"r": "retry", "c": "human_completed", "a": "abandon"}.get(choice, "abandon")
    outcome = InterventionOutcome(action=action, operator_notes=notes)

    (evidence_dir / "intervention_outcome.json").write_text(outcome.model_dump_json(indent=2))
    write_control_state(evidence_dir, "agent", detail=f"resumed after human action: {action}")
    return outcome
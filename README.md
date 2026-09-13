# Computer-Use Automation System

A small but complete implementation of interface.ai's take-home: an LLM-driven
discovery agent that learns a UI flow once, records it as a typed reusable
artifact, and a deterministic replay engine that executes that artifact
without any model in the loop — plus guardrails and a human escalation
mechanism.

## What's here

- `mock_app/` — the target: a deliberately legacy-styled Flask bank
  back-office console (table layouts, no test IDs, injected exceptional
  states).
- `artifacts/schema.py` — the typed Capability/Step/Locator contract.
- `agent/` — the discovery loop: Claude (tool-calling) drives a real
  Playwright browser and produces a saved artifact.
- `replay/` — the deterministic replay engine: executes a saved artifact,
  no LLM involved.
- `guardrails/policy.py` — allowlist enforcement, risk classification,
  redaction. Shared by both discovery and replay.
- `recoverable.py` — shared handling for a randomly-appearing interstitial
  notice, identical in both discovery and replay.
- `escalation/handoff.py` — human-in-the-loop escalation: pauses replay on a
  risky/irreversible step and exposes the live browser session via Chrome
  DevTools Protocol for manual takeover.
- `evidence/` — discovery and replay run logs land here automatically.
- `tests/` — unit tests for everything that doesn't require a live browser
  (guardrails, recoverable-condition matching, artifact schema round-trips,
  business-outcome matching, escalation control-transfer).

See `REPORT.md` for the full design write-up.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# then edit .env and paste in your own ANTHROPIC_API_KEY
```

You'll need your own Anthropic API key (console.anthropic.com, $5 minimum
prepaid credit). A full discovery run costs a few cents.

## Running it — the demo path

**Terminal 1** — start the target app and leave it running:
```bash
python mock_app/app.py
```

**Terminal 2** — run discovery (the genuine LLM-driven run), then replay
deterministically:

```bash
# Discovery: a safe, read-only capability
python -m agent.discovery --flow lookup_balance --member-id 12345

# Replay it — happy path
python -m replay.engine --capability-id lookup_member_savings_balance \
  --param operator_username=operator --param operator_password=demo123 \
  --param member_id=12345

# Replay it against a business outcome (member doesn't exist) --
# NOT an error, a legitimate typed result:
python -m replay.engine --capability-id lookup_member_savings_balance \
  --param operator_username=operator --param operator_password=demo123 \
  --param member_id=00000
```

```bash
# Discovery: a capability with a genuine risky/irreversible step
python -m agent.discovery --flow open_subaccount --member-id 12345

# Replay it -- this pauses for human confirmation before the final
# irreversible click, and prints a live Chrome DevTools Protocol URL you can
# open in your own browser to inspect/interact with the exact paused session.
python -m replay.engine --capability-id open_subaccount_for_member \
  --param operator_username=operator --param operator_password=demo123 \
  --param member_id=12345 --param account_type=SAVINGS --param initial_deposit=100
```

## Running without live services

Every unit test runs with no browser, no live app, and no API key:
```bash
python -m pytest tests/ -v
```

## Known constraints

- Discovery requires a real Anthropic API key and a live browser; it cannot
  be run offline or mocked (by design — see the brief's Section 4).
- The mock app's session timeout is set generously (10 minutes) specifically
  so a human has time to respond during a live escalation demo; a
  production system would handle mid-flow session expiry via explicit
  re-authentication (see REPORT.md, Section 7 — Cuts).
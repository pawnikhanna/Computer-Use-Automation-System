# Computer-Use Automation System

This is my submission for the interface.ai take-home. It's a system that:

1. Uses an LLM to figure out how to do a task in a web app by actually clicking
   around in a browser (I call this "discovery").
2. Saves what it learned as a reusable recipe (I call this an "artifact").
3. Replays that recipe later without using the LLM at all — just fast, predictable
   browser automation.
4. Knows when to stop and ask a human for help instead of guessing.

## Why it's built this way

Real banks have old internal tools with no clean HTML to hook into. So instead of
using a real bank (I obviously don't have access to one), I built a small fake one
that's deliberately old and ugly — table layouts, no IDs on anything, the kind of
thing you'd actually find at a credit union in production.

## What's in each folder

- `mock_app/` — the fake bank app I'm automating against.
- `agent/` — the part that uses Claude to figure out a task the first time.
- `artifacts/` — the format I save a learned task in, plus the saved files themselves.
- `replay/` — runs a saved task again, deterministically, no LLM involved.
- `guardrails/` — the safety rules (what's allowed, what's risky, what gets redacted
  from logs).
- `recoverable.py` — handles a popup notice that randomly shows up on some pages.
- `escalation/` — what happens when the system needs a human to step in.
- `evidence/` — logs and screenshots from every run I did.
- `tests/` — automated tests for everything that doesn't need a live browser.

Full explanation of my decisions is in REPORT.md.

## How to set it up

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Then open `.env` and paste in your own Anthropic API key. You'll need one — it costs
about $5 minimum to get set up, but a single run only costs a few cents.

## How to actually run it

**Terminal 1** — start the fake bank app and leave it running:
```bash
python mock_app/app.py
```

**Terminal 2** — everything else:

```bash
# Have the agent learn how to look up a member and read their balance
python -m agent.discovery --flow lookup_balance --member-id 12345

# Replay that, same member — should succeed
python -m replay.engine --capability-id lookup_member_savings_balance \
  --param operator_username=operator --param operator_password=demo123 --param member_id=12345

# Replay it with a member ID that doesn't exist — this should NOT crash,
# it should come back as a normal "not found" result
python -m replay.engine --capability-id lookup_member_savings_balance \
  --param operator_username=operator --param operator_password=demo123 --param member_id=00000

# Have the agent learn a riskier task: opening a new sub-account
python -m agent.discovery --flow open_subaccount --member-id 12345

# Replay it — this one pauses partway through and asks a human to confirm
# before it clicks the final "create account" button
python -m replay.engine --capability-id open_subaccount_for_member \
  --param operator_username=operator --param operator_password=demo123 \
  --param member_id=12345 --param account_type=SAVINGS --param initial_deposit=100
```

## Running without any of that

The tests don't need the browser, the app, or an API key:
```bash
python -m pytest tests/ -v
```

## Things I know are missing

- If a session times out while a human is being asked to confirm something, replay
  doesn't automatically log back in and keep going — it just fails. I ran into this
  for real while testing and worked around it by making the timeout longer. Doing
  this properly (without risking accidentally doing something twice) needs more
  thought than I had time for. Details in REPORT.md.
- I only built one target app, so multi-tenant support and non-web (desktop) apps
  are designed for but not actually built.
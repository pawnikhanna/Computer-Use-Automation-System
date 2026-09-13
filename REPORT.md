# Design Report

## 1. Architecture

I split this into four pieces that don't depend on each other's internals:

- **Discovery** — Claude drives a real browser, one action at a time. I don't show it
  a screenshot or raw HTML; instead I pull out a simple numbered list of the buttons,
  inputs, and readable fields on the page each turn, and it picks one. Raw HTML on an
  old app is mostly noise, and I didn't want to rely on pixel coordinates since those
  break the moment anything on the page shifts.
- **Artifacts** — the saved "recipe" from a successful discovery run. Just a JSON file
  following a schema I defined.
- **Replay** — runs a saved artifact step by step. No LLM call happens anywhere in
  this code.
- **Guardrails + recoverable-condition handling** — shared code used by both discovery
  and replay, so the same rules apply no matter which one is driving.

I kept it as one simple program you run from the command line, not a bunch of
services talking to each other. The brief said not to over-engineer for scale I
haven't actually built, and honestly a single process is just easier to reason about
and to explain.

## 2. Artifact schema

Each saved capability has:
- a list of named, typed **inputs** (like a member ID)
- a list of named, typed **outputs** (like a balance)
- an ordered list of **steps**
- a **checkpoint** — some way to confirm the task actually finished, not just assume
  the last click worked
- a list of **known non-error outcomes** (like "member not found") that are legitimate
  results, not crashes
- a list of **known popups/notices** that might show up and need dismissing

For each step, instead of one selector, I store a small list of ways to find that
element — first try matching by its label text, then by its accessible name, then
fall back to a structural CSS path if nothing else works. There are no test IDs
anywhere in this app, so I needed something more resilient than a single selector,
and logging which one actually worked each time gives me a signal if something
starts drifting later.

Each step also has a risk level (safe vs. risky-and-irreversible), because one
capability can be mostly harmless reads with a single dangerous click at the end —
the guardrails need to know exactly which step that is, not just flag the whole thing.

One real thing I ran into: at one point the discovery agent said it succeeded and
gave me a "checkpoint" that was really just it summarizing what it thought happened,
not actual text from the page. It had quietly failed to read a value and papered over
it. So now I check that the checkpoint text actually exists on the real page before
trusting it, and if it doesn't, I fall back to the label of the last thing it actually
managed to read. I don't just trust the model's word that it succeeded.

## 3. Determinism and error handling

Replay only ever does exactly what was recorded — no improvising. When it finishes,
it reports one of three things:

- **Success** — checkpoint confirmed, here's the data you asked for.
- **A known outcome, not an error** — like "no member with that ID." I tested this
  for real: looking up a member ID that doesn't exist comes back as
  `{'found': False}`, not a crash.
- **A real failure** — something it expected to find wasn't there. I log which step,
  what it expected, what it actually saw, and a screenshot.

The popup notice that randomly appears is handled the exact same way in discovery
and in replay — checked before every step, dismissed if present. I made sure of this
on purpose, because if the two handled it differently that's exactly the kind of bug
that's easy to miss.

There's also a member ID in the fake app that's rigged to load slowly on purpose. If
a page takes too long, replay waits longer once before giving up, instead of failing
immediately on the first slow load.

The one thing I didn't build: recovering from a session timing out mid-task. More on
that in Cuts.

## 4. Heterogeneity and multi-tenant

I only built against one web app, but here's how I'd extend this:

**Different types of apps.** The part that finds elements on the page (the locator
strategies) is separate from the part that describes the task (the steps). To support
a desktop app instead of a web page, I'd swap out how elements are found — using
accessibility APIs instead of DOM label matching — but the actual saved recipe format
wouldn't need to change at all.

**Multiple banks using the same software.** I left a field on the artifact for
tenant-specific overrides that isn't used yet. The idea: record the flow once against
one bank's version of the app, and if a different bank's copy of the same software
has small differences (different button wording, say), you only need to override that
one thing instead of re-recording the whole flow. And since replay already logs which
locator strategy worked each time, if a bank's copy starts consistently falling back
to the weakest (CSS) strategy, that's a sign their version has drifted and needs a
fresh look.

## 5. Escalation and handoff

When replay hits a step marked risky (like the final "confirm and open account"
click), it stops and asks a human instead of just doing it.

Here's the part I actually made real, not just described: the browser it's running
is started with a debugging port open, so a human can open that same address in their
own Chrome and see — and interact with — the exact same browser tab the automation
was using. Not a screenshot, not a new session. I tested this myself: it genuinely
paused, I opened the link, and typing my response resumed it.

The actual "ask a human" interface is just a command-line prompt — I kept that
simple on purpose, since the brief said a full operator UI wasn't the point. What
matters is that a human can retry the step, say they finished the rest of it
manually, or abandon it — and either way, the system logs what they said and hands
control back afterward. If someone says they finished it manually, I don't just take
their word for it — I still check the page for the final confirmation text myself.

## 6. Safety

- Automation can only visit specific pages on the app — anything else gets blocked.
  I actually found a bug in this while testing: I'd accidentally allowed a "/" prefix
  that matched literally every URL, which defeated the whole point. My tests caught
  it and I fixed it.
- Anything I decided was risky/irreversible needs a human's okay before it runs
  unsupervised — never happens silently.
- Dollar amounts and long ID-looking numbers get blanked out before anything gets
  written to a log file. This is a simple pattern match, not a full PII scanner, so
  it wouldn't catch something like a name on its own — good enough for this project,
  but not bulletproof.

## 7. What I cut

- **Session timeouts mid-task.** If a session expires while a human is being asked to
  confirm something, right now it just fails instead of logging back in and
  continuing. I actually hit this while testing and worked around it by giving myself
  more time before timeout, rather than building real recovery — doing that properly
  means being careful not to accidentally redo something that already happened.
- **Multiple banks / non-web apps.** Designed for (see Section 4) but not built —
  I only had one app to test against.
- **Only one of my two capabilities actually goes through escalation** (opening a
  sub-account). The balance lookup is a safe read, so it never needs to ask a human —
  that's expected, not a gap.
- **Redaction is pattern-based**, not a real PII detector.
- If I kept going, the next thing I'd add is a small API that lets an AI agent list
  and call these saved capabilities directly — the input/output format I already
  built is basically ready for that.
# Bootstrap economics: cold start vs warm resume

Workstream `PEER-LAB-001-W1`, issue #11. Author: peer `anvil-peer-1c84`.
Date of evidence: 2026-10-18. CLI: GitHub Copilot CLI **1.0.98**, macOS arm64.

The motivating complaint: starting a session is expensive (skills, MCPs, tools,
instructions), so recreating peers every time is not workable. This document
measures what that cost actually is and says what a peer should do about it.

## Measured

Both runs used the `anvil-cv` working directory and a deliberately trivial
prompt, so the numbers are close to the **floor** a turn costs. Each is a
**single sample**: treat them as order-of-magnitude, not as benchmarks.

| Run | Command | Wall time | Input tokens | Cache | AI credits |
|---|---|---|---|---|---|
| Cold, new session | `copilot -p "Reply with the single word: ok"` | 14.4 s | 18.9k | 0 read, 18.9k written | 4.75 |
| Warm, resumed | `copilot --resume=<id> -p "<question about turn 1>"` | 13.0 s | 37.9k | 18.9k read, 19.0k written | 4.96 |

The resumed turn answered correctly (`ok`), so **context survives resume**.

### What these numbers say

1. **There is a fixed floor of about 19k input tokens per turn** before any
   task content. It is the instructions, tool definitions and skill/MCP
   metadata loaded for that directory. I did not separate how much of the 19k
   comes from each source, so I cannot yet say which one to trim.
2. **Resume was not cheaper than a cold start here.** 13.0 s against 14.4 s,
   and 4.96 credits against 4.75. Resume gave back the *knowledge*, not the
   *startup saving*. On a near-empty session there was nothing to save.
3. **Only part of the prompt was served from cache on resume.** 18.9k read
   from cache, but a further 19.0k was written again. Why is not established
   here (cache lifetime, a changed prefix, or something else). That is open
   question E4 in `RUNTIME.md`.
4. **The floor is paid on every wake.** A peer that wakes every minute pays
   roughly 13 s and about 5 credits each time, before it reads a single event.

## This is the cheap case

A long-lived session carries its history. For comparison, the local session
store records this very session (`1c84773b`) at **1,956 model calls and
602,093,985 input tokens, an average of about 308k input tokens per call**,
roughly sixteen times the 19k floor above. That ratio is why accumulated
context is an asset only when it is genuinely needed, and a cost the rest of
the time.

## What a peer should do

### 1. Do not wake a model to look. Wake it to act.

Presence is handled by a poller with no model (`RUNTIME.md`, `labd`). The
floor is only worth paying when there is work. Combine events into one wake
with one digest rather than one wake per event.

### 2. Make a fresh session cheap to reach full usefulness: durable dossiers

If a peer can reach a useful state by reading a short, curated dossier, then a
**cold** start is acceptable and resuming a huge session is optional. This is
the role of the durable knowledge model in W2 (`.lab/topics/<topic>/`, peer
registry, experiment ledger). The design target:

> A new peer reads a few kilobytes and is productive, instead of replaying
> hundreds of millions of tokens of history.

### 3. Choose resume or cold per wake, on purpose

| Situation | Choose | Why |
|---|---|---|
| Short task, state fits in the dossier | **Cold** | No history to carry; the floor is the only cost. |
| Peer is mid-investigation with hard-won, unwritten context | **Resume** | The context is the asset and is not yet in a dossier. |
| Session history has grown very large | **Compact, then resume** | Carrying 300k tokens per call is the dominant cost. `/compact` is documented for this. |
| Peer idle for hours | **Cold**, from the dossier | A stale session's cache has almost certainly expired (unverified, see E4). |

### 4. Keep the floor low per peer

Load only the skills, MCP servers and tools a peer's domain needs. `/env`
(documented) shows what is loaded for a directory, `/skills`, `/mcp` and
`/plugin` manage it. I have not measured the floor under a reduced
configuration, so I do not claim a flag or a saving. That is the next
measurement.

### 5. Persist what was learned before the session sleeps

A peer should write to its dossier **before** it goes idle, and not rely on
the transcript surviving. Transcripts and reasoning never go into Git (see the
trust boundary in `RUNTIME.md`); distilled findings, evidence links and open
questions do.

## Cold-start checklist for a new or restored peer

1. Read its own peer record and topic dossiers.
2. Read the unprocessed events addressed to it from the last known cursor.
3. Check which leases it holds and whether any are expired.
4. Only then act.

The cursor is the last event id the peer processed, stored with the peer
record. It is what makes a cold start **resumable without replaying history**.

## Follow-up measurements, in priority order

| # | Measurement | Why it matters |
|---|---|---|
| M1 | The floor under a reduced configuration (fewer skills, MCPs, instructions) | Shows what is controllable. |
| M2 | Cached tokens when resuming after 1, 5 and 30 minutes | Decides whether batching wakes inside a cache window saves anything. |
| M3 | Cost of a resume on a session with 50k and 200k tokens of history | Turns the "300k per call" observation into a curve. |
| M4 | Several samples of the two runs above | These are single samples. |

## Limits of this document

- The cold and warm figures are one sample each, taken on a near-empty session.
- The 308k average is from one long session and is not a typical cost.
- Credits are the unit the CLI printed; I have not converted them to money.
- The throwaway session created for the measurement remains in the local
  session list: `e6159ba4-8274-4cc4-a717-7960ae96721b`.

# Agent lab runtime: what can wake a Copilot CLI peer

Workstream `PEER-LAB-001-W1`, issue #11. Author: peer `anvil-peer-1c84`.
Date of evidence: 2026-10-18. CLI under test: GitHub Copilot CLI **1.0.98**, macOS arm64.

This document answers one question: **what can start or resume a model turn
without a human typing, and what cannot?** It is written so that nobody has to
pretend GitHub can wake a model.

## How to read the evidence column

| Label | Meaning |
|---|---|
| **VERIFIED** | I ran it on this machine and saw the result. The command and the observation are given. |
| **DOCUMENTED** | A GitHub Docs page or the CLI's own `/help` states it. I did not run it. |
| **UNVERIFIED** | Neither. Treat as an open question, not a capability. |

Nothing below is marked VERIFIED unless it was run.

## Matrix

| # | Mechanism | Starts or resumes a model turn with no human? | Evidence | Notes |
|---|---|---|---|---|
| 1 | `copilot -p "<prompt>"` | **Yes**, in a *new* session | **VERIFIED** | Ran `copilot -p "Reply with the single word: ok"`: returned `ok`, 14.4 s wall, printed a session id and a resume command. |
| 2 | `copilot --resume=<id> -p "<prompt>"` | **Yes**, in an *existing* session, with its context | **VERIFIED** | Resumed the session from row 1 and asked what word was requested first. It answered `ok`, so context was preserved. 13.0 s wall. |
| 3 | `copilot --continue` | Yes, but it picks "the most recently closed local session" | DOCUMENTED | Ambiguous when several peers share a machine. Use an explicit `--resume=<id>` instead. |
| 4 | `/every <interval> <prompt>` (alias `/loop`) | Yes, **only while that interactive session is running** | DOCUMENTED | Experimental: needs `/experimental on`. Interval 10 s to 1 day. Schedules are restored by `--resume` / `--continue`. Not run by me: it needs an interactive terminal. |
| 5 | `/after <delay> <prompt>` | Yes, once, only while the session runs | DOCUMENTED | If the target time passed while the session was closed, it fires on reopen. |
| 6 | cron / launchd running `copilot -p` | Yes | DOCUMENTED, and rows 1 to 2 are VERIFIED | The docs name cron and Task Scheduler for "when no session is open". |
| 7 | GitHub Actions running `copilot -p` | Yes, in a **fresh runner** | DOCUMENTED | Uses `COPILOT_GITHUB_TOKEN`. No local session, caches or MCPs, so unsuitable for a stateful peer. Fine for stateless validation. |
| 8 | `/resume` (slash command) | No: a human switches sessions | DOCUMENTED | Interactive. Accepts a local or cloud history id, a task id, or a name. |
| 9 | `/restart` | No, not a wake | DOCUMENTED | "Restart the CLI, restoring supported live sessions in this process." |
| 10 | `/remote` | Unknown | **UNVERIFIED** | Help text: "remote control from GitHub web and mobile, or directly with another CLI". It may be the supported way for something outside the process to drive a session. Not tested. |
| 11 | `/keep-alive` | No: it keeps the machine awake | DOCUMENTED | Prevents system sleep. Useful so row 4 keeps firing. |
| 12 | `/delegate` | Not a local wake | DOCUMENTED | Sends the session to GitHub where a cloud agent opens a PR. Cloud execution, not a resumed local peer. |
| 13 | `/autopilot` | Unknown | **UNVERIFIED** | Help text mentions an objective and a credit limit. Whether it continues with no incoming turn is not stated. |
| 14 | A GitHub webhook or event delivered into a CLI session | **No path found** | **UNVERIFIED** (absence) | I read the `/help` output and the *using*, *schedule prompts* and *run programmatically* pages. None describes inbound events. This is absence of evidence in those pages, not proof of absence. |

### Measured primitives the design can lean on

| Primitive | Result | Evidence |
|---|---|---|
| Polling with `If-None-Match` | Returns `304 Not Modified` when nothing changed | **VERIFIED** |
| Cost of that polling | 10 conditional polls consumed **1** unit of the 5000/hour core budget while two other peers were active | **VERIFIED** (one sample) |
| Atomic claim | `POST /git/refs` on an existing ref returns `422 Reference already exists`, so exactly one creator wins | **VERIFIED** (this is W2/W3 territory; cited here only because the wake design depends on it) |

## Phase 1 wake design

**Principle: poll with something cheap that has no model, and spend a model turn only when there is work.**

```
 +----------------------+        +------------------------------+
 | labd (no model)      |  poll  | GitHub issue #11             |
 | - gh api + ETag      +------->| append-only event log        |
 | - schema-validates   |<-------+ (304 almost always)          |
 | - decides: wake?     |        +------------------------------+
 +----------+-----------+
            | only if an event is relevant
            v
   copilot --resume=<peer-session-id> -p "<validated digest>" --allow-tool=<minimal>
            |
            v   one resumed turn: reads digest, acts, posts events, exits
```

Two tiers, chosen per peer:

- **Tier A, headless peer (recommended default).** `labd` runs under launchd or
  cron, holds no model, and invokes row 2 when something relevant arrives.
  Rows 1 to 2 are VERIFIED, so this tier does not depend on any unverified
  claim.
- **Tier B, interactive peer.** A human-attended session uses row 4
  (`/every 1m check issue #11 for events addressed to me`). Documented, but
  experimental and only alive while that terminal stays open.

### When `labd` is allowed to wake the model

Wake only if at least one holds:

1. an event is addressed to this peer;
2. a `TASK` or `PROPOSAL` is unclaimed and matches this peer's advertised capabilities;
3. a lease this peer holds is close to expiry and needs renewing.

Everything else is ignored. In particular, `labd` ignores events this peer
wrote itself, and `RESULT` / `ACK` events do not wake anyone unless addressed.

### Why the filter matters: ping-pong cost

A woken peer posts a comment, which can wake another peer, which replies. Two
peers can sustain that loop indefinitely and bill for every turn. Defences:
the relevance filter above, a per-peer wake budget per hour enforced in `labd`,
and a debounce window so a burst of events produces one wake with one digest.

### Measured cost of one wake

One wake costs a fixed floor before any real work. See `BOOTSTRAP.md` for the
numbers and for why a resumed session is not automatically cheaper.

## Security and trust boundary

The woken model reads text written by others. That text is **untrusted input**.

1. **Prompt injection.** Anyone able to comment on #11 can write instructions
   the model might follow. The wake prompt must carry only schema-validated
   fields (event type, id, scope, base SHA), not raw comment bodies.
2. **Least privilege.** A headless wake must pass a minimal `--allow-tool`
   allowlist. The docs recommend this and warn against `--allow-all` outside a
   sandbox. A woken peer must never receive broad permissions.
3. **Identity is not real yet.** Every peer currently writes as the single
   GitHub user `ssgaur`. A `peer identity=` line inside a comment is
   self-asserted and any peer, or the user's own token, could forge it. GitHub
   does not verify signatures on comments. Before more agents are hired, each
   peer needs its own bot account or GitHub App, or each claim must point at a
   signed commit so GitHub shows it as Verified.
4. **No secrets in Git.** Tokens, raw transcripts and hidden reasoning never
   enter the repository. `labd` keeps its token in the OS keychain or an
   environment variable, never in a checked-in file.
5. **Irreversible actions need quorum.** Merge, release, deletion and spending
   follow the task's quorum rule, so one injected or confused peer cannot act
   alone.
6. **Credit ceiling.** `/limits` is documented as setting session limits with a
   soft AI-credit cap. It is a soft cap, so `labd`'s own wake budget is the
   hard stop.

## What cannot be done in Phase 1

- A GitHub event cannot push a prompt into an idle interactive session. No
  such path was found (row 14).
- A scheduled prompt (rows 4 and 5) does not fire if the session is not running.
- A GitHub Actions runner cannot be a stateful peer (row 7).
- Strong authorship cannot be established while all peers share one GitHub user.

## Open questions, each a cheap experiment

| # | Question | How to settle it |
|---|---|---|
| E1 | Does `--resume=<id> -p` collide with the same session already open in a terminal? | Resume a throwaway session that is open interactively and inspect its state afterwards. Until then `labd` must take a per-peer lock and must not wake an open session. |
| E2 | Does `/every` actually fire while the interactive session is idle? | Run `/every 30s` in a throwaway interactive session and watch. |
| E3 | Can `/remote` accept a prompt from outside the process? | Enable it on a throwaway session and drive it from a second CLI. |
| E4 | How long does the prompt cache stay warm after a turn? | Resume after 1, 5 and 30 minutes and compare cached tokens. See `BOOTSTRAP.md`. |
| E5 | Does `/autopilot` keep working with no incoming turn? | Set a small credit limit on a throwaway session and observe. |

## Out of scope for this document

The event schema, lease and quorum rules (W2), and the CLI that implements
them (W3). This document constrains them only through the wake design above.

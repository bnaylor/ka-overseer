# Overseer design

Approved in brainstorm 2026-10-01. Problem statement and brief: `overseer.md`.

## Decisions

| Question | Decision |
| --- | --- |
| Alerting when a session waits on the human | macOS notification once per item, plus a persistent red band on the dashboard |
| Authority | Direct on written rules (STOP), assign work when the human asks, advise otherwise. A session may decline advice with a reason. |
| Status flow | Hybrid: sessions push a fixed-format report on change; the Overseer reads GitHub itself and pings only on silence or drift |
| Scope | sessions matching `session_pattern` in `overseer/config.json` sessions only |
| Dashboard | Static HTML opened from disk, reloads itself every minute. No server until interaction is needed. |
| Overseer driver | Self-paced loop. Fast while moving, slow when quiet, never stops. |
| Rule relay | Numbered `rules.md` plus a RULE broadcast on change, acknowledged by number |
| Design calls | Go to the human. Never routed to a peer session. Nothing blocked in one session is asked of another. |
| Gating | `kube-agents-bot` reviews are the gating rounds and count toward the cap. `kyber775` is advisory, shown separately, never capped. |
| Gone sessions | Stay greyed while they own an open unclaimed PR; retire to history after 24 h once they own nothing, or when the human says |

## Spike learnings (2026-10-01, scratch session `overseer-spike`)

- The roster has four live states: `busy`, `idle`, `waiting` (parked on a question tool), `shell` (running a command). `waiting` is the question stall, visible directly. A permission prompt is assumed to read `waiting` too; untested.
- A message to a `waiting` session queues and is delivered the moment the human answers. It cannot unpark the session. It renders on that session's screen above the pending question, so it also reaches whoever is looking at that terminal.
- A message wakes an idle session; the fixture replied in about twenty seconds. `notify_when_idle` works and the notice carries a summary of the session's last turn.
- A session does not know its own name until it calls `ListAgents`, whose first line states it.
- `claude agents --json` returns name, status, kind, cwd, pid, startedAt, sessionId from the shell. A script can read the roster without the Overseer spending a turn.
- The Overseer cannot start sessions (classifier). Assigning work means messaging sessions the human has started.
- `claude attach` needs a real TTY; the `!` prefix detaches at once.

Consequence: the mechanical watching is split out of the Overseer session into `watch.sh`
(below), so the stall alert and the dashboard's status column do not wait for a tick.

## Files

All under `overseer/` in this repo unless noted.

| File | Purpose | Writer |
| --- | --- | --- |
| `overseer.md` | the human's brief | the human |
| `design.md` | this spec | brainstorm |
| `protocol.md` | message types and report schema, normative | Overseer session, with the human |
| `rules.md` | numbered process rules relayed to sessions | the human via the Overseer |
| `escalation.md` | stall signals, thresholds, ladder | Overseer session, with the human |
| `state.json` | single source of truth | Overseer only |
| `reports.log` | append-only, one JSON line per received report | Overseer only |
| `gh_snapshot.py` | the only place `gh` is called; writes the `prs` part of state and looks up PRs for worktree branches | code |
| `worktrees.py` | lists the extra git worktrees under the watched folders, with owner, PR and cleanup status; read-only | code |
| `watch.sh` | every 60 s: roster to `roster.json`, render, notify on a new `waiting` session | code |
| `roster.json` | last `claude agents --json`, filtered to kube-agents sessions | `watch.sh` |
| `render.py` | `state.json` to `dashboard.html` | code |
| `dashboard.html` | generated page | `render.py` |
| `../auto/overseer.txt` | the Overseer's loop brief | the human |
| `<ka-overseer>/skills/working-with-the-overseer/SKILL.md` | teaches every other session the protocol and the stall traps; symlinked into `~/.claude/skills/` | code |

The three briefs in `auto/` gain one sentence: on start, report to the Overseer per the skill.

## State model (`state.json`)

```json
{
  "updated": "2026-10-01T21:00:00Z",
  "rules_version": 7,
  "overseer": {"session": "<overseer-session>", "tick": 12, "next_wake": "2026-10-01T21:15:00Z"},
  "sessions": {
    "<session-name>": {
      "role": "pr-minder",
      "theme": "consumer budget",
      "driver": "loop", "cadence_min": 30,
      "mode": "auto",
      "rules_ack": 7,
      "prs": [2077, 2056],
      "status": "waiting-human",
      "roster_status": "waiting",
      "needs": "ci-deploy ordering decision on #2077",
      "last_report": "...", "last_activity": "...", "last_poke": "...",
      "ladder": 0,
      "note": "one line"
    }
  },
  "prs": {
    "2077": {
      "owner": "<session-name>",
      "title": "...", "head": "b18c48fe", "branch": "...",
      "mergeable": "MERGEABLE", "checks": "green",
      "unresolved_threads": 0,
      "rounds": 5, "advisory_rounds": 0,
      "hold": false, "lgtm": false, "approved": true,
      "reviewers": ["jayantid"],
      "last_activity": "...", "last_owner_report": "...",
      "drift": null
    }
  },
  "attention": [
    {"id": "s54-2077-q1", "session": "<session-name>", "pr": 2077,
     "since": "...", "what": "ci-deploy ordering decision", "notified": true}
  ],
  "retired": [ {"session": "...", "role": "...", "retired_at": "...", "prs_at_exit": []} ]
}
```

Roles: `pr-minder`, `bug-minder`, `review-minder`, `task`, `overseer`.
Drivers: `loop`, `goal`, `manual`.
Session `status` is self-reported, exactly one of: `working`, `waiting-human`, `waiting-review`, `idle`, `gone`.
`roster_status` is observed, one of `busy`, `idle`, `waiting`, `shell`, `gone`, and is refreshed by `watch.sh`, not by the session.
Checks: `green`, `red`, `pending`, `none`.
`drift` is null or one sentence naming the disagreement between GitHub and the owner's last report.
`ladder` is the escalation step for a non-reporting session (0 none, 1 pinged, 2 pinged twice, 3 escalated).

## Report schema (session to Overseer)

Six lines maximum. The first line is the headline the receiver previews.

```
OVERSEER REPORT <session-name>
role: pr-minder | theme: consumer budget | driver: loop/30m | mode: auto
prs: #2077 round 5 hold waiting-lgtm; #2056 round 16 0-threads waiting-lgtm
status: waiting-human: ci-deploy ordering decision on #2077
rules: 7
note: optional one line
```

A session sends a report:

- on intake, in reply to INTRO;
- on any change to its status or PR list;
- when it hits a rule boundary, such as the round cap;
- before it asks the human anything, in the same turn, with `status: waiting-human: <the question>`;
- before it stops or lets a loop lapse;
- in reply to PING.

The `prs:` line is semicolon-separated, one PR per item, free form after the number but
starting with the owner's belief about rounds and threads so drift can be computed.

## Messages (Overseer to session)

Each starts with a fixed first word.

| Word | Meaning | Expected response |
| --- | --- | --- |
| `INTRO` | who the Overseer is, path to the skill, request for a report | a report |
| `PING` | request a report | a report |
| `RULE n: <text>` | a new or changed rule | a report whose `rules:` line is `n` or higher |
| `STOP <reason>` | rule enforcement: halt the named activity | a report describing where you stopped |
| `ADVICE <text>` | a suggestion; may be declined with a reason | optional reply |
| `ASSIGN #<pr> <hazards>` | take ownership of this PR | a report listing the PR |

STOP is sent only for a written rule in `rules.md`. The current STOP cases are: gating rounds
at or past the cap, a push during a live gating round, and a `/hold` placed without a
clearance criterion.

## Stall detection and escalation (`escalation.md`)

Proactive path: the skill makes sessions self-report `waiting-human` before they ask.
That creates an attention item immediately, with one notification.

Signals checked each tick for sessions that have not self-reported:

| Signal | Threshold | Action |
| --- | --- | --- |
| Roster `waiting` (parked on a question or prompt) | immediate, by `watch.sh` | attention item, one notification; Overseer adds the PR context on its next tick |
| Silent while an owned PR moved | past 2x stated cadence | PING with idle subscription, ladder 1 |
| Roster idle but last report said working | one tick | PING, ladder 1 |
| Gone from roster while owning open PRs | immediate | attention item "orphan", offer reassignment |
| Unresolved threads untouched, owner silent | 2 h | PING; attention after one more tick |
| Red check | 1 h | PING; attention after one more tick |
| Parked waiting-review, no reviewer assigned | 1 day | ADVICE (request review); attention after one more tick |
| Gating rounds | 6 or more | STOP, attention item "drift check" |

Ladder for a non-reporting session: PING with an idle subscription (1) → next tick PING
again (2) → attention item and one notification (3). The idle notice carries a summary of the
session's last turn, so a session that forgets to report still tells the Overseer what it did. The ladder resets on any
report. An attention item notifies once and persists until the session reports a different
status, the facts overtake it, or the human clears it. No repeat notifications for the same item.
The facts that clear an item without a report (a gone session's leftovers after a restart or
rename) are listed in `escalation.md`.

Notification: `osascript -e 'display notification "<session>: <what>" with title "Overseer"'`.

## Rules (`rules.md`, initial set)

1. Gating bot rounds cap at six. At the cap, stop fixing and re-evaluate for drift; new findings past the cap are the human's call.
2. A clean gating round whose findings were all Medium or below needs no further `/review`. Proceed to human review.
3. Resolving open review threads is the top priority. They are what reaches the human gate.
4. A `/hold` needs its reason and its clearance criterion in the same comment.
5. Never push while a gating review round is in flight.
6. Never lgtm the human's own PRs.
7. `kyber775` is advisory. It does not gate and its findings do not count as rounds.
8. A finding whose fix is a new rule, a regex, or a design change goes to the human, not into the PR.
9. Nothing blocked in your session is asked of a peer. Route it to the human via the Overseer.

Numbers are stable. Changes append; a changed rule gets a new number and the old one is struck.

## Watcher (`watch.sh`)

A shell loop the human starts once in a spare terminal (or via a launchd plist). Every 60 s:

1. `claude agents --json` filtered to names matching sessions matching `session_pattern` in `overseer/config.json` and `overseer*`, written to `roster.json`.
2. `render.py`, which merges `roster.json` over `state.json` at render time so the status column is at most a minute old.
3. For each session whose roster status became `waiting` since the last pass, one `osascript` notification naming the session. A session that stays `waiting` does not re-notify; one that leaves and re-enters does.

The watcher never writes `state.json` and never sends messages. Judgment stays in the Overseer.

## Dashboard (`render.py` to `dashboard.html`)

Static page, `<meta http-equiv="refresh" content="60">`, state and roster embedded as JSON,
ages computed client-side so they tick between renders. One line per cell, long notes truncate
with full text on hover. Colour and status encoding follow the dataviz skill.

1. **Attention band** at the top. One row per item, oldest first: session, PR, age, what. Empty state is one quiet green line. This is the only red on the page. `gone-question` notes (a gone session's unanswered question) sit in a muted table below it and do not count against the green line.
2. **Sessions table.** Name, role, theme, driver and cadence, roster status, reported status, PRs, last report age, last activity age, rules acknowledged, note. Rows tint by status. Age past 2x cadence highlights. Gone sessions grey.
3. **PRs table.** Number and title, owner, mergeability, checks, unresolved threads, gating rounds (highlight at 6), advisory rounds (muted), hold, lgtm, approved, reviewers, last activity age, drift. Sorted nearest-the-gate first: zero threads and green checks at the top. Grouped by the `scope_queries` search that found the PR, in config order (by default: mine, review requested from me, reviewed by me). Each tick records the matching searches on the PR as `scopes`; a search that fails keeps the last tick's marks. Every search's group shows, even when empty. PRs no search finds go in an "Other" group: a session reports them, or they dropped out of every search. PRs saved before searches were recorded wait in "Not sorted yet" until the next tick that reads GitHub.
4. **Worktrees table.** Every extra worktree under the watched folders: path, owner, branch, PR and its state, unsaved work, last change, status, and the reason. Hovering a reason shows the command that cleans it up. Sorted with the ones to clean up first. See "Worktrees" below.
5. **Footer.** Rendered when, rules version in force, next Overseer wake.

## Worktrees (`worktrees.py`)

Sessions make a worktree per PR and often leave it behind once the PR merges. On every tick
that reads GitHub, the Overseer lists them so the human can see what is safe to remove. It
never removes one itself.

- **Where it looks.** `worktree_roots` in `config.json`, or `cwd_prefixes` when that is empty.
  Each root and each folder directly inside it that holds a main clone (`.git` is a directory)
  is asked for `git worktree list`. That also finds worktrees created elsewhere, such as under
  `/tmp`. A folder directly inside a root whose `.git` file points at a record git already pruned
  is listed too, because `git worktree list` no longer shows it.
- **PR.** One `gh pr list --author @me --state all` per repo, then `--head <branch>` for branches
  it did not cover. The repo is the clone's `upstream` remote, else `origin`. A PR only matches
  when its head is on the clone's `origin` owner. The open PR wins over older ones on the same branch.
- **Owner.** The PR's owner in state, else a live session listing that PR, else the session whose
  folder holds the worktree (deepest folder, live first, the Overseer last).
- **Unsaved work.** Anything that exists only in this folder, because `git worktree remove` deletes
  it without a warning:
  - uncommitted changes, untracked files, and files marked assume-unchanged or skip-worktree;
  - gitignored files outside tool caches (`__pycache__`, `node_modules`, `.terraform`, `.venv` and
    similar), such as `install.env`, `terraform.tfvars` or local terraform state;
  - commits no remote branch has. The PR's head commit counts as pushed, because GitHub deletes a
    merged PR's branch. A detached worktree also counts commits only its own history log still reaches.
- **Last change.** The latest of the last commit, the staging area, and the edited files. The scan
  runs git with `--no-optional-locks`, so it does not count as a change itself.
- **Status.**
  - `in use`: the PR is open, a live session is working inside the folder, or there is no finished PR and something changed in the last 3 days. Commits after a PR merged or closed count as no finished PR.
  - `cleanup`: the PR merged or closed, or there is no PR and nothing changed for 3 days; and nothing is unsaved. `git worktree remove` loses nothing: the branch and its commits stay.
  - `check`: same as `cleanup` but something is unsaved or the worktree is locked, or the folder lost its git record. The human decides.
  - `missing`: git lists the worktree but the folder is gone; `git worktree prune` drops it.
  - `unknown`: git failed, the clone's remote is not on GitHub, or GitHub did not answer and no earlier tick knew the PR.
- The tick's summary line counts `cleanup` plus `missing` as worktrees to clean up.

## GitHub snapshot (`gh_snapshot.py`)

Input: the set of PR numbers (union of `~/bin/my_open`, `my_open reviews`, `my_open reviewed`,
and every PR any session reports). Output: the `prs` map above, merged into `state.json`
preserving `owner`, `last_owner_report`, and `drift` inputs.

Per PR: `gh pr view --json` for title, head, branch, mergeable, labels, reviews, comments,
reviewRequests, statusCheckRollup, updatedAt; one GraphQL query for `reviewThreads` to count
`isResolved == false`. `rounds` is the count of review submissions by `kube-agents-bot`;
`advisory_rounds` the same for `kyber775`. `hold`, `lgtm`, `approved` come from labels.
`checks` is `red` if any rollup entry failed, `pending` if any is in progress, `green` if all
passed, `none` if empty.

Drift: compare the owner's last `prs:` item for this PR (rounds, threads, hold) with the
snapshot; on disagreement set `drift` to one sentence.

## The Overseer's tick

Startup: load `state.json`, else rebuild from `reports.log`. Read `roster.json` (or `ListAgents` if the watcher is not running); INTRO every
sessions matching `session_pattern` in `overseer/config.json` session not in the table. Snapshot GitHub. Write state, render, open the
dashboard once.

Each tick:

1. Roster diff from `roster.json`. INTRO newcomers. Mark leavers `gone`; orphan check on their PRs. For each `waiting` session, attach the PR and last-report context to the attention item the watcher opened.
2. GitHub snapshot and drift. Worktree scan (see "Worktrees").
3. Stall signals and ladders. Send PING, STOP, ADVICE. Add attention items; notify each new one once.
4. Retire gone sessions that own nothing and have been gone 24 h.
5. Write `state.json`, run `render.py`.
6. Report to the human in at most five lines: new attention items, what changed, next wake.
7. Schedule the next wake: 15 min if anything moved or a ladder is live; 60 min when quiet; 120 min after several quiet ticks. Never stop.

Between ticks, an incoming report wakes the session: append to `reports.log`, merge, re-render.

Rule change: the human states it here; it gets the next number in `rules.md`; `RULE n` is
broadcast to every live session; acks are tracked in `rules_ack`.

Assignment: the Overseer cannot start sessions; Y must already exist. the human says "give #X to Y"; Y receives `ASSIGN #X` with the hazards recorded in
state; the previous owner is told; ownership flips on Y's next report.

Retire: the human says "retire Y"; the row moves to `retired` and leaves the dashboard.

## The skill (`working-with-the-overseer`)

Triggers: a message from a session whose name the Overseer announced, starting any
kube-agents loop or goal, adopting or opening a PR in kube-agents, or about to ask the human a
question in a kube-agents session.

Content: who the Overseer is and what it may direct versus advise; the report schema and
when to send it; the six message types and what each asks; the stall traps and the
proactive `waiting-human` report; where `state.json`, `rules.md`, and `dashboard.html` live;
the current rules by number.

The skill opens with: call `ListAgents` first; its first line is your own session name, and
every report must carry it. While you are parked on a question, nothing the Overseer sends
reaches you until the human answers, so never wait on the Overseer for anything while parked.

Stall traps named in the skill:

- Ending a turn on "shall I" or "want me to" is a stall. Routine judgment calls are the session's. Only design calls, destructive actions, new rules or regexes, and anything blocked by permissions go to the human.
- A `/review` believed posted but never verified to have landed.
- A `/hold` without a clearance criterion.
- A push during a live gating round.
- A session not in auto mode holds the Overseer's messages for approval and is invisible. State your mode at intake.
- Something blocked by permissions goes to the human via `waiting-human` with the exact command, never to a peer.

Running sessions will not see a newly added skill in their listing; INTRO points at the
file path. New sessions get it from the listing.

## Verification before "done"

- `gh_snapshot.py` on #2077 reports rounds equal to the count of `kube-agents-bot` reviews visible on the PR, and threads equal to the unresolved count on the GitHub page.
- A synthetic report parsed by the merge step produces the expected session row, and a report with `waiting-human` produces an attention item and exactly one notification.
- `render.py` on a state with an empty attention list shows the green line; with one item other than a `gone-question` note, the red band.
- One live INTRO to a real session returns a report in the schema.
- `watch.sh` with a session entering `waiting` fires exactly one notification and the dashboard status column shows `waiting` within a minute.
- The question-tool spike is done (see Spike learnings); the permission-prompt case is still an assumption.

## Out of scope for the first build

Interactive dashboard actions, a server, non-kube-agents sessions, parsing free-form prose
from sessions that ignore the schema (the Overseer asks them to resend in the schema), and
any automatic reassignment without the human's word.

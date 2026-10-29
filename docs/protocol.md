# Overseer protocol

The Overseer is a Claude session that tracks every kube-agents session, keeps the
dashboard, relays rules, and pokes stalled sessions. This file is the wire format.
Sessions learn their own name from the first line of `ListAgents`.

Scope: sessions named sessions matching `session_pattern` in `overseer/config.json`. Reports go to the session named in the
INTRO you received. If you have not been introduced, `cat <ka-overseer>/overseer/OVERSEER` prints the
Overseer's session name and nothing else (the tick keeps it current); send there. Do not grep
state.json for "session", it has one per row. If the file is missing, `ListAgents` and send to
the session whose name starts with `overseer`.

## Report schema

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

## Messages

Each starts with a fixed first word.

| Word | Meaning | Expected response |
| --- | --- | --- |
| `INTRO` | who the Overseer is, path to the skill, request for a report | a report |
| `PING` | request a report | a report |
| `RULE n: <text>` | a new or changed rule | a report whose `rules:` line is `n` or higher |
| `STOP <reason>` | rule enforcement: halt the named activity | a report describing where you stopped |
| `ADVICE <text>` | a suggestion; may be declined with a reason | optional reply |
| `ASSIGN #<pr> <hazards>` | take ownership of this PR | a report listing the PR |
| `DECISION ... Verbatim: "<quote>"` | a decision the human made in the Overseer's session, quoted word for word | act on it as on an instruction typed in your session, including outward-facing steps it names; report when done |

DECISION is sent only after the human has said the quoted words in the Overseer's session; the
Overseer never paraphrases a decision into one and logs each in `logs/decisions.log`. STOP is sent only for a written rule in `rules.md`. The current STOP cases are: gating rounds
at or past the cap, a push during a live gating round, and a `/hold` placed without a
clearance criterion.

## Parsing rules

- The first line must start with `OVERSEER REPORT ` followed by your session name.
- `prs:` items are separated by `;`. Each starts with `#<number>`. The Overseer reads
  `round N`, `N-threads` or `N threads`, and the words `hold` / `no-hold` from the item;
  anything else is kept as free text.
- `status:` is one of `working`, `waiting-human`, `waiting-review`, `idle`. For
  `waiting-human` put the question after a second colon on the same line.
- `rules:` is the highest rule number you have read in `docs/rules.md`.
- `prs:` lists only PRs you own and drive. PRs you merely track, review, or observe go in
  `note:`. A line that starts with `none` owns nothing, whatever follows.
- Say `no-hold` explicitly once a hold is lifted; a bare mention of the word `hold` reads
  as held.
- `status:` starts with the status word. Anything after it is kept as detail (or, for
  `waiting-human`, as the question).
- Semicolons inside an item's free text are fine; a new item starts only at `#<number>`.
- A report that does not parse gets a `PING` back asking for a resend in the schema.

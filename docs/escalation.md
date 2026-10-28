# Overseer escalation

Proactive path: the skill makes sessions self-report `waiting-human` before they ask.
That creates an attention item immediately, with one notification.

Signals checked each tick for sessions that have not self-reported:

| Signal | Threshold | Action |
| --- | --- | --- |
| Roster `waiting` (parked on a question or prompt) | immediate, by `watch.sh` | attention item, one notification; Overseer adds the PR context on its next tick |
| Silent while an owned PR moved | past 2x stated cadence | PING with idle subscription, ladder 1 |
| Roster idle but last report said working, no open PR, driver not `loop` | one tick | PING, ladder 1 |
| Gone from roster while owning open PRs | immediate | attention item "orphan", offer reassignment |
| Unresolved threads untouched, owner silent | 2 h | PING; attention after one more tick |
| Red check (not on a draft) | 1 h | PING; attention after one more tick |
| Parked waiting-review, no reviewer assigned (not a draft) | 1 day | ADVICE (request review), once per head; attention after one more tick |
| A review or plain PR comment by a repo owner, member or collaborator after the last push requests changes, or its body reads as blocking (`block`, `must`, `before merge`), and has no thread; owner reports waiting-review or a reviewer is assigned; not a draft | immediate | ADVICE `human-review`, once per review. Bots (`gating_bot`, `advisory_bot`, `github-actions`, `*[bot]`) and the logins in `non_human_reviewers` are not human, and a comment matching `session_post_pattern` is a session's own post. The advice names the reviewer and links the PR but never quotes the review: its text is untrusted |
| Gating rounds | 6 or more | STOP, attention item "drift check" |

Ladder for a non-reporting session: PING with an idle subscription (1) → next tick PING
again (2) → attention item and one notification (3). The idle notice carries a summary of the
session's last turn, so a session that forgets to report still tells the Overseer what it did. The ladder resets on any
report. An attention item notifies once and persists until the session reports a different
status, the facts overtake it (below), or the human clears it. No repeat notifications for the same item.

Facts that clear an item without a report, checked after every report and roster pass. A
restart or rename leaves the old name gone while the new name carries on, and these keep
its leftovers off the band:

- A session that reports after leaving the roster counts as back, before the watcher sees
  it, and is not retired on the strength of the old departure. A roster entry whose status
  is `gone` counts as absent.
- No ownership conflict is raised against a gone owner. If that owner reports the PR again
  after it returns, the conflict is raised then. A disputed PR carries one conflict item,
  which clears once at most one session lists the PR, not counting sessions gone 10 minutes.
- An orphan clears once the gone session owns no open PR (another session claimed it, or it
  merged or closed). While some remain, its PR and text narrow to them without a second
  notification. A gone session keeps one orphan item.
- A session gone 10 minutes passes each PR it owns to a live session that lists it. With no
  such session, the orphan stays. A PR dropped by its owner goes to a live session that lists
  it; if only gone sessions list it, it goes to one of them. The next tick with fresh GitHub
  data then names that PR in the session's orphan (a new line, or added to its automatic
  orphan, which notifies again) if the PR is still open. A PR dropped because it merged
  raises nothing, and an orphan the human cleared stays cleared. The gone transition itself
  still raises its orphan before that tick's GitHub read, as before.
- On every hand-off, including `ovsr.py assign` and retire, the previous owner's reported
  rounds, threads and hold are dropped; drift waits for the new owner's own report.
- A session gone 10 minutes with an open `waiting-human` question keeps it on the dashboard
  as a quiet `gone-question` note ("gone while waiting on you: ..."): below the band, no
  second notification. The note clears when the session reports or reappears, when a PR the
  question names and the session listed merges, closes, or passes to a live session, at
  retire, or by hand.
- Items opened by hand with `ovsr.py attention` are marked manual and never swept by these
  rules. Items saved before the flag existed are marked manual on load unless their id and
  text match what the code writes. The older clears still apply to manual items: a different
  reported status clears `waiting-human`, and retire drops everything the session had.

Notification: `osascript -e 'display notification "<session>: <what>" with title "Overseer"'`.

## Attention item ids

`<session>:<kind>:<pr or ->` where kind is one of `waiting-human`, `waiting`,
`orphan`, `drift-check`, `stalled`, `red`, `threads`, `no-reviewer`,
`ownership-conflict`, `gone-question`. An id notifies once. The same id reopened after being cleared
notifies again.

## Rulings the Overseer may not make

- **Decisions the repo reserves for the human.** If the target repo's agent instructions say the user
  decides something before code changes (kube-agents: post-PR bot findings, fix / push back / defer),
  a session sitting `waiting-human` on it is correct, not stalled. Keep the item on the band with the
  session's recommendation attached; an Overseer "it's your call" does not release it. Verify such
  clauses against the upstream default branch, not a local checkout.
- `ovsr.py clear <id> --snooze` clears an explained item (a stale red on an old merge ref, say) and keeps it quiet while the PR head stays put; the next push drops the snooze and the signal re-raises on its own.

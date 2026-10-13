# Role examples

These are the standing roles one person (the "human" below) ran alongside the Overseer while
driving eight to fifteen Claude Code sessions on one repo.  Each file has a starting prompt you can
paste into a new session (or give to `/loop`), what the role owns and escalates, the permissions it
needed, and the lessons it learned the hard way.

None of these are required.  Start with the Overseer and whichever minder hurts most.  The prompts
are generalized from real ones: replace the `<placeholders>`, and expect to edit them as your own
workflow settles.

| Role | Runs as | One line |
| --- | --- | --- |
| [Overseer](overseer.md) | self-paced `/loop` | tracks every session and PR, keeps the dashboard, relays rules |
| [Planner](planner.md) | event-driven + slow `/loop` | holds program context; ordering, routing, design triage before the human |
| [PR minder](pr-minder.md) | `/loop` ~30m | drives the human's own open PRs to merge |
| [Review minder](review-minder.md) | `/loop` ~30m | reviews PRs where the human is the requested reviewer |
| [Issue sweeper](issue-sweeper.md) | `/loop` ~2h | verifies the human's open issues and dispatches fix agents |
| [Cluster manager](cluster-manager.md) | `/loop` 20-60m | owns shared test clusters: leases, baselines, drift, create/delete |
| [Worktree cleanup](worktree-cleanup.md) | idle, on request | removes finished git worktrees safely, one at a time |
| Task sessions | manual | everything else: one feature or bug each, briefed by the planner |

## Things every role shares

- **Report to the Overseer** on start, on every status change, and before asking the human
  anything (`status: waiting-human: <question>`).  The `working-with-the-overseer` skill in
  `../skills/` teaches the format; `../briefs/minder-report-in.txt` is the sentence to add to a
  loop prompt.
- **Decide and report** (rule 20 in `../docs/rules.md` in the shipped set): when a session has a
  clear recommendation it acts and reports one line, `#N: did <x> (rec; veto by <time>)`.  It
  asks first only for new grants/RBAC, an unprecedented security High, scope changes, or
  outward-facing steps that are hard to reverse.  The human agreed with session recommendations
  about 95% of the time, and the waiting was the real cost.
- **No agent lgtm or approve** on the human's behalf.  Sessions report `lgtm wanted on #N`.
- **Never launder a permission.**  If the classifier or a prompt blocks something in one session,
  no session asks another to do it.  The block goes to the human as an exact command.
- **Slow down, don't stop.**  Loops drop to a check every hour or two when nothing moves, and
  pick back up the moment something does.  Off-hours, waiting on humans is fine; don't nudge
  reviewers at night.
- **Verify against the system, not a summary.**  Every role below lists some version of this
  as its first lesson.

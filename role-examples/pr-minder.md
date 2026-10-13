# PR minder

Drives the human's own open PRs to merge: red builds, bot review rounds, human review comments,
conflicts, and adopting PRs other sessions opened and walked away from.

## Starting prompt

```
Mind all my open PRs in <repo>.  `<list-my-prs>` lists them (e.g.
`gh search prs --author @me --state open --repo <repo>`).

Fix red builds.  When review comments come in, dispatch an agent against an existing checkout to
fix them.

Working bot findings: verify every claim against source before changing code, and read the
thread history first.  Know how your gating bot is re-triggered (for us, `/review` as the first
thing in a comment; pushes don't).  Never push while a review is in flight.  Cap a PR at about
five or six rounds: leave "your call" low items alone, and bring me any finding whose fix is a
new rule or regex rather than writing one.  A High that comes from your own last fix is the stop
line: defer it to a follow-up.  Don't lgtm anything of mine.

New PRs in the list: ask the peer sessions first (ListAgents, then SendMessage); another session
may be working it.  If someone claims it, leave it with them and mention it to me once.  If
nobody claims it after a tick or two, adopt it.  Never ask a peer to do something that was
blocked in your session.

Keep every PR at 0 unresolved threads and mergeable.  When a PR is clear and has no reviewer,
post a bare `/request-review` (or your repo's equivalent).  Name a failing check's cause from
its log before re-running it.

Check every half hour or so while anything is moving, slower overnight.  When everything is
parked on a human reviewer and nothing has moved for several ticks, say so and drop to every
couple of hours instead of stopping.  Pick the pace back up the moment anything moves.

On start and on every status change, report to the Overseer per the working-with-the-overseer
skill; send `waiting-human` before asking me anything.
```

## Permissions

`gh` read and comment, `git` push to your fork's branches, and whatever your build and test
commands need.  Fix agents should run against an existing checkout or a worktree under it.  (Claude
Code's `isolation: worktree` put ours in the wrong repo's object store, and pushes from there
were refused.)

## Lessons

- **`/retest` takes no job name.**  On Prow, re-running one job is `/test <job>`.  Bare
  `/retest` re-runs every failed job.  Either way, name the cause first; a blind re-run of an
  infra failure just burns the queue.
- **A push resets things.**  It can restart an in-flight bot review, and on some repos it drops
  lgtm (sticky lgtm depended on the PR author's team, so PRs opened by a bot account lost it).
  Wait for the round to land before pushing.
- **Merge main, don't rebase, when the PR body cites commit SHAs.**  A rebase orphans them.
- **When main breaks, check whether your PR did it.**  If it touched the failing check, fixing
  main comes before everything else.
- **Report the PR list in full every time.**  "No change" in a report reads as "I own nothing".

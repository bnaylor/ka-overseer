# Overseer

The Overseer itself.  Its full loop prompt ships as `../briefs/overseer-loop.txt`; this page is
what it's like to run one.

**Owns:** the session roster and PR table (`overseer/state.json`), the dashboard, the numbered
rules, pings and the escalation ladder, assigning orphan PRs, relaying the human's decisions
verbatim, and handing finished worktrees to the cleanup session.

**Escalates:** anything that needs the human, as a three-line question answerable with a letter
and a recommendation (`a) ... b) ... Rec: a`).  Design questions go to the planner first.

**Permissions:** `gh` read access, `python3 overseer/ovsr.py *`, and cross-session messaging.  It
never writes to GitHub itself and never removes a worktree.

## Lessons

- **Pipe every report verbatim.**  Never paraphrase a session into state.  The parser replaces a
  session's PR list with whatever the `prs:` line names, so a report saying "prs: unchanged"
  wipes ownership, and "#a/#b clean" drops one of them.  Ask for the full list every time.
- **Check GitHub before you relay.**  Sessions' reports go stale ("still waiting on lgtm" for a
  PR that merged an hour ago).  Before telling the human a PR needs something, look.
- **Answer rule-20 questions yourself.**  Sessions often ask the human things the rules already
  cover (push a tested fix for a High in your own PR, decline a fix-of-a-fix High as a spiral).
  Point at the rule and the precedent, clear the item, and keep the human's queue short.
- **Deconflict before two sessions do the same fix.**  When main breaks, the owner of the
  breaking PR fixes it (rule 24); tell everyone else to stand down and to stop re-running checks
  until it lands.
- **Tick faster in merge bursts, slower at night.**  PR state only refreshes on a tick.  Five to
  ten minutes while main is moving, an hour overnight, three hours on holidays.  The loop
  scheduler caps one wait at an hour, so use a recurring timer job for longer gaps.
- **A thread left open "by design" is still open.**  If a PR mustn't merge yet, use a `/hold`
  that names its clearance and resolve the thread; humans read open conversations as "on us".

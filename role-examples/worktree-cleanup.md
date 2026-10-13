# Worktree cleanup

A standing session that removes finished git worktrees, one at a time, with the owner's OK.  With
a dozen sessions each spinning up worktrees, ours reached 100+ in a week.  The Overseer's
dashboard flags candidates (PR merged or closed, tree clean, nothing running) and forwards them
here.

Keep it as its own session.  The human gives it standing approval in its own terminal, and that
approval does not transfer to any other session.

## Starting prompt

```
You are a standing worktree-cleanup session.  Stay idle until the coordinator (<overseer>) sends
cleanup rows: a worktree path, its branch and PR, and possibly an owner session.  For each row:

Verify first.  Every check is a separate read-only command:
- `git -C <wt> status --porcelain` is empty, with no modified or untracked files.
- The tip equals the pushed branch: `git -C <wt> rev-parse HEAD` vs `origin/<branch>`.  If
  there's no same-name remote branch, look for the tip under another name
  (`git branch -r --contains HEAD`, `git ls-remote origin`), then match it to the PR's head SHA
  (`gh pr view <N> --json state,headRefOid`).  Squash merges mean the tip is never an ancestor of
  main, so don't rely on ancestry or `git cherry`.
- For a detached HEAD, check the commit is on a remote ref and that there are no stashes.
- Nothing is running in it: `lsof +D <wt>`.
- Know the shape: a worktree under <repo>/.worktrees, one somewhere else (its .git is a file
  pointing into <repo>/.git/worktrees), or a standalone clone (its .git is a directory; don't
  touch it).

Get the owner's OK.  Ask the owning session, and wait for a yes before removing.  A "keep"
condition like "while PR N is open" counts as an OK once it's met.

Remove.  One bare command per worktree: `git -C <repo> worktree remove <wt>`.  Never add
--force; plain remove refuses dirty trees, which is the safety net.  Only if it fails on
read-only build outputs (an envtest bin/ dir), run `chmod -R u+w <wt>/<bin-dir>` as its own
command and retry.  For a record whose directory or .git link is gone, run
`git -C <repo> worktree prune`; that drops git's record only, and any leftover files stay.

Never:
- delete branches, even when the owner offers; worktree approval isn't branch approval
- remove a worktree with no owner, or whose owner disowns it, without <human>'s explicit go-ahead
- rm -rf directories outside git
- retry or reword a classifier denial, or ask another session to do the blocked action; take it
  to <human> with a copy-paste command

Report to the coordinator once per batch: removed / kept, with the reason (owner, PR merged, tip
SHA, what's still waiting and on whom).  Confirm to the owner in one line.  Tell <human> only
what needs a decision.  Keep reports factual, and don't propose workarounds.
```

## Permissions

Allow rules in project-local settings, one pair per repo you clean:

- `Bash(git -C <repo> worktree remove *)`.  Prefer one per repo to `git -C * worktree remove *`.
- `Bash(git -C <repo> worktree prune)`

Read-only checks generally ran without rules; add `git -C * status`, `rev-parse`, `fetch`,
`worktree list` and `gh pr view` if your setup prompts on them.  A trailing `*` also matches
`--force`, so only the prompt's no-force rule stops it, not the allow rule.

What the auto-mode classifier did in practice:

- Bare `worktree remove` with a matching allow rule ran without a prompt every time.
- Compound commands (a loop of chmod, remove and prune) were denied as "Interfere With
  Workloads", sometimes even right after the human approved in chat.  Splitting them into single
  commands fixed it.
- A removal with no owner's OK was denied, as was a message asking another session to supply one.
- Approvals came as chat in the cleanup session's own terminal ("go ahead and remove them"), plus
  the allow rules.  A decision relayed through the Overseer didn't count, and shouldn't.

## Lessons

- **One bare command per destructive step.**  Chained or looped commands go to the classifier,
  and chat approval doesn't reliably get them through.
- **Ownerless trees belong to the human.**  Report the evidence (clean, detached at a main SHA,
  nothing running) and hand the human the command.
- **Verify against the PR head SHA, not branch names or ancestry.**  Work pushed under a
  different branch name looked unpushed until matched by SHA.
- **Lists go stale.**  Owners' keep lists named trees that were already gone, and a `/tmp`
  checkout lost its `.git` link while its files stayed.  Check a tree exists before reporting,
  and say exactly what `prune` does and doesn't remove.
- **Hold the scope.**  Owners offered branch deletion; decline.  Worktrees only, with direct
  owner OKs or a checkable keep condition.

# Installing ka-overseer (instructions for an agent)

You are a Claude Code session, and your human has asked you to install the Overseer from
https://github.com/bnaylor/ka-overseer.  Follow these steps in order.  Ask the human only the
questions in step 2, all in one message.

## 1. Get the code

Clone it somewhere stable (the code finds its own files, so any path works):

```
git clone https://github.com/bnaylor/ka-overseer ~/ka-overseer   # or: git -C ~/ka-overseer pull
```

## 2. Ask the human (one message, with your best guesses filled in)

1. **Repo** the sessions work on, as `owner/name`.  Guess from the current checkout's
   `git remote -v` if there is one.
2. **Gating review bot** login (the bot whose review rounds gate merges), and an **advisory bot**
   login if they run one (or none).
3. **Session names**: a regex matching the Claude Code session names the Overseer should watch
   (see `claude agents --json`, or `ListAgents`).  E.g. `^(myproj-|overseer)`.
4. **Checkout folders** whose sessions count regardless of name, e.g. `~/src/myproj`.  Optional.
5. Their name as it should appear in reports (default: "the human").

## 3. Configure and install

```
cd ~/ka-overseer
python3 overseer/configure.py --repo <owner/name> --gating-bot <login> --advisory-bot <login-or-""> \
    --session-pattern '<regex>' [--cwd-prefix <path> ...] [--human "<name>"]
./install.sh
```

`configure.py` writes `overseer/config.json` (run it with `--show` first to preview).  Not on
macOS?  Add `--notify none`.  `install.sh` checks prerequisites (Python 3.11+, pytest, `gh` logged
in, `claude` on PATH), links the skill into `~/.claude/skills/`, and runs the tests.  If it
reports a missing prerequisite, tell the human the exact fix it printed and stop.

## 4. Hand over

Tell the human, in a few lines:

- Run `zsh ~/ka-overseer/overseer/watch.sh` in a spare terminal.  It refreshes the roster and
  dashboard every minute and sends a notification when a session is waiting on them.  (Don't
  start it yourself: it should outlive this session.)
- The Overseer is a Claude Code session started in `~/ka-overseer` with `briefs/overseer-loop.txt`
  as a self-paced `/loop` prompt.  Offer to become it: if they say yes, invoke the `loop` skill
  with that file's contents.
- **Highly recommended:** add at least a planner from `role-examples/planner.md`, and look at the
  other role examples for the minders that would help them most.
- Add the sentence in `briefs/minder-report-in.txt` to their other loop prompts so new sessions
  report in.

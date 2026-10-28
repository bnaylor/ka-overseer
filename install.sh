#!/bin/zsh
# Check prerequisites, symlink the skill into ~/.claude/skills, and run the suite. Idempotent and
# non-interactive, so an agent can run it. Configure first: python3 overseer/configure.py --repo ...
set -eu
here="${0:A:h}"
fail=0

need() { print -u2 "install: $1"; fail=1; }

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null \
  || need "Python 3.11+ is required (found: $(python3 --version 2>&1 || echo none))"
python3 -c 'import pytest' 2>/dev/null \
  || need "pytest is needed for the test suite: python3 -m pip install --user pytest"
command -v gh >/dev/null || need "gh (GitHub CLI) is not on PATH: https://cli.github.com"
command -v gh >/dev/null && { gh auth status >/dev/null 2>&1 || need "gh is not logged in: run 'gh auth login'"; }
command -v claude >/dev/null || need "claude (Claude Code CLI) is not on PATH"
(( fail )) && exit 1

mkdir -p ~/.claude/skills
ln -sfn "$here/skills/working-with-the-overseer" ~/.claude/skills/working-with-the-overseer
echo "skill linked: ~/.claude/skills/working-with-the-overseer -> $here/skills/working-with-the-overseer"
chmod +x "$here/overseer/watch.sh"
python3 -m pytest "$here/overseer/tests" -q

[[ -f "$here/overseer/config.json" ]] \
  || print -u2 "install: warning: no overseer/config.json yet, so the defaults apply (no repo, no review bots). Run configure.py first."
repo=$(python3 -c "import sys; sys.path.insert(0, '$here/overseer'); import config; print(config.CFG['repo'])")
echo
echo "Configured for repo: $repo   (change with: python3 $here/overseer/configure.py --repo owner/name ...)"
echo "Next:"
echo "  1. In a spare terminal: zsh $here/overseer/watch.sh"
echo "  2. Start a Claude Code session in $here and give it briefs/overseer-loop.txt as a self-paced /loop prompt."
echo "  3. Add at least a planner from role-examples/ (highly recommended)."

"""Deployment settings. overseer/config.json (untracked; write it with configure.py, or copy
config.example.json) overrides these defaults; nothing else needs to change for a new team."""
import json
from pathlib import Path

DEFAULTS = {
    "repo": "owner/repo",
    "human": "the human",
    "human_short": "human",
    "session_pattern": "^overseer",  # set yours: a regex over the Claude Code session names to watch
    "cwd_prefixes": [],
    "worktree_roots": [],          # folders whose git clones' worktrees the dashboard lists; empty means cwd_prefixes
    "gating_bot": "",              # login of the review bot whose rounds gate merges; empty = none
    "advisory_bot": "",            # login of an advisory review bot, shown but never capped; empty = none
    "round_cap": 6,
    "ignored_check_contexts": ["tide"],
    "scope_queries": {
        "mine": ["--author", "@me"],
        "review_requested": ["--search", "review-requested:@me"],
        "reviewed": ["--search", "reviewed-by:@me -author:@me"],
    },
    "notify": "osascript",
}


def load(path=None):
    cfg = dict(DEFAULTS)
    p = Path(path) if path else Path(__file__).with_name("config.json")
    if p.exists():
        cfg.update(json.loads(p.read_text()))
    return cfg


CFG = load()

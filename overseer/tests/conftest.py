import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Pin the settings the tests are written against, before any module reads them at import time, so the
# suite neither needs overseer/config.json nor depends on a real deployment's values.
import config  # noqa: E402

TEST_CONFIG = {
    "repo": "example-org/widget",
    "session_pattern": "^(worker-|planner|overseer)",
    "cwd_prefixes": ["/home/dev/src/widget"],
    "worktree_roots": [],
    "gating_bot": "review-bot",
    "advisory_bot": "advisory-bot",
    "round_cap": 6,
    "ignored_check_contexts": ["tide", "pull-widget-smoke-test-next"],
}
config.CFG.clear()
config.CFG.update(config.DEFAULTS, **TEST_CONFIG)


@pytest.fixture(autouse=True)
def _no_real_worktree_scan(monkeypatch):
    """A tick that reads GitHub also scans worktrees; tests must never touch the real folders or gh."""
    import ovsr
    monkeypatch.setattr(ovsr, "refresh_worktrees", lambda state, now: None)


@pytest.fixture(autouse=True)
def _no_real_keep_file(monkeypatch, tmp_path):
    """A deployment's own worktree-keep.json must not change what the worktree tests see."""
    import worktrees
    monkeypatch.setattr(worktrees, "KEEP_FILE", str(tmp_path / "no-keep-file.json"))

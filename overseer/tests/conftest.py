import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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

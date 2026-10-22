# overseer/tests/test_worktrees.py
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import gh_snapshot as G
import ovsr
import render as V
import state as S
import worktrees as W
from ovsr import refresh_worktrees as REAL_REFRESH      # bound before conftest swaps in the stub

NOW = S.now_iso()
OLD = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
URL = "https://github.com/acme/widget.git"


def git(cwd, *args, env=None):
    full = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **(env or {})}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=full).stdout.strip()


def commit(cwd, name, when=None):
    (cwd / name).write_text(name)
    git(cwd, "add", name)
    env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else None
    git(cwd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name, env=env)


def age_index(path):
    """Make a worktree look untouched since OLD (git's setup commands just bumped its index)."""
    index = git(path, "rev-parse", "--git-path", "index")
    index = index if os.path.isabs(index) else str(path / index)
    t = S.parse_iso(OLD).timestamp()
    os.utime(index, (t, t))


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A remote, a main clone under the root, and one extra worktree per case."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "-q", "-b", "main")
    commit(remote, "base", OLD)
    git(remote, "config", "receive.denyCurrentBranch", "ignore")
    root = tmp_path / "ws1"
    root.mkdir()
    main = root / "widget"
    git(tmp_path, "clone", "-q", str(remote), str(main))
    git(main, "config", "remote.origin.url", URL)              # a GitHub URL on record ...
    git(main, "config", f"url.{remote}.insteadOf", URL)         # ... that really talks to the local remote
    elsewhere = tmp_path / "tmp"
    elsewhere.mkdir()

    def add(name, branch=None, where=root):
        path = where / name
        if branch:
            git(main, "worktree", "add", "-q", "-b", branch, str(path), "origin/main")
        else:
            git(main, "worktree", "add", "-q", "--detach", str(path), "origin/main")
        return path

    merged = add("widget-merged", "feat/merged")
    commit(merged, "m", OLD)
    git(merged, "push", "-q", "origin", "feat/merged")
    dirty = add("widget-dirty", "feat/dirty")
    commit(dirty, "d", OLD)
    git(dirty, "push", "-q", "origin", "feat/dirty")
    (dirty / "d").write_text("edited")
    local = add("widget-local", "feat/local")
    commit(local, "l", OLD)
    opened = add("widget-open", "feat/open")
    squashed = add("widget-squashed", "feat/squashed")
    commit(squashed, "s", OLD)
    git(squashed, "push", "-q", "origin", "feat/squashed")
    git(main, "push", "-q", "origin", "--delete", "feat/squashed")    # GitHub deletes the head branch after merge
    scratch = add("scratch", where=elsewhere)                          # detached, outside the root
    gone = add("widget-gone", "feat/gone")
    shutil.rmtree(gone)
    orphan = add("widget-orphan", "feat/orphan")
    shutil.rmtree(main / ".git" / "worktrees" / "widget-orphan")    # git dropped the record; the folder stayed
    for p in (merged, dirty, local, opened, squashed, scratch):
        age_index(p)
    return {"root": root, "main": main, "merged": merged, "dirty": dirty, "local": local, "open": opened,
            "squashed": squashed, "scratch": scratch, "gone": gone, "orphan": orphan}


def fake_prs(ws, table):
    def find(rows):
        found = {}
        for r in rows:
            if r["branch"] in table:
                n, st = table[r["branch"]]
                head = git(ws["squashed"], "rev-parse", "HEAD") if r["branch"] == "feat/squashed" else None
                found[(r["repo"], r["branch"])] = {"repo": r["repo"], "number": n, "state": st,
                                                   "url": f"https://github.com/acme/widget/pull/{n}", "head_oid": head}
        return found, {}
    return find


def by_name(rows):
    return {os.path.basename(r["path"]): r for r in rows}


def test_scan_lists_extra_worktrees_only_and_finds_ones_outside_the_root(ws):
    rows, errors = W.scan([str(ws["root"])])
    names = set(by_name(rows))
    assert errors == []
    assert "widget" not in names                                   # the main folder is never a candidate
    assert names == {"widget-merged", "widget-dirty", "widget-local", "widget-open", "widget-squashed", "scratch", "widget-gone",
                     "widget-orphan"}
    assert by_name(rows)["widget-orphan"]["orphan"] and by_name(rows)["widget-orphan"]["main"] == str(ws["main"])
    r = by_name(rows)
    assert r["widget-merged"]["repo"] == "acme/widget" and r["widget-merged"]["fork_owner"] == "acme"
    assert r["widget-gone"]["missing"] and not r["widget-merged"]["missing"]
    assert r["scratch"]["branch"] is None
    assert r["widget-dirty"]["dirty"] == 1 and r["widget-merged"]["dirty"] == 0
    assert r["widget-local"]["unpushed"] == 1 and r["widget-merged"]["unpushed"] == 0
    assert r["widget-squashed"]["unpushed"] == 1                   # remote branch deleted: looks unpushed until the PR head says otherwise


def test_scan_does_not_count_as_activity(ws):
    W.scan([str(ws["root"])])
    rows, _ = W.scan([str(ws["root"])])
    assert by_name(rows)["widget-merged"]["last_activity"] == OLD  # status ran twice and did not touch the index


def test_refresh_classifies_every_case(ws):
    state = S.empty_state()
    find = fake_prs(ws, {"feat/merged": (1, "MERGED"), "feat/dirty": (2, "MERGED"), "feat/open": (3, "OPEN"),
                         "feat/squashed": (4, "MERGED")})
    rows = by_name(W.refresh(state, NOW, find, roots=[str(ws["root"])]))
    assert rows["widget-merged"]["status"] == "cleanup" and rows["widget-merged"]["why"] == "PR #1 merged; nothing unsaved"
    assert rows["widget-dirty"]["status"] == "check" and "1 uncommitted change" in rows["widget-dirty"]["why"]
    assert rows["widget-local"]["status"] == "check" and "1 unpushed commit" in rows["widget-local"]["why"]
    assert rows["widget-open"]["status"] == "in use"
    assert rows["widget-squashed"]["status"] == "cleanup" and rows["widget-squashed"]["unpushed"] == 0
    assert rows["scratch"]["status"] == "cleanup" and rows["scratch"]["why"].startswith("detached, no PR, idle")
    assert rows["widget-gone"]["status"] == "missing"
    assert rows["widget-orphan"]["status"] == "check" and "no longer tracks" in rows["widget-orphan"]["why"]
    order = [r["status"] for r in state["worktrees"]["rows"]]
    assert order == sorted(order, key=W.STATUS_ORDER.index)
    assert state["worktrees"]["scanned"] == NOW


def test_recent_work_without_a_pr_is_in_use(ws):
    commit(ws["local"], "fresh")                                   # a commit right now
    rows = by_name(W.refresh(S.empty_state(), NOW, fake_prs(ws, {}), roots=[str(ws["root"])]))
    assert rows["widget-local"]["status"] == "in use"


def row(**kw):
    base = {"path": "/w/a", "main": "/w/m", "repo": "acme/widget", "branch": "b", "head": "abc", "locked": None,
            "missing": False, "dirty": 0, "unpushed": 0, "last_activity": OLD, "error": None, "pr": None, "pr_error": None}
    return {**base, **kw}


def test_classify_edges():
    pr = lambda st: {"number": 9, "state": st}
    assert W.classify(row(pr=pr("CLOSED")), NOW) == ("cleanup", "PR #9 closed; nothing unsaved")
    assert W.classify(row(pr=pr("MERGED"), locked="in use by ci"), NOW)[0] == "check"
    assert W.classify(row(pr=pr("OPEN"), dirty=3), NOW) == ("in use", "PR #9 open")
    assert W.classify(row(pr=pr("MERGED"), last_activity=NOW), NOW)[0] == "cleanup"     # merged and clean: recent work does not matter
    assert W.classify(row(pr_error="timeout"), NOW)[0] == "unknown"                     # never call it clean on a failed lookup
    assert W.classify(row(error="bad object"), NOW)[0] == "unknown"
    assert W.classify(row(last_activity=None), NOW) == ("in use", "no PR yet")


def test_refresh_keeps_the_last_known_pr_when_github_fails(ws):
    state = S.empty_state()
    W.refresh(state, NOW, fake_prs(ws, {"feat/merged": (1, "MERGED")}), roots=[str(ws["root"])])
    failing = lambda rows: ({}, {"acme/widget": "connection reset"})
    rows = by_name(W.refresh(state, NOW, failing, roots=[str(ws["root"])]))
    assert rows["widget-merged"]["status"] == "cleanup"            # last tick's answer stands
    assert rows["widget-local"]["status"] == "unknown"             # nothing known before either
    assert state["worktrees"]["errors"] == ["acme/widget: connection reset"]


def test_owner_prefers_the_pr_owner_then_the_deepest_live_folder():
    s = S.empty_state()
    s["overseer"]["session"] = "overseer"
    for name, cwd in (("ws5", "/u/ws5"), ("overseer", "/u/ws5"), ("deep", "/u/ws5/widget-x"), ("other", "/u/ws4")):
        S.session(s, name).update(cwd=cwd, roster_status="idle")
    S.pr(s, 7).update(owner="other")
    r = row(path="/u/ws5/widget-x", main="/u/ws5/widget", pr={"repo": W.CFG["repo"], "number": 7, "state": "OPEN"})
    assert W.owner_of(s, r) == "other"
    assert W.owner_of(s, row(path="/u/ws5/widget-x", main="/u/ws5/widget")) == "deep"
    assert W.owner_of(s, row(path="/tmp/scratch", main="/u/ws5/widget")) == "ws5"            # same folder: not the Overseer
    s["sessions"]["ws5"].update(roster_status="gone", gone_since=OLD)
    assert W.owner_of(s, row(path="/tmp/scratch", main="/u/ws5/widget")) == "overseer"       # the live one wins
    assert W.owner_of(s, row(path="/elsewhere/x", main="/elsewhere/m")) is None


def test_branch_prs_matches_branch_and_fork_owner():
    calls = []

    def run(cmd):
        calls.append(cmd)
        if "--author" in cmd:
            return json.dumps([
                {"number": 1, "state": "MERGED", "headRefName": "a", "headRefOid": "aaa", "url": "u1",
                 "headRepositoryOwner": {"login": "Me"}, "updatedAt": "2026-10-01T00:00:00Z"},
                {"number": 2, "state": "OPEN", "headRefName": "a", "headRefOid": "aa2", "url": "u2",
                 "headRepositoryOwner": {"login": "me"}, "updatedAt": "2026-09-01T00:00:00Z"},
                {"number": 3, "state": "OPEN", "headRefName": "b", "headRefOid": "bbb", "url": "u3",
                 "headRepositoryOwner": {"login": "someone-else"}, "updatedAt": "2026-10-01T00:00:00Z"}])
        if cmd[cmd.index("--head") + 1] == "c":
            return json.dumps([{"number": 4, "state": "CLOSED", "headRefName": "c", "headRefOid": "ccc", "url": "u4",
                                "headRepositoryOwner": {"login": "me"}}])
        raise RuntimeError("HTTP 502")

    rows = [{"repo": "o/r", "branch": b, "fork_owner": "me"} for b in ("a", "b", "c", "d")]
    found, errors = G.branch_prs(rows, run=run)
    assert found[("o/r", "a")]["number"] == 2                     # the open one beats a newer merged one
    assert found[("o/r", "c")] == {"repo": "o/r", "number": 4, "state": "CLOSED", "url": "u4", "head_oid": "ccc"}
    assert ("o/r", "b") not in found                              # same branch name on another fork is not ours
    assert errors == {("o/r", "b"): "HTTP 502", ("o/r", "d"): "HTTP 502"}
    assert sum("--author" in c for c in calls) == 1


def test_branch_prs_list_failure_marks_the_repo():
    def run(cmd):
        raise RuntimeError("auth required")
    found, errors = G.branch_prs([{"repo": "o/r", "branch": "a", "fork_owner": "me"}], run=run)
    assert found == {} and errors == {"o/r": "auth required"}


def test_render_worktrees_section():
    s = S.empty_state()
    assert "Not scanned yet" in V.render(s, None, NOW)
    S.session(s, "ws2").update(roster_status="idle")
    S.session(s, "ws3").update(roster_status="gone", gone_since=OLD)
    home = os.path.expanduser("~")
    s["worktrees"] = {"scanned": NOW, "errors": ["o/r: HTTP 502"], "rows": [
        dict(row(path=f"{home}/ws2/widget-1", main=f"{home}/ws2/widget", pr={"number": 5, "state": "MERGED", "url": "https://x/pull/5"}),
             owner="ws3", status="cleanup", why="PR #5 merged; nothing unsaved"),
        dict(row(path="/tmp/gone", branch=None, head="deadbeefcafe"), owner="ws2", status="missing", why="folder no longer exists"),
        dict(row(dirty=2, unpushed=1), owner=None, status="check", why="no PR, idle 9d; 2 uncommitted changes")]}
    html = V.render(s, None, NOW)
    assert "<h2>Worktrees</h2>" in html and "1 clean up · 1 missing · 1 check" in html
    assert "~/ws2/widget-1" in html and '<a href="https://x/pull/5">#5</a> merged' in html
    assert "ws3 (gone)" in html and "detached deadbeef" in html and "2 changed, 1 unpushed" in html
    assert f"git -C {home}/ws2/widget worktree remove {home}/ws2/widget-1" in html
    assert "git -C /w/m worktree prune" in html
    assert "o/r: HTTP 502" in html


def test_tick_scans_worktrees_and_reports_the_cleanup_count(tmp_path, monkeypatch, capsys):
    (tmp_path / "docs").mkdir()
    d = tmp_path / "overseer"
    d.mkdir()
    monkeypatch.setattr(ovsr, "notify", lambda title, text: None)
    monkeypatch.setattr(ovsr, "_roster", lambda p: [])
    monkeypatch.setattr(ovsr.G, "scope_numbers", lambda state: [])
    monkeypatch.setattr(ovsr.G, "snapshot", lambda state, numbers, now: {"errors": {}})
    seen = []

    def fake(state, now):
        seen.append(now)
        state["worktrees"] = {"scanned": now, "errors": [], "rows": [dict(row(), status="cleanup", why="x", owner=None)]}
    monkeypatch.setattr(ovsr, "refresh_worktrees", fake)
    assert ovsr.main(["--dir", str(d), "tick", "--no-gh"]) == 0
    assert seen == []                                              # no GitHub, no scan
    capsys.readouterr()
    assert ovsr.main(["--dir", str(d), "tick"]) == 0
    assert len(seen) == 1
    assert "1 worktree to clean up" in json.loads(capsys.readouterr().out)["summary"][-1]
    assert "<h2>Worktrees</h2>" in (d / "dashboard.html").read_text()


def test_a_failed_scan_never_fails_the_tick(monkeypatch):
    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(ovsr.W, "refresh", boom)
    state = S.empty_state()
    state["worktrees"] = {"scanned": OLD, "rows": [{"path": "/x"}], "errors": []}
    REAL_REFRESH(state, NOW)
    assert state["worktrees"]["rows"] == [{"path": "/x"}] and state["worktrees"]["errors"] == ["scan failed: disk gone"]


def test_roster_cwd_lands_on_the_session_and_old_state_loads(tmp_path):
    s = S.empty_state()
    S.apply_roster(s, [{"name": "ws1", "status": "idle", "kind": "interactive", "started_at": None, "cwd": "/u/ws1"}], NOW)
    assert s["sessions"]["ws1"]["cwd"] == "/u/ws1"
    old = tmp_path / "state.json"
    old.write_text(json.dumps({"sessions": {"ws1": {"role": "task"}}, "prs": {}, "attention": []}))
    loaded = S.load_state(old)
    assert loaded["worktrees"] is None and loaded["sessions"]["ws1"]["cwd"] is None


def test_gitignored_files_outside_caches_block_cleanup(ws):
    (ws["merged"] / ".gitignore").write_text("install.env\n__pycache__/\n*.pyc\n")
    git(ws["merged"], "add", ".gitignore")
    git(ws["merged"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ignore", env={"GIT_AUTHOR_DATE": OLD, "GIT_COMMITTER_DATE": OLD})
    git(ws["merged"], "push", "-q", "origin", "feat/merged")
    (ws["merged"] / "__pycache__").mkdir()
    (ws["merged"] / "__pycache__" / "x.pyc").write_text("cache")
    find = fake_prs(ws, {"feat/merged": (1, "MERGED")})
    rows = by_name(W.refresh(S.empty_state(), NOW, find, roots=[str(ws["root"])]))
    assert rows["widget-merged"]["status"] == "cleanup"            # a tool cache alone is not work
    (ws["merged"] / "install.env").write_text("TOKEN=secret")
    rows = by_name(W.refresh(S.empty_state(), NOW, find, roots=[str(ws["root"])]))
    r = rows["widget-merged"]
    assert r["status"] == "check" and r["ignored"] == 1
    assert "1 gitignored file only here (install.env)" in r["why"]


def test_repo_config_cannot_hide_untracked_or_assume_unchanged_work(ws):
    find = fake_prs(ws, {"feat/merged": (1, "MERGED")})
    git(ws["merged"], "config", "status.showUntrackedFiles", "no")
    (ws["merged"] / "notes.txt").write_text("draft")
    assert by_name(W.refresh(S.empty_state(), NOW, find, roots=[str(ws["root"])]))["widget-merged"]["status"] == "check"
    (ws["merged"] / "notes.txt").unlink()
    git(ws["merged"], "update-index", "--assume-unchanged", "m")
    (ws["merged"] / "m").write_text("edited where status cannot see it")
    assert by_name(W.refresh(S.empty_state(), NOW, find, roots=[str(ws["root"])]))["widget-merged"]["status"] == "check"


def test_a_detached_worktree_counts_commits_it_left_behind(ws):
    commit(ws["scratch"], "experiment", OLD)
    git(ws["scratch"], "checkout", "-q", "origin/main")              # git only warns; the reflog is all that reaches it
    age_index(ws["scratch"])
    r = by_name(W.refresh(S.empty_state(), NOW, fake_prs(ws, {}), roots=[str(ws["root"])]))["scratch"]
    assert r["unpushed"] == 1 and r["status"] == "check"


def test_new_commits_after_a_merged_pr_are_not_cleanup(ws):
    head_at_merge = git(ws["merged"], "rev-parse", "HEAD")

    def find(rows):
        return {(r["repo"], r["branch"]): {"repo": r["repo"], "number": 1, "state": "MERGED", "url": None,
                                          "head_oid": head_at_merge} for r in rows if r["branch"] == "feat/merged"}, {}
    commit(ws["merged"], "follow-up")                                # fresh work, pushed, for a PR not opened yet
    git(ws["merged"], "push", "-q", "origin", "feat/merged")
    r = by_name(W.refresh(S.empty_state(), NOW, find, roots=[str(ws["root"])]))["widget-merged"]
    assert r["beyond_pr"] == 1 and r["status"] == "in use"


def test_a_live_session_inside_the_folder_keeps_it_in_use(ws):
    s = S.empty_state()
    S.session(s, "ws1-wt").update(cwd=str(ws["merged"] / "sub"), roster_status="busy")
    find = fake_prs(ws, {"feat/merged": (1, "MERGED")})
    r = by_name(W.refresh(s, NOW, find, roots=[str(ws["root"])]))["widget-merged"]
    assert r["status"] == "in use" and r["why"] == "ws1-wt is working in this folder"


def test_edits_deep_in_a_new_folder_count_as_activity(ws):
    deep = ws["scratch"] / "newpkg" / "inner"
    deep.mkdir(parents=True)
    (deep / "ünï.py").write_text("x = 1")
    old = S.parse_iso(OLD).timestamp()
    for folder in (deep, deep.parent):                               # the folders predate the edit inside them
        os.utime(folder, (old, old))
    r = by_name(W.refresh(S.empty_state(), NOW, fake_prs(ws, {}), roots=[str(ws["root"])]))["scratch"]
    assert r["last_activity"] > OLD and r["status"] == "in use"


def test_a_non_github_remote_is_unknown_not_no_pr(ws):
    git(ws["main"], "config", "remote.origin.url", "https://gitlab.example.com/acme/widget.git")
    r = by_name(W.refresh(S.empty_state(), NOW, fake_prs(ws, {}), roots=[str(ws["root"])]))["widget-local"]
    assert r["status"] == "unknown" and "not a GitHub repo" in r["why"]


def test_parse_status_z_skips_rename_sources():
    text = "R  new name.txt\0old name.txt\0?? dir/ü.py\0!! .env\0"
    assert W.parse_status_z(text) == [("R ", "new name.txt"), ("??", "dir/ü.py"), ("!!", ".env")]


def test_a_kept_worktree_is_never_offered_for_removal(ws, tmp_path):
    keep = tmp_path / "keep.json"
    keep.write_text(json.dumps({str(ws["root"] / "widget-merged"): "durable checkout",
                                str(ws["root"] / "widget-dirty"): "owner keeps it"}))
    find = fake_prs(ws, {"feat/merged": (1, "MERGED"), "feat/dirty": (2, "MERGED")})
    rows = by_name(W.refresh(S.empty_state(), NOW, find, roots=[str(ws["root"])], keep_path=str(keep)))
    assert rows["widget-merged"]["status"] == "in use" and rows["widget-merged"]["why"] == "kept: durable checkout"
    assert rows["widget-dirty"]["status"] == "in use"              # a check row is kept too
    assert rows["scratch"]["status"] == "cleanup"                  # unlisted rows are unchanged


def test_the_example_keep_file_parses():
    keep = W.load_keep(os.path.join(os.path.dirname(W.__file__), "worktree-keep.example.json"))
    assert keep and all(os.path.isabs(k) for k in keep)

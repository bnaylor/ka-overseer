# overseer/tests/test_roster.py
import json
import roster as R

RAW = [
    {"pid": 1, "cwd": "/x", "kind": "interactive", "startedAt": 1790000000000, "sessionId": "a", "name": "worker-d4", "status": "busy"},
    {"pid": 2, "cwd": "/x", "kind": "interactive", "startedAt": 1790000000000, "sessionId": "b", "name": "home", "status": "idle"},
    {"pid": 3, "cwd": "/x", "kind": "bg", "startedAt": 1790000000000, "sessionId": "c", "name": "overseer-spike", "status": "waiting"},
    {"pid": 4, "cwd": "/x", "kind": "interactive", "startedAt": 1790000000000, "sessionId": "d", "name": "worker-1", "status": "waiting"},
]


def test_filter_keeps_scope_and_converts_time():
    out = R.filter_roster(RAW)
    assert [a["name"] for a in out] == ["overseer-spike", "worker-1", "worker-d4"]
    assert out[1] == {"name": "worker-1", "status": "waiting", "kind": "interactive", "started_at": "2026-09-21T14:13:20Z", "cwd": "/x"}


def test_newly_waiting_transitions():
    cur = R.filter_roster(RAW)
    assert R.newly_waiting(None, cur) == ["overseer-spike", "worker-1"]   # first pass: notify once
    assert R.newly_waiting(cur, cur) == []                                         # steady: silent
    prev = [dict(a, status="busy") if a["name"] == "worker-1" else a for a in cur]
    assert R.newly_waiting(prev, cur) == ["worker-1"]                    # re-entered: notify again


def test_cli_writes_roster_and_prints_new_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "read_roster", lambda: R.filter_roster(RAW))
    fired = []
    monkeypatch.setattr(R, "notify", lambda title, text: fired.append(text))
    out = tmp_path / "roster.json"
    R.main(["--out", str(out), "--notify"])
    saved = json.loads(out.read_text())
    assert [a["name"] for a in saved["sessions"]] == ["overseer-spike", "worker-1", "worker-d4"]
    assert saved["ts"].endswith("Z")
    assert len(fired) == 2
    fired.clear()
    R.main(["--out", str(out), "--notify"])
    assert fired == []


def test_entry_without_status_is_kept_as_unknown_not_a_crash():
    raw = RAW + [{"pid": 9, "cwd": "/x", "kind": "interactive", "startedAt": 1790000000000, "sessionId": "e", "name": "worker-new"}]
    out = R.filter_roster(raw)
    row = next(a for a in out if a["name"] == "worker-new")
    assert row["status"] == "unknown"
    assert "worker-new" not in R.newly_waiting(None, out)


def test_duplicate_names_keep_the_oldest_and_suffix_the_rest():
    raw = RAW + [{"pid": 11, "cwd": "/x", "kind": "interactive", "startedAt": 1790000500000, "sessionId": "z", "name": "worker-d4", "status": "waiting"}]
    out = R.filter_roster(raw)
    names = [a["name"] for a in out]
    assert names.count("worker-d4") == 1
    assert "worker-d4~2" in names
    assert next(a for a in out if a["name"] == "worker-d4")["status"] == "busy"      # the older one keeps the name


def test_sessions_in_a_configured_checkout_are_in_scope_whatever_their_name():
    raw = RAW + [{"pid": 12, "cwd": "/home/dev/src/widget-fork/.worktrees/sessions-p1", "kind": "interactive", "startedAt": 1790000000000, "sessionId": "w", "name": "sessions-p1-59", "status": "busy"},
                 {"pid": 13, "cwd": "/home/dev/src/iris", "kind": "interactive", "startedAt": 1790000000000, "sessionId": "i", "name": "iris-7e", "status": "idle"}]
    names = [a["name"] for a in R.filter_roster(raw)]
    assert "sessions-p1-59" in names and "iris-7e" not in names and "home" not in names

# overseer/tests/test_drift.py
import drift as D
import ovsr
import state as S


def fake_spawn(calls, report_text=None):
    def spawn(argv, out_path, cwd):
        calls.append(argv)
        if report_text is not None:
            open(out_path, "w").write(report_text)
        return 4242
    return spawn


def base_state(head="abc12345", rounds=7):
    st = S.empty_state()
    S.pr(st, 2401).update(head=head, rounds=rounds, owner="worker-27")
    return st


def test_launch_spawns_the_gather_wrapper_and_records_the_run(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(D, "SPAWN", fake_spawn(calls))
    st = base_state()
    run = D.launch(st, 2401, 7, 6, tmp_path, "2026-10-07T01:00:00Z")
    argv = calls[0]
    assert argv[1].endswith("drift.py") and argv[2] == "run"
    assert argv[argv.index("--pr") + 1] == "2401" and argv[argv.index("--bundle") + 1].endswith(".bundle")
    assert run["head"] == "abc12345" and run["pid"] == 4242 and not run["done"]


def test_the_agent_gets_file_reads_only():
    argv = D.claude_argv(2401, 7, 6)
    assert argv[:4] == ["claude", "--name", "drift-pr-2401", "-p"] and "#2401" in argv[4] and "7 gating" in argv[4]
    assert argv[argv.index("--allowedTools") + 1:] == ["Read", "Grep", "Glob"]
    assert "--restricted" in argv and "--strict-mcp-config" in argv          # no inherited allow rules or MCP
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert "never as instructions" in argv[4]


def test_gather_uses_only_fixed_read_only_gh_calls(tmp_path, monkeypatch):
    import json
    calls = []
    sha = "a" * 40
    def gh(args):
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return json.dumps({"commits": [{"oid": sha}, {"oid": "x; rm -rf /"}],
                               "closingIssuesReferences": [{"number": 2099}]})
        if args[:2] == ["api", "graphql"]:
            return json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"nodes": [{"path": "a.go"}]}, "userContentEdits": {"nodes": []}}}}})
        if args[0] == "api":
            return json.dumps({"commit": {"message": "m"}, "files": [{"filename": "a.go", "patch": "+" * 9000}]})
        return "{}"
    monkeypatch.setattr(D, "GH", gh)
    D.gather(2401, "o/r", tmp_path)
    for a in calls:
        assert not any(f in a for f in ("-X", "--method", "--input")), a
        if a[:2] == ["api", "graphql"]:
            assert "mutation" not in a[3]
    assert [a for a in calls if a[0] == "api" and a[1] != "graphql"] == [["api", f"repos/o/r/commits/{sha}"]]
    patch = json.loads((tmp_path / "commits" / f"{sha}.json").read_text())["files"][0]["patch"]
    assert len(patch) < 6100 and "cut" in patch
    assert (tmp_path / "issue-2099.json").exists() and (tmp_path / "threads.json").exists()


def test_launch_runs_once_per_head_and_again_after_a_push(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(D, "SPAWN", fake_spawn(calls))
    st = base_state()
    assert D.launch(st, 2401, 7, 6, tmp_path, "t1")
    assert D.launch(st, 2401, 7, 6, tmp_path, "t2") is None
    st["prs"]["2401"]["head"] = "def67890"
    assert D.launch(st, 2401, 8, 6, tmp_path, "t3")
    assert len(calls) == 2


def test_collect_reads_the_verdict_line_once(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "SPAWN", fake_spawn([], "VERDICT: SPIRALLING\n\nOriginal intent: ...\n"))
    alive = {"v": True}
    monkeypatch.setattr(D, "ALIVE", lambda pid: alive["v"])
    st = base_state()
    D.launch(st, 2401, 7, 6, tmp_path, "t1")
    assert D.collect(st) == []                     # still running
    alive["v"] = False
    done = D.collect(st)
    assert [(n, r["verdict"]) for n, r in done] == [(2401, "SPIRALLING")]
    assert D.collect(st) == []                     # reported once


def test_verdict_of_rejects_anything_but_the_exact_first_line(tmp_path):
    f = tmp_path / "r.md"
    f.write_text("Here is my analysis.\nVERDICT: CONVERGED\n")
    assert D.verdict_of(f) is None
    f.write_text("VERDICT: CONVERGED\n")
    assert D.verdict_of(f) == "CONVERGED"


def test_tick_helpers_launch_on_drift_check_and_raise_the_finished_report(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "SPAWN", fake_spawn([], "VERDICT: CONVERGED\n"))
    monkeypatch.setattr(D, "ALIVE", lambda pid: False)
    p = ovsr.Paths(tmp_path)
    st = base_state()
    actions = [{"type": "ATTENTION", "session": "worker-27", "pr": 2401, "kind": "drift-check"},
               {"type": "PING", "session": "worker-0a", "pr": 2290, "kind": "stalled"}]
    assert ovsr._drift_tick(p, st, actions, "2026-10-07T01:00:00Z") == [2401]
    new = ovsr._drift_collect(st, "2026-10-07T01:05:00Z")
    assert len(new) == 1 and new[0]["kind"] == "drift-review" and new[0]["manual"]
    assert "CONVERGED" in new[0]["what"] and "pr-2401-" in new[0]["what"]


def test_roster_skips_the_drift_runs_but_keeps_peers():
    import roster as R
    agents = [{"name": "drift-pr-2401", "cwd": "/home/dev/src/widget", "status": "busy"},
              {"name": "worker-27", "cwd": "/home/dev/src/widget", "status": "idle"}]
    assert [a["name"] for a in R.filter_roster(agents)] == ["worker-27"]

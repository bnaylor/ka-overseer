# overseer/tests/test_gh_snapshot.py
import json
from pathlib import Path

import gh_snapshot as G
import state as S

FIX = json.loads((Path(__file__).parent / "fixtures" / "pr2077.json").read_text())
T0 = "2026-10-01T20:00:00Z"


def test_checks_state():
    assert G.checks_state([]) == "none"
    assert G.checks_state([{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]) == "green"
    assert G.checks_state([{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"},
                           {"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None}]) == "red"
    assert G.checks_state([{"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": None}]) == "pending"
    assert G.checks_state([{"__typename": "StatusContext", "state": "SUCCESS"}]) == "green"
    assert G.checks_state([{"__typename": "StatusContext", "state": "PENDING"}]) == "pending"
    assert G.checks_state([{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SKIPPED"},
                           {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "NEUTRAL"}]) == "green"


def test_classify_real_pr():
    row = G.classify_pr(FIX, unresolved=0)
    assert row["title"].startswith("fix(operator)")
    assert row["head"] == FIX["headRefOid"][:8]
    assert row["branch"] == "fix/tasks-budget-reads-bridge-concurrency"
    assert row["rounds"] == len({r["commit"]["oid"] for r in FIX["reviews"] if r["author"]["login"] == "kube-agents-bot"})
    assert row["advisory_rounds"] == len({r["commit"]["oid"] for r in FIX["reviews"] if r["author"]["login"] == "kyber775"})
    labels = {l["name"] for l in FIX["labels"]}
    assert row["approved"] is ("approved" in labels) and row["lgtm"] is ("lgtm" in labels) and row["hold"] is ("do-not-merge/hold" in labels)
    assert row["reviewers"] == [r.get("login") or r.get("name") for r in FIX["reviewRequests"]]
    assert row["unresolved_threads"] == 0
    assert row["checks"] in ("green", "pending")      # frozen mid Tide-retest; the roll-up logic has its own tests
    assert row["state"] == "OPEN" and row["draft"] is False
    assert row["updated_at"] == FIX["updatedAt"]


def test_labels_drive_hold_lgtm_approved():
    pr = dict(FIX, labels=[{"name": "lgtm"}, {"name": "do-not-merge/hold"}])
    row = G.classify_pr(pr, 0)
    assert row["lgtm"] is True and row["hold"] is True and row["approved"] is False


def test_compute_drift():
    assert G.compute_drift({"rounds": 5, "unresolved_threads": 0, "hold": False, "owner_belief": None}) is None
    assert G.compute_drift({"rounds": 5, "unresolved_threads": 0, "hold": False,
                            "owner_belief": {"rounds": 5, "threads": 0, "hold": False}}) is None
    d = G.compute_drift({"rounds": 6, "unresolved_threads": 2, "hold": False,
                         "owner_belief": {"rounds": 5, "threads": 0, "hold": None}})
    assert "rounds 5" in d and "GitHub 6" in d and "threads 0" in d and "GitHub 2" in d
    assert G.compute_drift({"rounds": 6, "unresolved_threads": 2, "hold": True,
                            "owner_belief": {"rounds": None, "threads": None, "hold": False}}) == "owner says no hold, GitHub has hold"


def test_snapshot_merges_and_survives_a_failed_fetch():
    s = S.empty_state()
    S.pr(s, 2077)["owner"] = "kube-agents-vamp-54"
    S.pr(s, 2077)["owner_belief"] = {"rounds": 5, "threads": 0, "hold": False}
    S.pr(s, 9999)["title"] = "old title"

    def fetch_pr(n):
        if n == 9999:
            raise RuntimeError("HTTP 502")
        return FIX

    result = G.snapshot(s, {2077, 9999}, fetch_pr, lambda n: 0, T0)
    assert result["updated"] == [2077] and list(result["errors"]) == [9999]
    assert s["prs"]["2077"]["owner"] == "kube-agents-vamp-54"
    assert s["prs"]["2077"]["rounds"] == G.classify_pr(FIX, 0)["rounds"]
    assert s["prs"]["2077"]["snapshot_error"] is None
    assert s["prs"]["9999"]["title"] == "old title"
    assert s["prs"]["9999"]["snapshot_error"] == "HTTP 502"
    d = s["prs"]["2077"]["drift"]
    assert d is None if s["prs"]["2077"]["rounds"] == 5 else "GitHub" in d


def test_scope_numbers_unions_lists_and_state():
    s = S.empty_state()
    S.pr(s, 5)
    cmds = {"prs": lambda: [5, 6], "reviews": lambda: [7], "reviewed": lambda: []}
    assert G.scope_numbers(s, cmds) == {5, 6, 7}


def test_tide_context_is_merge_pool_state_not_a_check():
    assert G.checks_state([{"__typename": "StatusContext", "context": "tide", "state": "PENDING"},
                           {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}]) == "green"
    assert G.checks_state([{"__typename": "StatusContext", "context": "tide", "state": "PENDING"}]) == "none"


def test_snapshot_keeps_previous_rounds_for_delta_detection():
    s = S.empty_state()
    G.snapshot(s, {2077}, lambda n: FIX, lambda n: 0, T0)
    assert s["prs"]["2077"]["rounds_prev"] is None
    G.snapshot(s, {2077}, lambda n: FIX, lambda n: 0, T0)
    assert s["prs"]["2077"]["rounds_prev"] == s["prs"]["2077"]["rounds"]


def test_fetch_unresolved_paginates_past_100_threads():
    pages = [
        {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"isResolved": True}] * 99 + [{"isResolved": False}],
                                                                   "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}}}},
        {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"isResolved": False}, {"isResolved": False}],
                                                                   "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}},
    ]
    seen = []
    def graphql(variables):
        seen.append(variables.get("after"))
        return pages.pop(0)
    assert G.fetch_unresolved(2056, graphql=graphql) == 3
    assert seen == [None, "c1"]


def test_superseded_check_runs_do_not_count():
    rollup = [{"__typename": "CheckRun", "name": "classify", "status": "COMPLETED", "conclusion": "CANCELLED", "startedAt": "2026-10-01T18:47:10Z"},
              {"__typename": "CheckRun", "name": "classify", "status": "COMPLETED", "conclusion": "SUCCESS", "startedAt": "2026-10-01T18:51:20Z"}]
    assert G.checks_state(rollup) == "green"
    assert G.checks_state(list(reversed(rollup))) == "green"        # order in the roll-up does not matter
    rollup[1]["conclusion"] = "FAILURE"
    assert G.checks_state(rollup) == "red"                            # the latest run is what counts


def test_last_activity_is_the_latest_real_event_not_updated_at():
    pr = dict(FIX, updatedAt="2026-10-02T00:00:00Z")                  # updatedAt bumped by nothing visible
    row = G.classify_pr(pr, 0)
    expected = max([c["committedDate"] for c in FIX["commits"]] + [c["createdAt"] for c in FIX["comments"]]
                   + [r["submittedAt"] for r in FIX["reviews"]])
    assert row["last_activity"] == expected
    assert row["updated_at"] == "2026-10-02T00:00:00Z"
    bare = G.classify_pr({"updatedAt": "2026-10-01T00:00:00Z"}, 0)   # nothing but updatedAt: fall back to it
    assert bare["last_activity"] == "2026-10-01T00:00:00Z"


def test_optional_next_lane_does_not_make_a_pr_red():
    rollup = [{"__typename": "StatusContext", "context": "pull-kube-agents-smoke-test-next", "state": "FAILURE"},
              {"__typename": "StatusContext", "context": "pull-kube-agents-smoke-test", "state": "SUCCESS"}]
    assert G.checks_state(rollup) == "green"


def test_rounds_count_distinct_commits_not_duplicate_submissions():
    pr = dict(FIX)
    bot = [r for r in FIX["reviews"] if r["author"]["login"] == "kube-agents-bot"]
    pr["reviews"] = FIX["reviews"] + [dict(bot[0])]          # the bot submitted twice on the same commit
    assert G.classify_pr(pr, 0)["rounds"] == G.classify_pr(FIX, 0)["rounds"]
    assert G.classify_pr(FIX, 0)["rounds"] == len({r["commit"]["oid"] for r in bot})


def test_scope_numbers_marks_which_search_found_each_pr():
    s = S.empty_state()
    S.pr(s, 5)["scopes"] = ["reviewed"]                  # found by a search last tick, not this one
    S.pr(s, 9)["scopes"] = ["review_requested"]
    def broken():
        raise RuntimeError("HTTP 502")
    cmds = {"mine": lambda: [6], "review_requested": broken, "reviewed": lambda: [7]}
    assert G.scope_numbers(s, cmds) == {5, 6, 7, 9}
    assert s["prs"]["6"]["scopes"] == ["mine"] and s["prs"]["7"]["scopes"] == ["reviewed"]
    assert s["prs"]["5"]["scopes"] == []                 # dropped out of the search: no longer in that group
    assert s["prs"]["9"]["scopes"] == ["review_requested"]   # that search failed: keep last tick's answer
    assert S.pr(S.empty_state(), 1)["scopes"] is None        # never searched: not the same as found by none


def test_snapshot_rereads_an_unknown_mergeable_once_after_a_wait():
    s = S.empty_state()
    reads, waits = {"n": 0}, []

    def fetch_pr(n):
        reads["n"] += 1
        return {**FIX, "state": "OPEN", "mergeable": "UNKNOWN" if reads["n"] == 1 else "CONFLICTING"}

    G.snapshot(s, {2077}, fetch_pr, lambda n: 0, T0, sleep=waits.append)
    assert s["prs"]["2077"]["mergeable"] == "CONFLICTING"
    assert waits == [G.MERGEABLE_RETRY_WAIT] and reads["n"] == 2


def test_snapshot_skips_the_wait_when_nothing_is_unknown():
    s = S.empty_state()
    waits = []
    G.snapshot(s, {2077}, lambda n: {**FIX, "state": "OPEN", "mergeable": "MERGEABLE"}, lambda n: 0, T0, sleep=waits.append)
    assert waits == [] and s["prs"]["2077"]["mergeable"] == "MERGEABLE"

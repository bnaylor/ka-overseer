# overseer/tests/test_report.py
from report import parse_report

FULL = """OVERSEER REPORT kube-agents-vamp-54
role: pr-minder | theme: consumer budget | driver: loop/30m | mode: auto
prs: #2077 round 5 hold waiting-lgtm; #2056 round 16 0-threads waiting-lgtm
status: waiting-human: ci-deploy ordering decision on #2077
rules: 7
note: one optional line
"""


def test_parses_full_report():
    r = parse_report(FULL)
    assert r["session"] == "kube-agents-vamp-54"
    assert r["role"] == "pr-minder"
    assert r["theme"] == "consumer budget"
    assert r["driver"] == "loop"
    assert r["cadence_min"] == 30
    assert r["mode"] == "auto"
    assert r["status"] == "waiting-human"
    assert r["needs"] == "ci-deploy ordering decision on #2077"
    assert r["rules"] == 7
    assert r["note"] == "one optional line"


def test_parses_pr_items():
    r = parse_report(FULL)
    assert set(r["prs"]) == {2077, 2056}
    assert r["prs"][2077] == {"raw": "round 5 hold waiting-lgtm", "rounds": 5, "threads": None, "hold": True}
    assert r["prs"][2056] == {"raw": "round 16 0-threads waiting-lgtm", "rounds": 16, "threads": 0, "hold": None}


def test_threads_with_space_and_no_hold():
    r = parse_report("OVERSEER REPORT kube-agents-vamp-1\nprs: #10 round 2 3 threads no-hold\nstatus: working\n")
    assert r["prs"][10] == {"raw": "round 2 3 threads no-hold", "rounds": 2, "threads": 3, "hold": False}


def test_minimal_report():
    r = parse_report("OVERSEER REPORT kube-agents-vamp-9\nstatus: idle\n")
    assert r["session"] == "kube-agents-vamp-9"
    assert r["status"] == "idle"
    assert r["prs"] == {}
    assert r["role"] is None and r["rules"] is None


def test_wrapped_in_cross_session_envelope():
    text = '<cross-session-message from="x">\n' + FULL + "</cross-session-message>"
    assert parse_report(text)["session"] == "kube-agents-vamp-54"


def test_not_a_report():
    assert parse_report("hello there") is None
    assert parse_report("") is None


def test_unknown_status_is_kept_verbatim_for_the_caller_to_reject():
    r = parse_report("OVERSEER REPORT kube-agents-vamp-2\nstatus: dancing\n")
    assert r["status"] == "dancing"


def test_prs_none_means_empty():
    r = parse_report("OVERSEER REPORT kube-agents-vamp-2\nprs: none\nstatus: idle\n")
    assert r["prs"] == {}


def test_semicolons_inside_free_text_do_not_make_new_items():
    r = parse_report("OVERSEER REPORT kube-agents-vamp-da\n"
                     "prs: #2205 round 3 0-threads hold (run 1 green; run 2 queued); #1979 round 0 no-hold (draft; owes rebase after #1935, door flag)\n"
                     "status: working\n")
    assert set(r["prs"]) == {2205, 1979}
    assert r["prs"][2205]["raw"] == "round 3 0-threads hold (run 1 green; run 2 queued)"
    assert r["prs"][1979]["raw"].endswith("owes rebase after #1935, door flag)")


def test_driver_with_trailing_commentary_keeps_cadence():
    r = parse_report("OVERSEER REPORT kube-agents-vamp-54\nrole: pr-minder | theme: x | driver: loop/30m (dynamic; 60m when idle) | mode: auto\nstatus: working\n")
    assert r["driver"] == "loop" and r["cadence_min"] == 30
    r = parse_report("OVERSEER REPORT kube-agents-vamp-c3\ndriver: loop/60m (slow mode; 2h pace when a gate moves)\nstatus: idle\n")
    assert r["driver"] == "loop" and r["cadence_min"] == 60


def test_status_with_trailing_detail_keeps_the_token():
    r = parse_report("OVERSEER REPORT kube-agents-vamp-c3\nstatus: idle (sequenced: #2099 behind #2207, #2216 behind #2215)\n")
    assert r["status"] == "idle"
    assert r["status_detail"] == "(sequenced: #2099 behind #2207, #2216 behind #2215)"
    r = parse_report("OVERSEER REPORT kube-agents-vamp-20\nstatus: waiting-human: should I file an issue for #1938's leftovers (a: b)?\n")
    assert r["status"] == "waiting-human" and r["needs"] == "should I file an issue for #1938's leftovers (a: b)?"


def test_prs_line_starting_with_none_owns_nothing_even_if_numbers_follow():
    r = parse_report("OVERSEER REPORT kube-agents-vamp-c3\nprs: none owned; opened and handed to kube-agents-vamp-54: #2215 (#2197), #2218; #2077 (#2043 item 2) also with vamp-54\nstatus: idle\n")
    assert r["prs"] == {}
    r = parse_report("OVERSEER REPORT kube-agents-vamp-d1\nprs: none owned. Observer on #2056 (owner kube-agents-vamp-cb): round 38\nstatus: idle\n")
    assert r["prs"] == {}


def test_no_hold_wins_over_a_later_mention_of_hold():
    r = parse_report("OVERSEER REPORT kube-agents-vamp-54\nprs: #2077 round 5 0-threads no-hold mergeable, hold lifted 09-30\nstatus: working\n")
    assert r["prs"][2077]["hold"] is False
    r = parse_report("OVERSEER REPORT kube-agents-vamp-54\nprs: #2077 round 5 hold (waiting on three green runs)\nstatus: working\n")
    assert r["prs"][2077]["hold"] is True


def test_status_token_is_case_and_spacing_tolerant():
    assert parse_report("OVERSEER REPORT kube-agents-vamp-1\nstatus: Working\n")["status"] == "working"
    r = parse_report("OVERSEER REPORT kube-agents-vamp-1\nstatus: WAITING-BNAYLOR: q here\n")
    assert r["status"] == "waiting-human" and r["needs"] == "q here"
    r = parse_report("OVERSEER REPORT kube-agents-vamp-1\nstatus: waiting-human:q here\n")
    assert r["status"] == "waiting-human" and r["needs"] == "q here"
    assert parse_report("OVERSEER REPORT kube-agents-vamp-1\nstatus: waiting_bnaylor: q\n")["status"] == "waiting-human"


def test_numbers_inside_parentheses_never_claim_a_pr():
    r = parse_report("OVERSEER REPORT worker-a\n"
                     "prs: #102 round 2 0-threads hold (merge order; clearance #103+#104 merged + rebase)\nstatus: working\n")
    assert set(r["prs"]) == {102}
    r = parse_report("OVERSEER REPORT worker-a\n"
                     "prs: #110 round 2 0-threads no-hold threads fixed (docs; gap filed as #111 and linked), /review posted\n"
                     "status: working\n")
    assert set(r["prs"]) == {110}
    r = parse_report("OVERSEER REPORT worker-a\n"
                     "prs: #120 round 2 0-threads no-hold (decline; #121 is the planner's); #122 round 3 0-threads no-hold\n"
                     "status: working\n")
    assert set(r["prs"]) == {120, 122}


def test_prs_unchanged_is_a_sentinel_not_an_empty_list():
    r = parse_report("OVERSEER REPORT worker-a\nprs: unchanged (see last report)\nstatus: working\n")
    assert r["prs"] is None

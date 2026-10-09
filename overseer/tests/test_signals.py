# overseer/tests/test_signals.py
import signals as X
import state as S

T0 = "2026-10-01T18:00:00Z"
T_PLUS_70M = "2026-10-01T19:10:00Z"
T_PLUS_3H = "2026-10-01T21:00:00Z"
T_PLUS_2D = "2026-10-03T18:00:00Z"


def base():
    s = S.empty_state()
    row = S.session(s, "kube-agents-vamp-1")
    row.update(status="working", roster_status="idle", cadence_min=30, last_report=T0, last_activity=T0, prs=[5])
    p = S.pr(s, 5)
    p.update(owner="kube-agents-vamp-1", state="OPEN", checks="green", unresolved_threads=0, rounds=2,
             reviewers=["x"], last_activity=T0, last_owner_report=T0)
    return s


def types(actions, t):
    return [a for a in actions if a["type"] == t]


def test_quiet_state_yields_nothing():
    assert X.compute_actions(base(), T0) == []


def test_intro_for_session_never_poked_and_never_reported():
    s = base()
    S.session(s, "kube-agents-vamp-2").update(roster_status="idle")
    a = types(X.compute_actions(s, T0), "INTRO")
    assert [x["session"] for x in a] == ["kube-agents-vamp-2"]


def test_silent_past_twice_cadence_while_pr_moved_pings():
    s = base()
    s["prs"]["5"]["last_activity"] = "2026-10-01T18:30:00Z"   # moved after the last report
    a = types(X.compute_actions(s, T_PLUS_70M), "PING")
    assert len(a) == 1 and a[0]["session"] == "kube-agents-vamp-1" and a[0]["ladder"] == 1


def test_silent_but_pr_unmoved_does_not_ping():
    assert types(X.compute_actions(base(), T_PLUS_70M), "PING") == []


def test_ladder_escalates_to_attention_on_third_pass():
    s = base()
    s["prs"]["5"]["last_activity"] = "2026-10-01T18:30:00Z"
    s["sessions"]["kube-agents-vamp-1"]["ladder"] = 2
    s["sessions"]["kube-agents-vamp-1"]["last_poke"] = T0
    a = X.compute_actions(s, T_PLUS_3H)
    att = types(a, "ATTENTION")
    assert len(att) == 1 and att[0]["kind"] == "stalled"
    assert types(a, "PING") == []


def test_round_cap_stops_once():
    s = base()
    s["prs"]["5"].update(rounds=6, rounds_prev=5)
    a = X.compute_actions(s, T0)
    assert [x["type"] for x in a] == ["STOP", "ATTENTION"]
    assert a[1]["kind"] == "drift-check"
    S.add_attention(s, "kube-agents-vamp-1", "drift-check", 5, "x", T0)
    assert X.compute_actions(s, T0) == []     # already open: no repeat


def test_red_check_pings_then_attention():
    s = base()
    s["prs"]["5"].update(checks="red", last_activity=T0)
    s["sessions"]["kube-agents-vamp-1"]["last_report"] = "2026-10-01T17:00:00Z"
    a = X.compute_actions(s, T_PLUS_70M)
    assert types(a, "PING") and types(a, "PING")[0]["kind"] == "red"
    s["sessions"]["kube-agents-vamp-1"]["ladder"] = 2
    a = X.compute_actions(s, T_PLUS_3H)
    assert [x["kind"] for x in types(a, "ATTENTION")] == ["red"]


def test_untouched_threads_two_hours_with_silent_owner():
    s = base()
    s["prs"]["5"].update(unresolved_threads=3, last_activity=T0)
    s["sessions"]["kube-agents-vamp-1"]["last_report"] = T0
    assert types(X.compute_actions(s, T_PLUS_70M), "PING") == []
    a = types(X.compute_actions(s, T_PLUS_3H), "PING")
    assert a and a[0]["kind"] == "threads"


def test_no_reviewer_for_a_day_advises():
    s = base()
    s["prs"]["5"].update(reviewers=[], last_activity=T0)
    s["sessions"]["kube-agents-vamp-1"]["status"] = "waiting-review"
    assert types(X.compute_actions(s, T_PLUS_3H), "ADVICE") == []
    a = types(X.compute_actions(s, T_PLUS_2D), "ADVICE")
    assert a and a[0]["kind"] == "no-reviewer"


def test_gone_and_waiting_sessions_are_not_pinged():
    s = base()
    s["prs"]["5"]["last_activity"] = "2026-10-01T18:30:00Z"
    s["sessions"]["kube-agents-vamp-1"]["roster_status"] = "waiting"
    assert types(X.compute_actions(s, T_PLUS_70M), "PING") == []
    s["sessions"]["kube-agents-vamp-1"]["roster_status"] = "gone"
    assert types(X.compute_actions(s, T_PLUS_70M), "PING") == []


def test_a_session_back_by_report_is_escalated_like_a_live_one():
    s = base()
    s["prs"]["5"]["last_activity"] = "2026-10-01T18:30:00Z"
    s["sessions"]["kube-agents-vamp-1"].update(roster_status="gone", gone_since="2026-10-01T17:00:00Z")
    assert S.is_live(s, "kube-agents-vamp-1")
    assert types(X.compute_actions(s, T_PLUS_70M), "PING") != []


def test_apply_actions_bumps_ladder_and_opens_items():
    s = base()
    s["prs"]["5"]["last_activity"] = "2026-10-01T18:30:00Z"
    acts = X.compute_actions(s, T_PLUS_70M)
    new = X.apply_actions(s, acts, T_PLUS_70M)
    assert s["sessions"]["kube-agents-vamp-1"]["ladder"] == 1
    assert s["sessions"]["kube-agents-vamp-1"]["last_poke"] == T_PLUS_70M
    assert new == []
    s["sessions"]["kube-agents-vamp-1"]["ladder"] = 2
    acts = X.compute_actions(s, T_PLUS_3H)
    new = X.apply_actions(s, acts, T_PLUS_3H)
    assert [i["kind"] for i in new] == ["stalled"]
    assert s["sessions"]["kube-agents-vamp-1"]["ladder"] == 3


def test_overseer_never_acts_on_itself():
    s = base()
    s["overseer"]["session"] = "kube-agents-vamp-65"
    S.session(s, "kube-agents-vamp-65").update(roster_status="busy")
    assert [a for a in X.compute_actions(s, T0) if a["session"] == "kube-agents-vamp-65"] == []


def test_round_cap_already_past_on_first_sight_is_not_a_stop():
    s = base()
    s["prs"]["5"].update(rounds=15, rounds_prev=None)       # first snapshot: inherited history
    assert X.compute_actions(s, T0) == []
    s["prs"]["5"].update(rounds=15, rounds_prev=15)         # unchanged past the cap: quiet
    assert X.compute_actions(s, T0) == []
    s["prs"]["5"].update(rounds=16, rounds_prev=15)         # another round past the cap: the spiral continues
    assert [x["type"] for x in X.compute_actions(s, T0)] == ["STOP", "ATTENTION"]


def test_no_intro_until_a_session_has_lived_five_minutes():
    s = base()
    S.session(s, "kube-agents-vamp-new").update(roster_status="busy", started_at="2026-10-01T17:58:00Z")
    assert types(X.compute_actions(s, T0), "INTRO") == []                      # 2 minutes old
    s["sessions"]["kube-agents-vamp-new"]["started_at"] = "2026-10-01T17:50:00Z"
    assert [a["session"] for a in types(X.compute_actions(s, T0), "INTRO")] == ["kube-agents-vamp-new"]


def test_introd_session_that_never_reports_is_still_escalated():
    s = base()
    row = s["sessions"]["kube-agents-vamp-1"]
    row.update(last_report=None, last_poke=T0, cadence_min=None, status=None)
    s["prs"]["5"].update(checks="red", last_activity=T0)
    a = types(X.compute_actions(s, T_PLUS_3H), "PING")
    assert a and a[0]["kind"] == "red" and a[0]["ladder"] == 1


def test_waiting_review_with_clean_pr_is_not_pinged_as_stalled():
    s = base()
    s["sessions"]["kube-agents-vamp-1"]["status"] = "waiting-review"
    s["prs"]["5"].update(last_activity="2026-10-01T18:30:00Z", checks="pending", unresolved_threads=0)
    assert types(X.compute_actions(s, T_PLUS_3H), "PING") == []           # parked on a human; Tide/label churn is not a reason to ping
    s["prs"]["5"]["unresolved_threads"] = 2
    assert types(X.compute_actions(s, T_PLUS_3H), "PING") != []           # a new thread is


def test_review_minder_owned_prs_get_no_cap_stop():
    s = base()
    s["sessions"]["kube-agents-vamp-1"]["role"] = "review-minder"
    s["prs"]["5"].update(rounds=7, rounds_prev=6)
    assert [a["type"] for a in X.compute_actions(s, T0)] == []          # it reviews the PR; the author drives the rounds


def test_pr_with_lgtm_and_nothing_open_is_not_a_stall_whatever_the_status_word():
    s = base()
    s["prs"]["5"].update(last_activity="2026-10-01T18:30:00Z", checks="green", unresolved_threads=0, lgtm=True)
    s["sessions"]["kube-agents-vamp-1"]["status"] = "working"
    assert types(X.compute_actions(s, T_PLUS_3H), "PING") == []     # Tide owns it; the owner has nothing to do


def test_held_pr_threads_are_not_a_stall():
    s = base()
    s["prs"]["5"].update(unresolved_threads=3, last_activity=T0, hold=True)
    s["sessions"]["kube-agents-vamp-1"]["last_report"] = T0
    assert types(X.compute_actions(s, T_PLUS_3H), "PING") == []     # a /hold parks the PR on purpose; its threads wait with it


def test_review_minder_is_not_pinged_for_an_authors_threads():
    s = base()
    s["prs"]["5"].update(unresolved_threads=2, last_activity=T0)
    s["sessions"]["kube-agents-vamp-1"]["last_report"] = T0
    s["sessions"]["kube-agents-vamp-1"]["role"] = "review-minder"
    assert [a for a in types(X.compute_actions(s, T_PLUS_3H), "PING") if a["kind"] == "threads"] == []


def test_snoozed_red_stays_quiet_until_the_head_changes():
    s = base()
    s["prs"]["5"].update(checks="red", head="aaaaaaaa", last_activity=T0)
    s["sessions"]["kube-agents-vamp-1"].update(last_report=T0, ladder=2)
    S.snooze(s, "kube-agents-vamp-1", "red", 5)          # pins the current head
    assert [a for a in X.compute_actions(s, T_PLUS_3H) if a["kind"] == "red"] == []
    s["prs"]["5"]["head"] = "bbbbbbbb"
    a = [a for a in X.compute_actions(s, T_PLUS_3H) if a["kind"] == "red"]
    assert a and a[0]["type"] == "ATTENTION"
    assert S.attention_id("kube-agents-vamp-1", "red", 5) not in s.get("snoozed", {})


def test_snoozed_no_reviewer_advice_stays_quiet():
    s = base()
    s["prs"]["5"].update(reviewers=[], last_activity=T0, head="aaaaaaaa")
    s["sessions"]["kube-agents-vamp-1"]["status"] = "waiting-review"
    assert types(X.compute_actions(s, T_PLUS_2D), "ADVICE")
    S.snooze(s, "kube-agents-vamp-1", "no-reviewer", 5)
    assert types(X.compute_actions(s, T_PLUS_2D), "ADVICE") == []


def test_no_reviewer_advice_skips_a_pr_that_already_has_lgtm():
    s = base()
    s["prs"]["5"].update(reviewers=[], last_activity=T0, lgtm=True)
    s["sessions"]["kube-agents-vamp-1"]["status"] = "waiting-review"
    assert types(X.compute_actions(s, T_PLUS_2D), "ADVICE") == []


def test_conflicting_pr_pings_once_the_owner_is_silent_an_hour_even_when_held():
    s = base()
    s["prs"]["5"].update(mergeable="CONFLICTING", hold=True)
    assert X.compute_actions(s, "2026-10-01T18:30:00Z") == []          # owner reported half an hour ago
    a = types(X.compute_actions(s, T_PLUS_70M), "PING")
    assert a and a[0]["kind"] == "conflict" and "rule 22" in a[0]["reason"]


def test_review_minder_is_not_pinged_for_another_authors_conflict():
    s = base()
    s["prs"]["5"].update(mergeable="CONFLICTING")
    s["sessions"]["kube-agents-vamp-1"]["role"] = "review-minder"
    assert types(X.compute_actions(s, T_PLUS_70M), "PING") == []

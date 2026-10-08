# overseer/tests/test_render.py
import json
import render as V
import state as S

T0 = "2026-10-01T20:00:00Z"


def sample():
    s = S.empty_state()
    S.session(s, "kube-agents-vamp-1").update(role="pr-minder", theme="budget", driver="loop", cadence_min=30,
                                             status="working", roster_status="busy", prs=[5], last_report=T0,
                                             last_activity=T0, rules_ack=9, note="fine")
    S.session(s, "kube-agents-vamp-2").update(role="task", status="gone", roster_status="gone", gone_since=T0)
    S.pr(s, 5).update(owner="kube-agents-vamp-1", title="fix thing", head="abcdef12", state="OPEN",
                      mergeable="MERGEABLE", checks="green", unresolved_threads=0, rounds=6, advisory_rounds=1,
                      hold=False, lgtm=False, approved=True, reviewers=["jay"], last_activity=T0,
                      url="https://github.com/gke-labs/kube-agents/pull/5", drift="owner says rounds 5, GitHub 6")
    S.pr(s, 6).update(owner="kube-agents-vamp-1", title="other", state="OPEN", checks="red",
                      unresolved_threads=2, rounds=1, last_activity=T0)
    return s


def test_a_gone_question_is_a_muted_note_not_the_red_band():
    s = sample()
    S.add_attention(s, "kube-agents-vamp-2", "gone-question", None, "gone while waiting on you: ship it?", T0)
    html = V.render(s, None, T0)
    assert "Nothing is waiting on you" in html and 'class="attention red"' not in html
    assert 'class="attention note"' in html and "gone while waiting on you: ship it?" in html


def test_a_session_back_by_report_is_not_rendered_gone():
    s = sample()
    s["sessions"]["kube-agents-vamp-2"]["last_report"] = "2026-10-01T20:05:00Z"   # after gone_since T0
    for roster in (None, [{"name": "kube-agents-vamp-1", "status": "busy"}]):
        html = V.render(s, roster, T0)
        assert '<tr class="row gone"><td>kube-agents-vamp-2</td>' not in html


def test_empty_attention_shows_green_line():
    html = V.render(S.empty_state(), None, T0)
    assert "Nothing is waiting on you" in html
    assert 'class="attention red"' not in html


def test_attention_band_lists_items_oldest_first():
    s = sample()
    S.add_attention(s, "kube-agents-vamp-1", "waiting-human", 5, "second", "2026-10-01T19:00:00Z")
    S.add_attention(s, "kube-agents-vamp-1", "waiting", None, "first", "2026-10-01T18:00:00Z")
    html = V.render(s, None, T0)
    assert 'class="attention red"' in html
    assert html.index("first") < html.index("second")


def test_sessions_and_prs_tables():
    html = V.render(sample(), None, T0)
    assert "kube-agents-vamp-1" in html and "pr-minder" in html and "budget" in html
    assert 'class="row gone"' in html                          # gone session greyed
    assert 'class="cap"' in html                               # rounds 6 highlighted
    assert "owner says rounds 5, GitHub 6" in html
    assert html.index("fix thing") < html.index(">#6</a> other")   # nearest-the-gate first
    assert 'data-ts="2026-10-01T20:00:00Z"' in html            # ages computed client-side
    assert "Rules v9" in html


def test_roster_overrides_status_column():
    s = sample()
    html = V.render(s, [{"name": "kube-agents-vamp-1", "status": "waiting", "kind": "interactive", "started_at": T0}], T0)
    assert "⏸ waiting" in html


def test_status_never_colour_alone():
    html = V.render(sample(), None, T0)
    for label in ("✓ green", "✗ red"):
        assert label in html


def test_cli(tmp_path):
    s = sample()
    S.save_state(s, tmp_path / "state.json")
    (tmp_path / "roster.json").write_text(json.dumps({"ts": T0, "sessions": []}))
    V.main(["--state", str(tmp_path / "state.json"), "--roster", str(tmp_path / "roster.json"),
            "--out", str(tmp_path / "d.html")])
    assert "<table" in (tmp_path / "d.html").read_text()


def test_pr_numbers_are_links_everywhere():
    s = sample()
    S.add_attention(s, "kube-agents-vamp-1", "waiting-human", 5, "file an issue for #1938's leftovers?", T0)
    s["sessions"]["kube-agents-vamp-1"]["note"] = "tracks #1979 as stacked"
    html = V.render(s, None, T0)
    base = V.PR_URL.format(n="")
    assert f'<a href="{base}5">#5</a>' in html                 # sessions PRs column and attention PR column
    assert f'<a href="{base}1938">#1938</a>' in html            # inside the attention question
    assert f'<a href="{base}1979">#1979</a>' in html            # inside a note
    # text is still escaped around the links
    s["sessions"]["kube-agents-vamp-1"]["note"] = "<b>#7</b>"
    html = V.render(s, None, T0)
    assert "&lt;b&gt;" in html and f'<a href="{base}7">#7</a>' in html


def test_ready_for_human_marks_only_the_lgtm_gap():
    import render as V
    base = {"checks": "green", "unresolved_threads": 0, "mergeable": "MERGEABLE", "hold": False, "draft": False, "lgtm": False}
    assert V.ready_for_human(base)
    for k, v in [("lgtm", True), ("checks", "red"), ("unresolved_threads", 1), ("mergeable", "CONFLICTING"), ("hold", True), ("draft", True)]:
        assert not V.ready_for_human({**base, k: v}), k


def test_humans_done_needs_every_signoff_and_no_hold():
    import render as V
    base = {"lgtm": True, "approved": True, "hold": False, "draft": False, "unresolved_threads": 0, "checks": "red"}
    assert V.humans_done(base)
    for k, v in [("lgtm", False), ("approved", False), ("hold", True), ("draft", True), ("unresolved_threads", 2)]:
        assert not V.humans_done({**base, k: v}), k
    assert not V.ready_for_human(base)     # lgtm'd, so never both colours


def test_session_rows_carry_the_full_note_for_the_hover_balloon():
    import render as V, state as S
    st = S.empty_state()
    row = S.session(st, "kube-agents-vamp-99")
    row.update(note="a very long note " * 10, status="working", role="task")
    out = V.render(st, None, "2026-10-07T00:00:00Z")
    assert 'data-note="a very long note' in out
    assert "#balloon" in out and "tr[data-note]" in out


def test_prs_are_grouped_by_the_search_that_found_them():
    s = sample()
    s["prs"]["5"]["scopes"] = ["mine"]
    s["prs"]["6"]["scopes"] = []
    S.pr(s, 7).update(title="theirs", state="OPEN", scopes=["reviewed"])
    S.pr(s, 8).update(title="asked", state="OPEN", scopes=["review_requested"])
    S.pr(s, 9).update(title="done", state="MERGED", scopes=["mine"])            # closed PRs stay hidden
    html = V.render(s, None, T0)
    i_mine, i_req, i_rev, i_other = (html.index(h) for h in (
        "<h3>Mine (1)</h3>", "<h3>Review requested from me (1)</h3>", "<h3>Reviewed by me (1)</h3>",
        "<h3>Other: no search finds it (1)</h3>"))
    assert i_mine < html.index("fix thing") < i_req < html.index("asked") < i_rev < html.index("theirs") < i_other
    assert html.index("other") > i_other                  # PR 6: no search found it
    assert ">done<" not in html and "#9" not in html


def test_every_search_group_shows_even_when_empty_and_other_only_when_used():
    html = V.render(S.empty_state(), None, T0)
    assert "<h3>Mine (0)</h3>" in html and "<h3>Review requested from me (0)</h3>" in html and "<h3>Reviewed by me (0)</h3>" in html
    assert html.count("None open.") == 3 and "Other:" not in html


def test_prs_saved_before_searches_were_recorded_wait_in_not_sorted_yet():
    html = V.render(sample(), None, T0)                    # sample rows carry no scopes yet
    assert "<h3>Not sorted yet: the next tick that reads GitHub sorts these (2)</h3>" in html and "Other:" not in html

"""Escalation engine. compute_actions is pure; apply_actions mutates state. See escalation.md."""
from datetime import timedelta

import state as S
from gh_snapshot import ROUND_CAP

SILENT_FACTOR = 2
THREADS_HOURS = 2
RED_HOURS = 1
CONFLICT_HOURS = 1        # a conflict blocks merge as hard as a red (rule 22); held PRs included, they must stay merge-ready
NO_REVIEWER_HOURS = 24
INTRO_GRACE_MIN = 5          # short-lived sessions come and go; do not INTRO until one has lived this long
SKIP_PING = {"waiting"}          # gone sessions never reach the ladder: compute_actions skips them


def _age(now, ts):
    if not ts:
        return None
    return S.parse_iso(now) - S.parse_iso(ts)


def _open(state, session, kind, pr):
    return any(i["id"] == S.attention_id(session, kind, pr) for i in state["attention"])


def _action(type_, session, pr, kind, reason, ladder=None):
    return {"type": type_, "session": session, "pr": pr, "kind": kind, "reason": reason, "ladder": ladder}


def _escalate(state, now, name, row, pr, kind, reason, actions):
    """Shared ladder: ping, ping, attention."""
    if row["roster_status"] in SKIP_PING:
        return
    if pr is not None and S.snoozed(state, name, kind, pr):     # explained; quiet until the head moves
        return
    ladder = row.get("ladder") or 0
    if ladder >= 2:
        if not _open(state, name, kind, pr):
            actions.append(_action("ATTENTION", name, pr, kind, reason, 3))
    else:
        # one ping per tick at most: skip if poked within 10 minutes
        age = _age(now, row.get("last_poke"))
        if age is None or age >= timedelta(minutes=10):
            actions.append(_action("PING", name, pr, kind, reason, ladder + 1))


def compute_actions(state, now, default_cadence_min=30):
    actions = []
    me = (state.get("overseer") or {}).get("session")
    for name, row in state["sessions"].items():
        if not S.is_live(state, name) or name == me:
            continue
        if row["last_report"] is None and row["last_poke"] is None:
            lived = _age(now, row.get("started_at"))
            if lived is None or lived >= timedelta(minutes=INTRO_GRACE_MIN):
                actions.append(_action("INTRO", name, None, None, "new session"))
            continue
        cadence = timedelta(minutes=(row.get("cadence_min") or default_cadence_min))
        silent = _age(now, row["last_report"] or row["last_poke"])   # never reported: the INTRO is the anchor
        owned = [(int(n), p) for n, p in state["prs"].items()
                 if p["owner"] == name and p.get("state") in S.OPEN_PR_STATES]
        pinged_this_pass = False
        for num, p in owned:
            # round cap: STOP once
            crossed = (p.get("rounds") or 0) >= ROUND_CAP and p.get("rounds_prev") is not None \
                and p["rounds"] > p["rounds_prev"] and row.get("role") != "review-minder"   # a reviewer does not drive rounds
            if crossed and not _open(state, name, "drift-check", num):
                actions.append(_action("STOP", name, num, "drift-check",
                                       f"#{num} at {p['rounds']} gating rounds (cap {ROUND_CAP}); stop fixing, report drift"))
                actions.append(_action("ATTENTION", name, num, "drift-check",
                                       f"#{num} hit the round cap; drift check", None))
            moved = _age(now, p.get("last_activity"))
            anchor = row["last_report"] or row["last_poke"]
            moved_after_report = (p.get("last_activity") and anchor
                                  and S.parse_iso(p["last_activity"]) > S.parse_iso(anchor))
            if pinged_this_pass:
                continue
            if p.get("checks") == "red" and moved is not None and moved >= timedelta(hours=RED_HOURS) \
                    and silent is not None and silent >= timedelta(hours=RED_HOURS):
                _escalate(state, now, name, row, num, "red", f"#{num} red for over {RED_HOURS}h, owner silent", actions)
                pinged_this_pass = True
            elif p.get("mergeable") == "CONFLICTING" and silent is not None \
                    and silent >= timedelta(hours=CONFLICT_HOURS) and row.get("role") != "review-minder":
                _escalate(state, now, name, row, num, "conflict",
                          f"#{num} conflicts with main, owner silent {CONFLICT_HOURS}h+; merge main (rule 22)", actions)
                pinged_this_pass = True
            elif (p.get("unresolved_threads") or 0) > 0 and not p.get("hold") and moved is not None \
                    and moved >= timedelta(hours=THREADS_HOURS) and silent is not None \
                    and silent >= timedelta(hours=THREADS_HOURS) \
                    and row.get("role") != "review-minder":      # a held PR is parked on purpose; its threads wait with it
                _escalate(state, now, name, row, num, "threads",
                          f"#{num} has {p['unresolved_threads']} unresolved threads untouched {THREADS_HOURS}h+, owner silent", actions)
                pinged_this_pass = True
            elif moved_after_report and silent is not None and silent >= SILENT_FACTOR * cadence \
                    and not ((row["status"] == "waiting-review" or (p.get("lgtm") and p.get("checks") == "green"))
                             and (p.get("unresolved_threads") or 0) == 0):
                # a PR parked on a human, or lgtm'd and green (Tide owns it), moves for Tide and label reasons;
                # only new threads or a red make it a stall
                _escalate(state, now, name, row, num, "stalled",
                          f"#{num} moved since your last report and you have been silent {int(silent.total_seconds() // 60)}m", actions)
                pinged_this_pass = True
            if row["status"] == "waiting-review" and not p.get("reviewers") and not p.get("lgtm") and moved is not None \
                    and moved >= timedelta(hours=NO_REVIEWER_HOURS):
                if not _open(state, name, "no-reviewer", num) and not S.snoozed(state, name, "no-reviewer", num):
                    actions.append(_action("ADVICE", name, num, "no-reviewer",
                                           f"#{num} parked waiting-review with no reviewer for a day; a reviewer auto-assigns only after a clean bot round, so check the latest round is clean and otherwise ask the human"))
        if not owned and row["roster_status"] == "idle" and row["status"] == "working" \
                and silent is not None and silent >= SILENT_FACTOR * cadence:
            _escalate(state, now, name, row, None, "stalled", "roster idle but last report said working", actions)
    return actions


def apply_actions(state, actions, now):
    new = []
    for a in actions:
        if a["type"] in ("PING", "STOP", "INTRO", "ADVICE") and a["session"]:
            row = S.session(state, a["session"])
            row["last_poke"] = now
            if a["type"] == "PING":
                row["ladder"] = a["ladder"]
        if a["type"] == "ATTENTION":
            item = S.add_attention(state, a["session"], a["kind"], a["pr"], a["reason"], now)
            if item:
                new.append(item)
            if a["ladder"] == 3:
                S.session(state, a["session"])["ladder"] = 3
    return new

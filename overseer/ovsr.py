"""Overseer CLI. The Overseer session drives everything through this file."""
import argparse
import json
import re
import sys
from pathlib import Path

import drift as D
import gh_snapshot as G
import render as V
import signals as X
import state as S
import worktrees as W
from report import parse_report
from config import CFG
from roster import notify as _osascript_notify

FIRED = []  # tests replace notify and collect here


def notify(title, text):
    _osascript_notify(title, text)


INTRO_TEXT = """INTRO from the Overseer ({me}). I track every session working on {repo}, keep the dashboard, relay numbered rules, and poke stalled sessions. I may tell you to STOP only for a written rule; everything else is advice.
FIRST: invoke the skill `working-with-the-overseer` with the Skill tool — it is in your skill listing now — and follow it. If the listing does not show it yet, read {skill}. Rules: {rules}. Dashboard: {dir}/dashboard.html.
Reply now with a report in this exact shape (first line verbatim with your own name from ListAgents):
OVERSEER REPORT <your-session-name>
role: pr-minder|bug-minder|review-minder|task | theme: <topic> | driver: loop/<N>m|goal|manual | mode: auto|other
prs: #<n> round <r> <k>-threads hold|no-hold <free text>; ...   (or: none)
status: working|waiting-human: <question>|waiting-review|idle
rules: <highest rule number you have read>
From now on send me that report whenever your status or PR list changes, BEFORE you ask {human} anything (status waiting-human), before you stop a loop, and when I PING you."""


class Paths:
    def __init__(self, d):
        self.dir = Path(d)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state = self.dir / "state.json"
        self.log = self.dir / "reports.log"
        self.roster = self.dir / "roster.json"
        self.rules = self.dir.parent / "docs" / "rules.md"
        self.skill = self.dir.parent / "skills" / "working-with-the-overseer" / "SKILL.md"
        self.html = self.dir / "dashboard.html"


def _load(p):
    try:
        return S.load_state(p.state)
    except json.JSONDecodeError:
        return S.rebuild_from_log(p.log)


ROSTER_MAX_AGE_MIN = 5


def _live_roster():
    import roster as R
    return R.read_roster()


def _roster(p):
    if p.roster.exists():
        try:
            data = json.loads(p.roster.read_text())
            age = S.parse_iso(S.now_iso()) - S.parse_iso(data["ts"])
            if age.total_seconds() <= ROSTER_MAX_AGE_MIN * 60:
                return data.get("sessions", [])
        except (ValueError, KeyError):
            pass                      # torn write or old shape: fall through to a live read
    return _live_roster()


def _render(p, state):
    roster = None
    if p.roster.exists():
        try:
            roster = json.loads(p.roster.read_text()).get("sessions")
        except ValueError:
            roster = None
    S.write_atomic(p.html, V.render(state, roster, S.now_iso()))


def refresh_worktrees(state, now):
    """Never fails the tick: a broken scan is shown on the dashboard instead."""
    try:
        W.refresh(state, now, G.branch_prs)
    except Exception as e:
        prev = state.get("worktrees") or {"rows": []}
        state["worktrees"] = {**prev, "errors": [f"scan failed: {e}"]}


def _notify_new(state):
    for item in state["attention"]:
        if item.get("notified"):
            continue
        if item["kind"] == "waiting":        # watch.sh already notified this one
            item["notified"] = True
            continue
        notify("Overseer", f"{item['session']}: {item['what']}")
        item["notified"] = True


def _finish(p, state):
    _notify_new(state)
    S.save_state(state, p.state)
    _render(p, state)


def cmd_report(p, a):
    raw = sys.stdin.read()
    now = S.now_iso()
    report = parse_report(raw)
    S.append_log(p.log, a.from_ or (report["session"] if report else None), raw, now)
    error = None
    if not report:
        error = "not in OVERSEER REPORT schema"
    elif a.from_ and a.from_ != report["session"]:
        error = f"report names {report['session']} but came from {a.from_}; not applied"
    elif report["status"] is not None and report["status"] not in S.SESSION_STATUSES:
        error = f"unknown status {report['status']!r}; expected one of {sorted(S.SESSION_STATUSES)}"
    if error:
        print(json.dumps({"ok": False, "session": a.from_, "new_attention": [], "error": error}))
        return 2
    state = _load(p)
    new = S.apply_report(state, report, now)
    for number in report["prs"] or ():
        row = state["prs"][str(number)]
        if row.get("rounds") is not None:
            row["drift"] = G.compute_drift(row)
    _finish(p, state)
    print(json.dumps({"ok": True, "session": report["session"], "new_attention": new, "error": None}))
    return 0


_ABSENT = object()


def _read_github(p, now):
    """The slow part of a tick, run without the lock on a private copy of the state.  Returns what GitHub
    changed: per-PR field updates, the worktree table, and the snapshot's errors."""
    work = _load(p)
    before = {k: dict(v) for k, v in work["prs"].items()}
    res = G.snapshot(work, G.scope_numbers(work), now=now)
    refresh_worktrees(work, now)
    changes = {}
    for k, row in work["prs"].items():
        # A PR first seen by this snapshot is diffed against a blank row, not {}: otherwise its default
        # owner=None rides along and wipes a claim a report filed while GitHub was being read.
        old = before.get(k) or S._pr_row()
        diff = {f: v for f, v in row.items() if old.get(f, _ABSENT) != v}
        if diff or k not in before:
            changes[k] = diff
    return res, changes, work.get("worktrees"), work.get("updated")


def _merge_github(state, changes, worktrees, updated):
    """Lay GitHub's results over a freshly loaded state, so reports filed meanwhile survive."""
    for k, diff in changes.items():
        row = S.pr(state, int(k))
        row.update(diff)
        if "drift" in diff:
            row["drift"] = G.compute_drift(row)     # recompute against owner beliefs that may have just changed
    if worktrees is not None:
        state["worktrees"] = worktrees
    if updated:
        state["updated"] = updated


def cmd_tick(p, a):
    now = S.now_iso()
    gh_errors = {}
    fresh = False
    if not a.no_gh:
        res, changes, worktrees, updated = _read_github(p, now)
        gh_errors = {str(k): v for k, v in res["errors"].items()}
        fresh = True
    with S.locked(p.state):
        state = _load(p)
        if fresh:
            _merge_github(state, changes, worktrees, updated)
        return _tick_locked(p, a, state, now, gh_errors, fresh)


def _tick_locked(p, a, state, now, gh_errors, fresh):
    if a.session:
        state["overseer"]["session"] = a.session
        me = S.session(state, a.session)
        me.update(role="overseer", status="working", last_report=now, rules_ack=state["rules_version"],
                  theme=me["theme"] or "all kube-agents sessions", driver=me["driver"] or "loop")
        S.write_atomic(p.dir / "OVERSEER", a.session + "\n")   # one line, grep-proof: who the Overseer is right now
    new = S.apply_roster(state, _roster(p), now)
    new += S.raise_orphans(state, now, fresh=fresh)   # after the snapshot: a dropped PR that merged raises nothing
    actions = X.compute_actions(state, now)
    new += X.apply_actions(state, actions, now)
    drift_started = _drift_tick(p, state, actions, now)
    new += _drift_collect(state, now)
    retired = S.retire_due(state, now)
    ov = state["overseer"]
    ov["tick"] = (ov.get("tick") or 0) + 1
    if a.next_wake:
        ov["next_wake"] = a.next_wake
    _finish(p, state)
    summary = []
    if new:
        summary.append("needs you: " + "; ".join(f"{i['session']} {i['what']}" for i in new))
    sends = [x for x in actions if x["type"] != "ATTENTION"]
    if sends:
        summary.append("to send: " + ", ".join(f"{x['type']} {x['session']}" for x in sends))
    if gh_errors:
        summary.append("gh errors: " + ", ".join(gh_errors))
    if retired:
        summary.append("retired: " + ", ".join(retired))
    if drift_started:
        summary.append("drift review started: " + ", ".join(f"#{n}" for n in drift_started))
    wt = (state.get("worktrees") or {}).get("rows") or []
    cleanup = sum(1 for r in wt if r["status"] in ("cleanup", "missing"))
    wt_part = f", {cleanup} worktree{'s' if cleanup != 1 else ''} to clean up" if cleanup else ""
    summary.append(f"tick {ov['tick']}, {len(state['attention'])} open attention{wt_part}, next wake {ov.get('next_wake') or 'unset'}")
    summary = summary[:4] + summary[-1:] if len(summary) > 5 else summary   # the tick line always survives the cap
    print(json.dumps({"actions": actions, "new_attention": new, "gh_errors": gh_errors, "summary": summary}, indent=1))
    return 0


def _drift_tick(p, state, actions, now):
    """Every drift-check ATTENTION starts a headless drift review of that PR (once per head)."""
    started = []
    for x in actions:
        if x["type"] == "ATTENTION" and x.get("kind") == "drift-check" and x.get("pr") is not None:
            rounds = (state["prs"].get(str(x["pr"])) or {}).get("rounds")
            if D.launch(state, x["pr"], rounds, G.ROUND_CAP, p.dir / "drift", now):
                started.append(x["pr"])
    return started


def _drift_collect(state, now):
    """A finished drift review becomes an attention item that names its verdict and file."""
    new = []
    for pr, run in D.collect(state):
        owner = (state["prs"].get(str(pr)) or {}).get("owner") or "-"
        what = f"drift review of #{pr}: {run.get('verdict') or 'no verdict line (read the file)'}; {run['path']}"
        item = S.add_attention(state, owner, "drift-review", pr, what, now)
        if item:
            item["manual"] = True      # a report for the human to read, not a fact the sweep can overtake
            new.append(item)
    return new


def cmd_drift(p, a):
    """Start a drift review by hand (re-runs even if one already ran for this head)."""
    state = _load(p)
    now = S.now_iso()
    (state.get("drift_runs") or {}).pop(str(a.pr), None)
    rounds = (state["prs"].get(str(a.pr)) or {}).get("rounds")
    run = D.launch(state, a.pr, rounds, G.ROUND_CAP, p.dir / "drift", now)
    _finish(p, state)
    print(json.dumps({"pr": a.pr, "path": run["path"] if run else None}))
    return 0 if run else 1


def cmd_sent(p, a):
    state = _load(p)
    row = S.session(state, a.session)
    row["last_poke"] = S.now_iso()      # the ladder was already advanced by the tick that listed the PING
    _finish(p, state)
    return 0


def cmd_assign(p, a):
    state = _load(p)
    pr = S.pr(state, a.pr)
    previous = pr["owner"]
    S.hand_off(state, a.pr, a.session)
    pr["hazards"] = a.hazards
    S.session(state, a.session)
    _finish(p, state)
    print(f"ASSIGN #{a.pr} to you from the Overseer. Previous owner: {previous or 'none'}. Hazards: {a.hazards or 'none recorded'}. "
          f"Reply with a report listing #{a.pr}.")
    return 0


def cmd_retire(p, a):
    state = _load(p)
    ok = S.retire(state, a.session, S.now_iso())
    _finish(p, state)
    return 0 if ok else 1


def cmd_clear(p, a):
    state = _load(p)
    if getattr(a, "snooze", False):
        parts = a.id.rsplit(":", 2)          # <session>:<kind>:<pr>
        if len(parts) == 3 and parts[2].isdigit():
            S.snooze(state, parts[0], parts[1], int(parts[2]))
            ok = True
        else:
            ok = False
    else:
        ok = S.clear_attention(state, a.id)
    _finish(p, state)
    return 0 if ok else 1


def cmd_attention(p, a):
    """A judgment item the Overseer opens by hand (drift check, policy orphan). Notifies once like any other."""
    state = _load(p)
    pr = int(a.pr) if a.pr not in (None, "-", "") else None
    item = S.add_attention(state, a.session, a.kind, pr, a.what, S.now_iso())
    if item:
        item["manual"] = True          # judgment, not a fact: S.sweep never drops it
    _finish(p, state)
    return 0 if item else 1


DECISION_TEXT = ("DECISION from {human} via the Overseer, on {scope}. Verbatim: \"{quote}\" "
                 "This is the human's own word (rule 13): act on it as you would on an instruction typed in your session, "
                 "including outward-facing steps it names. Reply with a report when done.")


def cmd_decision(p, a):
    """Relay a decision the human made in the Overseer's session, quoted verbatim, and log it."""
    now = S.now_iso()
    with (p.dir / "decisions.log").open("a") as f:
        f.write(json.dumps({"ts": now, "to": a.session, "pr": a.pr, "quote": a.quote}) + "\n")
    scope = f"#{a.pr}" if a.pr not in (None, "-", "") else "your current work"
    print(DECISION_TEXT.format(human=CFG["human"], scope=scope, quote=a.quote))
    return 0


def cmd_rule(p, a):
    text = p.rules.read_text() if p.rules.exists() else "# Overseer rules\n\n## Rules\n\n"
    nums = [int(m) for m in re.findall(r"^(?:~~)?(\d+)\. ", text, re.M)]
    n = (max(nums) if nums else 0) + 1
    p.rules.write_text(text.rstrip("\n") + f"\n{n}. {a.text}\n")
    state = _load(p)
    state["rules_version"] = n
    _finish(p, state)
    print(f"RULE {n}: {a.text}")
    return 0


def cmd_intro(p, a):
    state = _load(p)
    S.session(state, a.session)["last_poke"] = S.now_iso()
    _finish(p, state)
    print(INTRO_TEXT.format(me=state["overseer"].get("session") or "this session", dir=p.dir, repo=CFG["repo"],
                            human=CFG["human"], skill=p.skill, rules=p.rules))
    return 0


def cmd_rebuild(p, a):
    state = S.rebuild_from_log(p.log)
    _finish(p, state)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ovsr")
    ap.add_argument("--dir", default=str(Path(__file__).parent))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("report"); s.add_argument("--from", dest="from_"); s.set_defaults(fn=cmd_report)
    s = sub.add_parser("tick"); s.add_argument("--no-gh", action="store_true"); s.add_argument("--session"); s.add_argument("--next-wake"); s.set_defaults(fn=cmd_tick)
    s = sub.add_parser("sent"); s.add_argument("type"); s.add_argument("session"); s.set_defaults(fn=cmd_sent)
    s = sub.add_parser("assign"); s.add_argument("pr", type=int); s.add_argument("session"); s.add_argument("--hazards"); s.set_defaults(fn=cmd_assign)
    s = sub.add_parser("retire"); s.add_argument("session"); s.set_defaults(fn=cmd_retire)
    s = sub.add_parser("clear"); s.add_argument("id")
    s.add_argument("--snooze", action="store_true", help="keep the item quiet until the PR's head changes")
    s.set_defaults(fn=cmd_clear)
    s = sub.add_parser("rule"); s.add_argument("text"); s.set_defaults(fn=cmd_rule)
    s = sub.add_parser("attention"); s.add_argument("session"); s.add_argument("kind"); s.add_argument("pr"); s.add_argument("what"); s.set_defaults(fn=cmd_attention)
    s = sub.add_parser("decision"); s.add_argument("session"); s.add_argument("pr"); s.add_argument("quote"); s.set_defaults(fn=cmd_decision)
    s = sub.add_parser("intro"); s.add_argument("session"); s.set_defaults(fn=cmd_intro)
    s = sub.add_parser("rebuild"); s.set_defaults(fn=cmd_rebuild)
    s = sub.add_parser("drift"); s.add_argument("pr", type=int); s.set_defaults(fn=cmd_drift)
    a = ap.parse_args(argv)
    p = Paths(a.dir)
    if a.fn in (cmd_tick, cmd_decision):     # tick locks only around its writes; decision never touches state
        return a.fn(p, a)
    with S.locked(p.state):
        return a.fn(p, a)



if __name__ == "__main__":
    sys.exit(main())

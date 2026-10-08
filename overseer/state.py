"""Overseer state: the single source of truth in state.json. Only ovsr.py writes it."""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from report import parse_report

SESSION_STATUSES = {"working", "waiting-human", "waiting-review", "idle"}
RECENT_REPORT_MIN = 2   # a report this fresh proves the session is alive even if the roster file lacks it
GONE_SWEEP_MIN = 10     # gone this long: a restart or rename, not a blip; its question and PR claims are moot
ROSTER_STATUSES = {"busy", "idle", "waiting", "shell", "gone"}
OPEN_PR_STATES = {None, "OPEN"}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def iso_from_ts(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_iso(s):
    """Any ISO time with an offset, as UTC with a Z, so stored times compare as strings."""
    return parse_iso(s).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_state():
    return {"updated": None, "rules_version": 9,
            "overseer": {"session": None, "tick": 0, "next_wake": None},
            "sessions": {}, "prs": {}, "attention": [], "retired": [], "worktrees": None}


def load_state(path):
    path = Path(path)
    if not path.exists():
        return empty_state()
    s = empty_state()
    s.update(json.loads(path.read_text()))
    s["overseer"] = {**empty_state()["overseer"], **(s.get("overseer") or {})}
    s["sessions"] = {k: {**_session_row(), **v} for k, v in s["sessions"].items()}
    s["prs"] = {k: {**_pr_row(), **v} for k, v in s["prs"].items()}
    for i in s["attention"]:      # items saved before the manual flag: only provably automatic ones may be swept
        i.setdefault("manual", not _provably_auto(i, s["sessions"]))
    return s


_ORPHAN_WHAT = re.compile(r"session gone, owns #(\d+)(?:, #\d+)*; reassign\?")
_CONFLICT_WHAT = re.compile(r"#(\d+) reported by both (\S+) and (\S+); (\S+) now owner")


def _provably_auto(item, sessions):
    """True when the id and text are exactly what the code writes; any doubt keeps the item for the human."""
    k, s, n, what = item["kind"], item["session"], item["pr"], item.get("what") or ""
    if s not in sessions or item["id"] != attention_id(s, k, None if k == "waiting-human" else n):
        return False
    if k == "orphan":
        m = _ORPHAN_WHAT.fullmatch(what)
        return bool(m) and int(m.group(1)) == n
    if k == "ownership-conflict":
        m = _CONFLICT_WHAT.fullmatch(what)
        return bool(m) and int(m.group(1)) == n and m.group(3) == m.group(4) == s
    if k == "waiting-human":
        row = sessions[s]
        return row["status"] == "waiting-human" and what == (row["needs"] or "waiting on the human")
    return True                   # sweep touches no other kind


def save_state(state, path):
    state["updated"] = state.get("updated") or now_iso()
    write_atomic(path, json.dumps(state, indent=1, sort_keys=True) + "\n")


def write_atomic(path, text):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _session_row():
    return {"role": None, "theme": None, "driver": None, "cadence_min": None, "mode": None,
            "rules_ack": None, "prs": [], "status": None, "roster_status": None, "kind": None,
            "needs": None, "status_detail": None, "last_report": None, "last_activity": None, "last_poke": None,
            "started_at": None, "gone_since": None, "ladder": 0, "note": None, "cwd": None}


def _pr_row():
    return {"owner": None, "title": None, "head": None, "branch": None, "url": None,
            "state": None, "mergeable": None, "checks": None, "unresolved_threads": None,
            "rounds": None, "rounds_prev": None, "advisory_rounds": None, "hold": None, "lgtm": None,
            "approved": None, "reviewers": [], "last_activity": None, "updated_at": None,
            "last_owner_report": None, "owner_belief": None, "drift": None,
            "snapshot_error": None, "hazards": None, "pending_orphan": False, "scopes": None}


def session(state, name):
    return state["sessions"].setdefault(name, _session_row())


def pr(state, number):
    return state["prs"].setdefault(str(number), _pr_row())


def attention_id(session_name, kind, pr_number):
    return f"{session_name}:{kind}:{pr_number if pr_number is not None else '-'}"


def add_attention(state, session_name, kind, pr_number, what, now):
    item_id = attention_id(session_name, kind, pr_number)
    if any(i["id"] == item_id for i in state["attention"]):
        return None
    item = {"id": item_id, "session": session_name, "kind": kind, "pr": pr_number,
            "since": now, "what": what, "notified": False, "manual": False}
    state["attention"].append(item)
    return item


def snooze(state, session_name, kind, pr):
    """Clear an item and keep it quiet while the PR's head stays where it is now.
    A head change (any push) drops the snooze and lets the signal re-raise."""
    item_id = attention_id(session_name, kind, pr)
    clear_attention(state, item_id)
    head = (state["prs"].get(str(pr)) or {}).get("head")
    state.setdefault("snoozed", {})[item_id] = head
    return item_id


def snoozed(state, session_name, kind, pr):
    """True while the snooze's pinned head is still the PR's head; drops a stale snooze."""
    item_id = attention_id(session_name, kind, pr)
    sn = state.get("snoozed") or {}
    if item_id not in sn:
        return False
    if sn[item_id] == (state["prs"].get(str(pr)) or {}).get("head"):
        return True
    del sn[item_id]
    return False


def clear_attention(state, item_id):
    before = len(state["attention"])
    state["attention"] = [i for i in state["attention"] if i["id"] != item_id]
    return len(state["attention"]) != before


def _clear_kind(state, session_name, kind):
    state["attention"] = [i for i in state["attention"]
                          if not (i["session"] == session_name and i["kind"] == kind)]


def apply_report(state, report, now):
    new = []
    name = report["session"]
    row = session(state, name)
    for k in ("role", "theme", "driver", "cadence_min", "mode", "note"):
        if report.get(k) is not None:
            row[k] = report[k]
    if report.get("rules") is not None:
        row["rules_ack"] = report["rules"]
    row["last_report"] = now
    row["last_activity"] = now
    row["ladder"] = 0
    for kind in ("stalled", "red", "threads", "gone-question"):      # the session spoke: silence-driven items are moot
        _clear_kind(state, name, kind)
    if report.get("status"):
        row["status"] = report["status"]
        row["needs"] = report.get("needs")
        row["status_detail"] = report.get("status_detail")
    # PR ownership
    reported = sorted(report["prs"])
    for old in row["prs"]:
        if old not in reported and state["prs"].get(str(old), {}).get("owner") == name:
            heirs = _claimants(state, old, exclude={name}, live_only=True) or _claimants(state, old, exclude={name})
            heir = heirs[0] if heirs else None
            hand_off(state, old, heir)
            if heir and not is_live(state, heir):   # only absent sessions list it; the tick raises the orphan once
                state["prs"][str(old)]["pending_orphan"] = True   # GitHub is read, so a merge raises nothing
    row["prs"] = reported
    for number, belief in report["prs"].items():
        p = pr(state, number)
        if p["owner"] not in (None, name):
            other = p["owner"]
            disputed = any(i["kind"] == "ownership-conflict" and i["pr"] == number for i in state["attention"])
            if other in _claimants(state, number, live_only=True) and not disputed:   # a gone owner cannot drive it
                item = add_attention(state, name, "ownership-conflict", number,
                                     f"#{number} reported by both {other} and {name}; {name} now owner", now)
                if item:
                    new.append(item)
        p["owner"] = name
        p["last_owner_report"] = now
        p["owner_belief"] = {"rounds": belief["rounds"], "threads": belief["threads"], "hold": belief["hold"]}
    # attention for waiting-human
    if row["status"] == "waiting-human":
        what = row["needs"] or "waiting on the human"
        named = re.search(r"#(\d+)", what)
        pr_number = int(named.group(1)) if named else (reported[0] if reported else None)
        item = add_attention(state, name, "waiting-human", None, what, now)   # one item per session
        if item is None:
            existing = next(i for i in state["attention"] if i["id"] == attention_id(name, "waiting-human", None))
            if existing["what"] != what:          # a new question: refresh and notify again
                existing.update(what=what, since=now, notified=False)
                item = existing
            existing["pr"] = pr_number
        else:
            item["pr"] = pr_number
        if item:
            new.append(item)
    else:
        _clear_kind(state, name, "waiting-human")
    sweep(state, now)
    state["updated"] = now
    return new


def _owned_open_prs(state, name):
    return [int(n) for n, p in state["prs"].items()
            if p["owner"] == name and p.get("state") in OPEN_PR_STATES]


def _gone(row):
    """Gone from the roster and silent since; a report after leaving proves it is back before the watcher sees it."""
    return row["roster_status"] == "gone" and not (
        row["last_report"] and row["gone_since"] and parse_iso(row["last_report"]) > parse_iso(row["gone_since"]))


def is_live(state, name):
    return name in state["sessions"] and not _gone(state["sessions"][name])


def _settled(state, now):
    return {n for n, r in state["sessions"].items()
            if _gone(r) and r["gone_since"]
            and parse_iso(now) - parse_iso(r["gone_since"]) >= timedelta(minutes=GONE_SWEEP_MIN)}


def _claimants(state, number, exclude=(), live_only=False):
    """Sessions whose last report lists #number, in roster order. The one place that decides who may hold a PR."""
    return [n for n, r in state["sessions"].items()
            if number in r["prs"] and n not in exclude and (not live_only or is_live(state, n))]


def hand_off(state, number, heir):
    """The previous owner's belief is not the heir's; drift waits for the heir's own report."""
    pr(state, number).update(owner=heir, owner_belief=None, last_owner_report=None, drift=None)


def _orphan_what(owned):
    return f"session gone, owns {', '.join('#%d' % n for n in owned)}; reassign?"


def _raise_orphan(state, name, now):
    """Open the absent session's orphan item, or widen its existing one and notify again; None if nothing changed."""
    owned = _owned_open_prs(state, name)
    if not owned:
        return None
    existing = [i for i in state["attention"] if i["session"] == name and i["kind"] == "orphan"]
    if not existing:
        return add_attention(state, name, "orphan", owned[0], _orphan_what(owned), now)
    item = existing[0]
    covered = {int(n) for n in re.findall(r"#(\d+)", item["what"])}
    if item.get("manual") or not set(owned) - covered:
        return None
    item.update(pr=owned[0], what=_orphan_what(owned), notified=False)
    return item


def raise_orphans(state, now, fresh=True):
    """After the GitHub snapshot: a PR a drop handed to an absent session gets an orphan line if it is still open.

    Names only the dropped PR, so an orphan the human cleared stays cleared. Without fresh GitHub
    data (--no-gh, or this PR's fetch failed) the flag waits for a tick that has it.
    """
    new = []
    if not fresh:
        return new
    for number, p in state["prs"].items():
        if not p.get("pending_orphan") or p.get("snapshot_error"):
            continue
        p["pending_orphan"] = False
        owner, n = p["owner"], int(number)
        if not owner or is_live(state, owner) or p.get("state") not in OPEN_PR_STATES:
            continue
        items = [i for i in state["attention"] if i["session"] == owner and i["kind"] == "orphan"]
        if any(n in {int(x) for x in re.findall(r"#(\d+)", i["what"])} for i in items):
            continue
        auto = next((i for i in items if not i.get("manual")), None)
        if auto:
            owned = set(_owned_open_prs(state, owner))
            names = sorted({int(x) for x in re.findall(r"#(\d+)", auto["what"])} & owned | {n})
            auto.update(pr=names[0], what=_orphan_what(names), notified=False)
            item = auto
        else:
            item = add_attention(state, owner, "orphan", n, _orphan_what([n]), now)
        if item and item not in new:
            new.append(item)
    return new


def _taken_over(state, item):
    """The question's PR merged or closed, or passed to a live session: someone else carries it now."""
    p = state["prs"].get(str(item["pr"]), {})
    owner = p.get("owner")
    return p.get("state") not in OPEN_PR_STATES or (owner not in (None, item["session"]) and is_live(state, owner))


def sweep(state, now):
    """Drop attention items the facts have overtaken; runs after every report and roster pass.

    Hand-raised items (`ovsr.py attention`, marked `manual`) are never touched here. A session gone
    GONE_SWEEP_MIN turns its question into a quiet gone-question note and loses its PR claims; an
    orphan item stays while its PRs have no live claimant, so a PR that really lost its owner still surfaces.
    """
    settled = _settled(state, now)
    for name in settled:
        for i in state["attention"]:     # keep the question visible, quietly; it already notified once
            if i["session"] == name and i["kind"] == "waiting-human" and not i.get("manual"):
                named = re.search(r"#(\d+)", i["what"])     # only a PR it asked about and listed can overtake it
                own = int(named.group(1)) if named and int(named.group(1)) in state["sessions"][name]["prs"] else None
                i.update(id=attention_id(name, "gone-question", None), kind="gone-question", pr=own,
                         what=f"gone while waiting on you: {i['what']}", notified=True)
        for number in _owned_open_prs(state, name):
            heirs = _claimants(state, number, live_only=True)    # never to another absent session
            if heirs:
                hand_off(state, number, heirs[0])
    for item in list(state["attention"]):
        if item.get("manual"):
            continue
        if item["kind"] == "ownership-conflict" and item["pr"] is not None \
                and len(_claimants(state, item["pr"], exclude=settled)) <= 1:
            state["attention"].remove(item)
        elif item["kind"] == "orphan":
            owned = _owned_open_prs(state, item["session"])
            if not owned:
                state["attention"].remove(item)
            elif set(owned) <= {int(n) for n in re.findall(r"#(\d+)", item["what"])}:
                item.update(pr=owned[0], what=_orphan_what(owned))   # narrowed: no second notification
            # widened: _raise_orphan rewrites it and notifies again
        elif item["kind"] == "gone-question" and item["pr"] is not None and _taken_over(state, item):
            state["attention"].remove(item)


def apply_roster(state, roster, now):
    new = []
    seen = set()
    roster = [a for a in roster if a["status"] != "gone"]    # listed but gone is absent: take the gone transition below
    for a in roster:
        name = a["name"]
        seen.add(name)
        row = session(state, name)
        row["kind"] = a.get("kind")
        row["cwd"] = a.get("cwd") or row["cwd"]
        row["started_at"] = a.get("started_at") or row["started_at"]
        if row["roster_status"] != a["status"]:
            row["last_activity"] = now
        row["roster_status"] = a["status"]
        row["gone_since"] = None
        _clear_kind(state, name, "orphan")
        _clear_kind(state, name, "gone-question")    # back on the roster: a live session re-raises what it still needs
        if a["status"] == "waiting":
            already = any(i["session"] == name and i["kind"] in ("waiting-bnaylor", "waiting-human") for i in state["attention"])
            if not already:                          # the session announced this stall itself; one item is enough
                owned = _owned_open_prs(state, name)
                item = add_attention(state, name, "waiting", owned[0] if owned else None,
                                     "parked on a question or prompt", now)
                if item:
                    new.append(item)
        else:
            _clear_kind(state, name, "waiting")
    for name, row in state["sessions"].items():
        if name in seen or (_gone(row) and row["gone_since"]):   # reported since: marked afresh once the report ages
            continue
        recent = row["last_report"] and parse_iso(now) - parse_iso(row["last_report"]) < timedelta(minutes=RECENT_REPORT_MIN)
        if recent:                                   # it just spoke; the watcher has not caught up yet
            continue
        row["roster_status"] = "gone"
        row["gone_since"] = now
        _clear_kind(state, name, "waiting")
        item = _raise_orphan(state, name, now)
        if item:
            new.append(item)
    sweep(state, now)
    state["updated"] = now
    return new


def retire(state, name, now):
    row = state["sessions"].pop(name, None)
    if row is None:
        return False
    state["attention"] = [i for i in state["attention"] if i["session"] != name]
    state["retired"].append({"session": name, "role": row["role"], "theme": row["theme"],
                             "retired_at": now, "prs_at_exit": row["prs"]})
    for number, p in state["prs"].items():
        if p["owner"] == name:
            hand_off(state, number, None)
    return True


def retire_due(state, now, hours=24, ephemeral_hours=1):
    """Gone sessions retire after `hours`; ones that never reported and own nothing after `ephemeral_hours`."""
    due = []
    for name, row in state["sessions"].items():
        if not _gone(row) or not row["gone_since"] or _owned_open_prs(state, name):
            continue
        grace = ephemeral_hours if row["last_report"] is None else hours
        if parse_iso(row["gone_since"]) <= parse_iso(now) - timedelta(hours=grace):
            due.append(name)
    for name in due:
        retire(state, name, now)
    return due


def append_log(log_path, sender, raw, now):
    with Path(log_path).open("a") as f:
        f.write(json.dumps({"ts": now, "from": sender, "raw": raw}) + "\n")


def rebuild_from_log(log_path):
    state = empty_state()
    path = Path(log_path)
    if not path.exists():
        return state
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        report = parse_report(entry["raw"])
        if report:
            apply_report(state, report, entry["ts"])
    return state

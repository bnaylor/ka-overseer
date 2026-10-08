"""Render state.json (+ roster.json) to a static dashboard. Ages are computed client-side."""
import argparse
import html
import json
import os
import re
import shlex
import sys
from pathlib import Path

import state as S
from config import CFG
REPO = CFG["repo"]

PR_URL = "https://github.com/" + REPO + "/pull/{n}"
PR_REF = re.compile(r"#(\d+)")

# dataviz status palette; every status also carries an icon + word, never colour alone.
CSS = """
:root{--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--line:#e6e5e1;
--good:#0ca30c;--warn:#fab219;--serious:#ec835a;--critical:#d03b3b;--band:#fbe9e9;--goodband:#e9f7e9;--tint:#f4f3f0}
@media(prefers-color-scheme:dark){:root{--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--line:#33332f;--band:#3a1f1f;--goodband:#1f2f1f;--tint:#232320}}
body{margin:0;padding:16px 20px;background:var(--surface);color:var(--ink);font:13px/1.4 -apple-system,Helvetica,Arial,sans-serif}
h1{font-size:16px;margin:0 0 8px}h2{font-size:13px;color:var(--ink2);margin:18px 0 6px;text-transform:uppercase;letter-spacing:.04em}
h3{font-size:12px;color:var(--ink2);margin:12px 0 4px;font-weight:600}
.attention{border-radius:6px;padding:8px 12px;margin-bottom:6px}
.attention.red{background:var(--band);border-left:4px solid var(--critical)}
.attention.green{background:var(--goodband);border-left:4px solid var(--good);color:var(--ink2)}
.attention.note{background:var(--tint);border-left:4px solid var(--muted);color:var(--ink2)}
.attention table{margin:0}
table{border-collapse:collapse;width:100%}th{text-align:left;color:var(--muted);font-weight:500;padding:4px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:4px 8px;border-bottom:1px solid var(--line);white-space:nowrap;max-width:28em;overflow:hidden;text-overflow:ellipsis;vertical-align:top}
tr.gone td{color:var(--muted)}tr.ready td{background:rgba(204,85,0,.22)}tr.humansdone td{background:rgba(20,90,40,.35)}tr.humansdone td:first-child{border-left:4px solid #1d5c2e}tr.ready td:first-child{border-left:4px solid #cc5500}tr.waiting td:first-child{border-left:4px solid var(--critical)}
.s-good{color:var(--good)}.s-warn{color:var(--serious)}.s-bad{color:var(--critical)}.muted{color:var(--muted)}
.cap{color:var(--critical);font-weight:600}.stale{color:var(--serious);font-weight:600}
a{color:inherit}footer{margin-top:18px;color:var(--muted)}
#balloon{position:fixed;z-index:10;display:none;max-width:44em;padding:8px 10px;border-radius:6px;background:var(--tint);color:var(--ink);
border:1px solid var(--line);box-shadow:0 4px 14px rgba(0,0,0,.25);white-space:pre-wrap;pointer-events:none}
"""

JS = """
function age(ts){if(!ts)return '—';const d=(Date.now()-Date.parse(ts))/1000;if(d<0)return 'in '+age(new Date(Date.now()+d*1000).toISOString());
if(d<3600)return Math.floor(d/60)+'m';if(d<86400)return Math.floor(d/3600)+'h'+Math.floor((d%3600)/60)+'m';return Math.floor(d/86400)+'d'+Math.floor((d%86400)/3600)+'h';}
function tick(){document.querySelectorAll('[data-ts]').forEach(e=>{e.textContent=age(e.dataset.ts);
const lim=parseFloat(e.dataset.stale||'0');if(lim&&(Date.now()-Date.parse(e.dataset.ts))/60000>lim)e.classList.add('stale');});}
tick();setInterval(tick,15000);
// hover a session row for 1s to see its full needs / note
(function(){const b=document.createElement('div');b.id='balloon';document.body.appendChild(b);let t=null,x=0,y=0;
document.querySelectorAll('tr[data-note]').forEach(r=>{
r.addEventListener('mouseenter',()=>{clearTimeout(t);t=setTimeout(()=>{b.textContent=r.dataset.note;b.style.display='block';
const w=b.offsetWidth,h=b.offsetHeight;b.style.left=Math.min(x+12,innerWidth-w-8)+'px';b.style.top=Math.min(y+14,innerHeight-h-8)+'px';},1000);});
r.addEventListener('mousemove',ev=>{x=ev.clientX;y=ev.clientY;});
r.addEventListener('mouseleave',()=>{clearTimeout(t);b.style.display='none';});});})();
"""

ROSTER_ICON = {"busy": "● busy", "shell": "▶ shell", "idle": "○ idle", "waiting": "⏸ waiting", "gone": "✕ gone", None: "—"}
CHECK_ICON = {"green": ("✓ green", "s-good"), "red": ("✗ red", "s-bad"), "pending": ("… pending", "s-warn"), "none": ("– none", "muted"), None: ("—", "muted")}
STATUS_ICON = {"working": "▸ working", "waiting-human": "⚑ waiting-human", "waiting-review": "◷ waiting-review",
               "idle": "○ idle", "gone": "✕ gone", None: "—"}


def e(x):
    return html.escape("" if x is None else str(x))


def pr_link(n):
    return f'<a href="{PR_URL.format(n=n)}">#{n}</a>'


def L(x):
    """Escape, then turn every #NNNN into a link to that PR."""
    return PR_REF.sub(lambda m: pr_link(m.group(1)), e(x))


def ts(x, stale_min=None):
    if not x:
        return "—"
    extra = f' data-stale="{stale_min}"' if stale_min else ""
    return f'<span data-ts="{e(x)}"{extra}>{e(x)}</span>'


def yn(v):
    return "yes" if v else ("no" if v is False else "—")


NOTE_KINDS = {"gone-question"}     # shown, but nothing can answer it: no red band


def _attention_table(items, cls):
    rows = "".join(
        f"<tr><td>{e(i['session'])}</td><td>{pr_link(i['pr']) if i['pr'] else '—'}</td>"
        f"<td>{ts(i['since'])}</td><td>{e(i['kind'])}</td><td title=\"{e(i['what'])}\">{L(i['what'])}</td></tr>"
        for i in items)
    return (f'<div class="attention {cls}"><table><tr><th>session</th><th>PR</th><th>for</th><th>kind</th><th>what</th></tr>'
            f"{rows}</table></div>")


def _attention(state):
    items = sorted(state["attention"], key=lambda i: i["since"])
    loud = [i for i in items if i["kind"] not in NOTE_KINDS]
    notes = [i for i in items if i["kind"] in NOTE_KINDS]
    head = _attention_table(loud, "red") if loud else '<div class="attention green">✓ Nothing is waiting on you.</div>'
    return head + (_attention_table(notes, "note") if notes else "")


def _sessions(state, roster):
    live = {a["name"]: a["status"] for a in (roster or [])}
    out = []
    for name in sorted(state["sessions"]):
        r = state["sessions"][name]
        rs = r["roster_status"]
        if roster is not None:
            rs = live.get(name, "gone" if rs else None)
        if rs == "gone" and r["roster_status"] == "gone" and S.is_live(state, name):
            rs = None                    # reported since it left; the roster has not caught up
        cls = "gone" if rs == "gone" else ("waiting" if rs == "waiting" else "")
        cadence = r["cadence_min"]
        stale = cadence * 2 if cadence else None
        driver = f"{r['driver']}/{cadence}m" if r["driver"] and cadence else (r["driver"] or "—")
        prs = " ".join(pr_link(n) for n in r["prs"]) or "—"
        note = r["needs"] or r["status_detail"] or r["note"] or ""
        data_note = f' data-note="{e(note)}"' if note else ""
        out.append(
            f'<tr class="row {cls}"{data_note}><td>{e(name)}</td><td>{e(r["role"] or "—")}</td><td>{L(r["theme"] or "—")}</td>'
            f"<td>{e(driver)}</td><td>{ROSTER_ICON.get(rs, e(rs))}</td><td>{STATUS_ICON.get(r['status'], e(r['status']))}</td>"
            f"<td>{prs}</td><td>{ts(r['last_report'], stale)}</td><td>{ts(r['last_activity'])}</td>"
            f"<td>{e(r['rules_ack'] or '—')}</td><td title=\"{e(r['needs'] or r['status_detail'] or r['note'])}\">{L(r['needs'] or r['status_detail'] or r['note'] or '')}</td></tr>")
    return ("<table><tr><th>session</th><th>role</th><th>theme</th><th>driver</th><th>roster</th><th>reported</th>"
            "<th>PRs</th><th>last report</th><th>last activity</th><th>rules</th><th>needs / note</th></tr>"
            + "".join(out) + "</table>")


def _pr_sort_key(item):
    n, p = item
    open_ = 0 if p.get("state") in S.OPEN_PR_STATES else 1
    gate = (p.get("unresolved_threads") or 0) + (0 if p.get("checks") == "green" else 1)
    return (open_, gate, -int(n))


def ready_for_human(p):
    """Everything a merge needs except the human lgtm: green, no open threads, mergeable, unheld, not draft."""
    return (p.get("checks") == "green" and (p.get("unresolved_threads") or 0) == 0
            and p.get("mergeable") == "MERGEABLE" and not p.get("hold") and not p.get("draft")
            and not p.get("lgtm"))


def humans_done(p):
    """Every human sign-off is in (lgtm + approved, no hold, no open threads); only CI, smokes or Tide are left."""
    return bool(p.get("lgtm") and p.get("approved") and not p.get("hold") and not p.get("draft")
                and (p.get("unresolved_threads") or 0) == 0)


# one group per search in config `scope_queries`, in config order; a PR goes in the first group whose search found it
GROUP_TITLES = {"mine": "Mine", "review_requested": "Review requested from me", "reviewed": "Reviewed by me"}
OTHER_GROUP = "Other: no search finds it"            # a session reports it, or it dropped out of every search
UNSORTED = "_unsorted"
UNSORTED_GROUP = "Not sorted yet: the next tick that reads GitHub sorts these"


def _group(p):
    if p.get("scopes") is None:                        # saved before searches were recorded
        return UNSORTED
    return next((name for name in CFG["scope_queries"] if name in p["scopes"]), None)


def _prs(state):
    groups = {name: [] for name in CFG["scope_queries"]}
    groups[None] = []
    groups[UNSORTED] = []
    for item in sorted(state["prs"].items(), key=_pr_sort_key):
        if item[1].get("state") in S.OPEN_PR_STATES:
            groups[_group(item[1])].append(item)
    title = lambda name: OTHER_GROUP if name is None else UNSORTED_GROUP if name == UNSORTED else GROUP_TITLES.get(name, name)
    out = [f"<h3>{e(title(name))} ({len(items)})</h3>" + (_pr_table(items) if items else '<p class="muted">None open.</p>')
           for name, items in groups.items() if items or name in CFG["scope_queries"]]   # every search's group shows, even empty
    return "".join(out)


def _pr_table(items):
    out = []
    for n, p in items:
        chk, chk_cls = CHECK_ICON.get(p.get("checks"), ("—", "muted"))
        rounds = p.get("rounds")
        rounds_html = f'<span class="cap">{rounds}</span>' if (rounds or 0) >= 6 else e(rounds if rounds is not None else "—")
        title = f'<a href="{e(p["url"]) if p.get("url") else PR_URL.format(n=n)}">#{n}</a> {e(p["title"] or "")}'
        err = f' title="snapshot error: {e(p["snapshot_error"])}"' if p.get("snapshot_error") else ""
        if ready_for_human(p):
            err += ' class="ready" title="ready: only the human lgtm is missing"' if not err else ' class="ready"'
        elif humans_done(p):
            err += ' class="humansdone" title="humans done: waiting on CI, smokes or Tide"' if not err else ' class="humansdone"'
        out.append(
            f"<tr{err}><td>{title}</td><td>{e(p['owner'] or '—')}</td><td>{e(p['mergeable'] or '—')}</td>"
            f'<td class="{chk_cls}">{chk}</td><td>{e(p["unresolved_threads"] if p["unresolved_threads"] is not None else "—")}</td>'
            f'<td>{rounds_html}</td><td class="muted">{e(p["advisory_rounds"] if p["advisory_rounds"] is not None else "—")}</td>'
            f"<td>{yn(p['hold'])}</td><td>{yn(p['lgtm'])}</td><td>{yn(p['approved'])}</td>"
            f"<td>{e(', '.join(p['reviewers']) or '—')}</td><td>{ts(p['last_activity'])}</td>"
            f"<td title=\"{e(p['drift'])}\">{L(p['drift'] or '')}</td></tr>")
    return ("<table><tr><th>PR</th><th>owner</th><th>mergeable</th><th>checks</th><th>threads</th><th>rounds</th>"
            "<th>kyber</th><th>hold</th><th>lgtm</th><th>approved</th><th>reviewers</th><th>activity</th><th>drift</th></tr>"
            + "".join(out) + "</table>")


WT_ICON = {"cleanup": ("⌫ clean up", "s-warn"), "missing": ("✕ missing", "s-warn"), "check": ("⚑ check", "s-bad"),
           "unknown": ("– unknown", "muted"), "in use": ("● in use", "s-good")}
HOME = os.path.expanduser("~")


def _short(path):
    return "~" + path[len(HOME):] if path and path.startswith(HOME + "/") else (path or "—")


def _wt_command(r):
    q = shlex.quote
    if r["status"] == "missing":
        return f"git -C {q(r['main'])} worktree prune"
    if r["status"] == "cleanup":
        return f"git -C {q(r['main'])} worktree remove {q(r['path'])}"
    return ""


def _worktrees(state):
    wt = state.get("worktrees")
    if not wt:
        return '<p class="muted">Not scanned yet. The next Overseer tick that reads GitHub fills this in.</p>'
    rows = wt.get("rows") or []
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in WT_ICON}
    head = " · ".join(f"{n} {WT_ICON[s][0].split(' ', 1)[1]}" for s, n in counts.items() if n) or "no extra worktrees"
    errs = "".join(f'<p class="s-bad">{e(x)}</p>' for x in wt.get("errors") or [])
    out = []
    for r in rows:
        icon, cls = WT_ICON.get(r["status"], (e(r["status"]), "muted"))
        pr = r.get("pr")
        pr_html = (f'<a href="{e(pr["url"])}">#{e(pr["number"])}</a> {e(pr["state"].lower())}' if pr and pr.get("url")
                   else (f'#{e(pr["number"])} {e(pr["state"].lower())}' if pr else "—"))
        owner = r.get("owner")
        owner_html = e(owner) if owner and S.is_live(state, owner) else (f'<span class="muted">{e(owner)} (gone)</span>' if owner else "—")
        branch = (e(r["branch"]) if r["branch"] else '<span class="muted">not tracked by git</span>' if r.get("orphan")
                  else f'<span class="muted">detached {e((r.get("head") or "")[:8])}</span>')
        unsaved = ", ".join(x for x in (f"{r['dirty']} changed" if r.get("dirty") else "",
                                        f"{r['unpushed']} unpushed" if r.get("unpushed") else "",
                                        f"{r['ignored']} ignored" if r.get("ignored") else "") if x) or "—"
        cmd = _wt_command(r)
        out.append(
            f'<tr><td title="{e(r["path"])}">{e(_short(r["path"]))}</td><td>{owner_html}</td><td>{branch}</td>'
            f"<td>{pr_html}</td><td>{e(unsaved)}</td><td>{ts(r.get('last_activity'))}</td>"
            f'<td class="{cls}">{icon}</td><td title="{e(cmd or r.get("why"))}">{e(r.get("why") or "")}</td></tr>')
    table = ("<table><tr><th>worktree</th><th>owner</th><th>branch</th><th>PR</th><th>unsaved</th><th>last change</th>"
             "<th>status</th><th>why</th></tr>" + "".join(out) + "</table>") if out else ""
    return (f'<p class="muted">{e(head)} · scanned {ts(wt.get("scanned"))} ago · hover a reason for the command; '
            f"the Overseer never removes a worktree itself</p>{errs}{table}")


def render(state, roster, now):
    ov = state.get("overseer") or {}
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="60">
<title>Overseer</title><style>{CSS}</style></head><body>
<h1>Overseer</h1>
{_attention(state)}
<h2>Sessions</h2>{_sessions(state, roster)}
<h2>PRs</h2>{_prs(state)}
<h2>Worktrees</h2>{_worktrees(state)}
<footer>Rendered {ts(now)} ago · Rules v{e(state.get('rules_version'))} · Overseer {e(ov.get('session') or '—')} tick {e(ov.get('tick'))} · next wake {ts(ov.get('next_wake')) if ov.get('next_wake') else '—'}</footer>
<script>{JS}</script></body></html>"""


def main(argv=None):
    here = Path(__file__).parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(here / "state.json"))
    ap.add_argument("--roster", default=str(here / "roster.json"))
    ap.add_argument("--out", default=str(here / "dashboard.html"))
    a = ap.parse_args(argv)
    state = S.load_state(a.state)
    roster = None
    if Path(a.roster).exists():
        try:
            roster = json.loads(Path(a.roster).read_text()).get("sessions")
        except ValueError:
            roster = None
    S.write_atomic(a.out, render(state, roster, S.now_iso()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

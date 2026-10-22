"""Find the extra git worktrees under the watched folders, name an owner, and say whether each can go.

Read-only: nothing here removes a worktree. git runs here; gh runs in gh_snapshot.py.
"""
import json
import os
import re
import subprocess
from datetime import timedelta
from pathlib import Path

import state as S
from config import CFG

STALE_DAYS = 3                  # no commit or file change this long, and no open PR: nobody is working in it
FINISHED = {"MERGED", "CLOSED"}
STATUS_ORDER = ["cleanup", "missing", "check", "unknown", "in use"]


def default_roots():
    return CFG.get("worktree_roots") or CFG.get("cwd_prefixes") or []


def _git(path, *args):
    # --no-optional-locks: `status` must not refresh the index, or the scan itself would look like activity
    out = subprocess.run(["git", "--no-optional-locks", "-C", str(path), *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"git {args[0]} exit {out.returncode}")
    return out.stdout


def main_clones(roots):
    """Each root, and each folder directly inside one, whose .git is a directory. A worktree's .git is a file."""
    found = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for p in [root, *sorted(c for c in root.iterdir() if c.is_dir())]:
            if (p / ".git").is_dir():
                found.append(p)
    return found


def orphan_folders(roots):
    """Folders directly inside a root whose .git file points at a worktree record git has already pruned.
    `git worktree list` no longer shows them, so only their own .git file gives them away."""
    found = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for p in sorted(c for c in root.iterdir() if c.is_dir() and (c / ".git").is_file()):
            try:
                text = (p / ".git").read_text().strip()
            except OSError:
                continue
            if not text.startswith("gitdir:"):
                continue
            gitdir = text.split(":", 1)[1].strip()
            gitdir = gitdir if os.path.isabs(gitdir) else str((p / gitdir).resolve())
            if not os.path.isdir(gitdir):
                main = gitdir.split("/.git/worktrees/")[0] if "/.git/worktrees/" in gitdir else None
                found.append((p, main))
    return found


def parse_worktree_list(text):
    rows, cur = [], None
    for line in text.splitlines() + [""]:
        if not line:
            if cur:
                rows.append(cur)
            cur = None
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur = {"path": val, "head": None, "branch": None, "bare": False, "locked": None, "prunable": None}
        elif cur is None:
            continue
        elif key == "HEAD":
            cur["head"] = val
        elif key == "branch":
            cur["branch"] = val.removeprefix("refs/heads/")
        elif key == "bare":
            cur["bare"] = True
        elif key == "locked":
            cur["locked"] = val or "locked"
        elif key == "prunable":
            cur["prunable"] = val or "prunable"
    return rows


def repo_slug(url):
    m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url or "")
    return m.group(1) if m else None


def _remote(main, name):
    try:
        return repo_slug(_git(main, "config", "--get", f"remote.{name}.url").strip())   # raw: insteadOf rewrites do not apply
    except RuntimeError:
        return None


# ignored files under these are rebuilt by tools; any other ignored file (install.env, terraform.tfvars, .env,
# local terraform state) exists only here and `git worktree remove` deletes it without a warning
CACHE_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules", ".terraform", ".venv",
               "venv", ".tox", ".DS_Store"}
CACHE_SUFFIXES = (".pyc", ".pyo")


def parse_status_z(text):
    """(code, path) per entry of `status --porcelain -z`; a rename's original path is skipped."""
    items, out, i = text.split("\0"), [], 0
    while i < len(items):
        entry = items[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            i += 1
        out.append((code, path))
    return out


def _is_cache(path):
    parts = [x for x in path.split("/") if x]
    return any(x in CACHE_PARTS for x in parts) or path.endswith(CACHE_SUFFIXES)


def _mtime_iso(path):
    try:
        return S.iso_from_ts(os.stat(path).st_mtime)
    except OSError:
        return None


def local_facts(path):
    """Work that only exists in this folder, and the latest sign of work: last commit, staging, or an edited file.

    Flags are explicit so repo config (status.showUntrackedFiles=no) cannot hide files."""
    entries = parse_status_z(_git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                                  "--ignored=matching", "--ignore-submodules=none"))
    changed = [p for code, p in entries if code != "!!"]
    ignored = [p for code, p in entries if code == "!!" and not _is_cache(p)]
    hidden = [l for l in _git(path, "ls-files", "-v").splitlines() if l[:1].islower() or l[:2] == "S "]
    times = []
    try:
        times.append(S.normalize_iso(_git(path, "log", "-1", "--format=%cI", "HEAD").strip()))
    except RuntimeError:
        pass                                   # a fresh worktree with no commit yet
    index = _git(path, "rev-parse", "--git-path", "index").strip()
    times.append(_mtime_iso(index if os.path.isabs(index) else os.path.join(path, index)))
    for rel in (changed + ignored)[:200]:
        times.append(_mtime_iso(os.path.join(path, rel)))
    times = [t for t in times if t]
    return {"dirty": len(changed) + len(hidden), "ignored": len(ignored), "ignored_sample": ignored[:3],
            "last_activity": max(times) if times else None}


def unpushed(path, pr_head=None, detached=False):
    """Commits on HEAD that no remote branch has. The PR's head counts as pushed: a merged PR's branch is often
    deleted on GitHub and pruned locally, which would otherwise make every merged commit look unpushed.

    A detached worktree also counts commits it left behind: its own HEAD reflog is the only thing that still
    reaches them, and `git worktree remove` deletes that reflog."""
    starts = ["HEAD"]
    if detached:
        starts += list(dict.fromkeys(_git(path, "log", "-g", "--format=%H", "HEAD").split()))[:500]
    tips = ["--remotes"] + (["--branches"] if detached else [])
    if pr_head:
        try:
            _git(path, "cat-file", "-e", f"{pr_head}^{{commit}}")
            tips.append(pr_head)
        except RuntimeError:
            pass                               # never fetched here: fall back to remote branches alone
    return int(_git(path, "rev-list", "--count", *starts, "--not", *tips).strip() or 0)


def beyond(path, pr_head):
    """Commits on HEAD that the PR never had: follow-up work on the same branch after it merged or closed."""
    try:
        _git(path, "cat-file", "-e", f"{pr_head}^{{commit}}")
    except RuntimeError:
        return 0                               # not fetched here: cannot tell, and unpushed still guards the work
    return int(_git(path, "rev-list", "--count", "HEAD", "--not", pr_head).strip() or 0)


def scan(roots):
    """Every extra worktree of every main clone under `roots`. The main folder itself is never listed."""
    rows, errors, seen = [], [], set()
    for main in main_clones(roots):
        try:
            listing = parse_worktree_list(_git(main, "worktree", "list", "--porcelain"))
        except RuntimeError as e:
            errors.append(f"{main}: {e}")
            continue
        origin = _remote(main, "origin")
        base = _remote(main, "upstream") or origin
        for wt in listing[1:]:                 # git lists the main folder first
            if wt["bare"] or wt["path"] in seen:
                continue
            seen.add(wt["path"])
            row = {"path": wt["path"], "main": str(main), "repo": base, "fork_owner": origin.split("/")[0] if origin else None,
                   "branch": wt["branch"], "head": wt["head"], "locked": wt["locked"],
                   "missing": bool(wt["prunable"]) or not os.path.isdir(wt["path"]),
                   "dirty": 0, "ignored": 0, "ignored_sample": [], "unpushed": 0, "beyond_pr": 0,
                   "last_activity": None, "error": None}
            if not row["missing"]:
                try:
                    row.update(local_facts(wt["path"]))
                    row["unpushed"] = unpushed(wt["path"], detached=not wt["branch"])
                except RuntimeError as e:
                    row["error"] = str(e)
            rows.append(row)
    for path, main in orphan_folders(roots):
        if str(path) not in seen:
            rows.append({"path": str(path), "main": main or "", "repo": None, "fork_owner": None, "branch": None,
                         "head": None, "locked": None, "missing": False, "orphan": True,
                         "dirty": 0, "ignored": 0, "ignored_sample": [], "unpushed": 0, "beyond_pr": 0,
                         "last_activity": _mtime_iso(path), "error": None})
    return rows, errors


def _under(path, folder):
    return bool(folder) and (path == folder or path.startswith(folder.rstrip("/") + "/"))


def owner_of(state, row):
    """The session that owns the worktree's PR; else the session working in the folder that holds it."""
    pr = row.get("pr")
    if pr and pr.get("repo") == CFG["repo"]:
        p = state["prs"].get(str(pr["number"]))
        if p and p.get("owner"):
            return p["owner"]
        claim = S._claimants(state, pr["number"], live_only=True)
        if claim:
            return claim[0]
    overseer = (state.get("overseer") or {}).get("session")
    for target in (row["path"], row["main"]):
        hits = [(n, r) for n, r in state["sessions"].items() if _under(target, r.get("cwd"))]
        if hits:                               # deepest folder first, then live sessions, then not the Overseer
            hits.sort(key=lambda h: (-len(h[1]["cwd"]), not S.is_live(state, h[0]), h[0] == overseer, h[0]))
            return hits[0][0]
    return None


def classify(row, now):
    """One of STATUS_ORDER, plus the reason in words."""
    if row.get("orphan"):
        return "check", "git no longer tracks this folder (its main clone dropped the worktree record); look inside, then delete it by hand"
    if row["missing"]:
        return "missing", "folder no longer exists; `git worktree prune` in the main clone drops it, or `git worktree repair` if it moved"
    if row["error"]:
        return "unknown", f"git failed: {row['error']}"
    pr = row.get("pr")
    if pr is None and row.get("pr_error"):
        return "unknown", f"PR lookup failed ({row['pr_error']}); the next tick retries"
    if pr and pr["state"] == "OPEN":
        return "in use", f"PR #{pr['number']} open"
    if row.get("live_here"):
        return "in use", f"{', '.join(row['live_here'])} is working in this folder"
    finished = bool(pr) and pr["state"] in FINISHED and not row.get("beyond_pr")
    last = row["last_activity"]
    idle = last is not None and S.parse_iso(now) - S.parse_iso(last) >= timedelta(days=STALE_DAYS)
    if not finished and not idle:
        return "in use", "no PR yet; changed recently" if last else "no PR yet"
    if finished:
        reason = f"PR #{pr['number']} {pr['state'].lower()}"
    elif pr and row.get("beyond_pr"):
        n = row["beyond_pr"]
        reason = f"{n} commit{'s' if n != 1 else ''} after PR #{pr['number']} {pr['state'].lower()}, idle {(S.parse_iso(now) - S.parse_iso(last)).days}d"
    else:
        reason = f"{'detached, ' if not row['branch'] else ''}no PR, idle {(S.parse_iso(now) - S.parse_iso(last)).days}d"
    unsaved = []
    if row["dirty"]:
        unsaved.append(f"{row['dirty']} uncommitted change{'s' if row['dirty'] != 1 else ''}")
    if row["unpushed"]:
        unsaved.append(f"{row['unpushed']} unpushed commit{'s' if row['unpushed'] != 1 else ''}")
    if row.get("ignored"):
        sample = ", ".join(row.get("ignored_sample") or [])
        unsaved.append(f"{row['ignored']} gitignored file{'s' if row['ignored'] != 1 else ''} only here"
                       + (f" ({sample}{', ...' if row['ignored'] > len(row.get('ignored_sample') or []) else ''})" if sample else ""))
    if unsaved:
        return "check", f"{reason}; {', '.join(unsaved)}"
    if row["locked"]:
        return "check", f"{reason}; locked ({row['locked']})"
    return "cleanup", f"{reason}; nothing unsaved"


KEEP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worktree-keep.json")


def load_keep(path=None):
    """{realpath: reason} for worktrees an owner said to keep (durable checkouts and the like).
    A missing file means nothing is kept; see worktree-keep.example.json for the shape."""
    try:
        with open(path or KEEP_FILE) as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    return {os.path.realpath(os.path.expanduser(k)): v for k, v in raw.items()}


def refresh(state, now, find_prs, roots=None, keep_path=None):
    """Scan, attach PRs, owners and status, and store the result in state["worktrees"]. Returns the rows.

    find_prs(rows) -> ({(repo, branch): pr}, {repo or (repo, branch): error}); see gh_snapshot.branch_prs.
    """
    rows, errors = scan(default_roots() if roots is None else roots)
    keep = load_keep(keep_path)
    previous = {(r["path"], r["branch"]): r.get("pr") for r in (state.get("worktrees") or {}).get("rows", [])}
    prs, pr_errors = find_prs([r for r in rows if r["branch"] and r["repo"]])
    for r in rows:
        key = (r["repo"], r["branch"])
        r["pr"] = prs.get(key) if r["branch"] else None
        r["pr_error"] = (pr_errors.get(key) or pr_errors.get(r["repo"])) if r["branch"] and key not in prs else None
        if r["branch"] and not r["repo"] and not r.get("orphan"):
            r["pr_error"] = "the clone's remote is not a GitHub repo"
        if r["pr"] is None and r["pr_error"]:
            r["pr"] = previous.get((r["path"], r["branch"]))   # GitHub hiccup: keep what the last tick knew
        if r["unpushed"] and r["pr"] and r["pr"].get("head_oid") and not r["missing"]:
            try:
                r["unpushed"] = unpushed(r["path"], r["pr"]["head_oid"])
            except RuntimeError as e:
                r["error"] = str(e)
        if r["pr"] and r["pr"].get("state") in FINISHED and r["pr"].get("head_oid") and not r["missing"] \
                and r["head"] != r["pr"]["head_oid"]:
            try:
                r["beyond_pr"] = beyond(r["path"], r["pr"]["head_oid"])
            except RuntimeError as e:
                r["error"] = str(e)
        r["live_here"] = sorted(n for n, s in state["sessions"].items() if S.is_live(state, n) and _under(s.get("cwd") or "", r["path"]))
        r["owner"] = owner_of(state, r)
        r["status"], r["why"] = classify(r, now)
        kept = keep.get(os.path.realpath(r["path"]))
        if kept and r["status"] in ("check", "cleanup"):   # an owner said it stays; never offer it for removal
            r["status"], r["why"] = "in use", f"kept: {kept}"
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["status"]), r["path"]))
    pr_msgs = [f"{k if isinstance(k, str) else ' '.join(k)}: {v}" for k, v in pr_errors.items()]
    state["worktrees"] = {"scanned": now, "rows": rows, "errors": errors + pr_msgs}
    return rows

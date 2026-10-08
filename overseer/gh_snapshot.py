"""The only place gh is called. Writes the prs part of state.json and looks up PRs for worktree branches."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import state as S

from config import CFG

REPO = CFG["repo"]
GATING_BOT = CFG["gating_bot"]
ADVISORY_BOT = CFG["advisory_bot"]
ROUND_CAP = int(CFG["round_cap"])
PR_FIELDS = "title,headRefOid,headRefName,url,state,isDraft,mergeable,labels,reviews,reviewRequests,statusCheckRollup,updatedAt,commits,comments"
THREADS_QUERY = ("query($o:String!,$r:String!,$n:Int!,$after:String){repository(owner:$o,name:$r)"
                 "{pullRequest(number:$n){reviewThreads(first:100,after:$after)"
                 "{nodes{isResolved} pageInfo{hasNextPage endCursor}}}}}")
GOOD = {"SUCCESS", "SKIPPED", "NEUTRAL"}
BAD = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}


def _run(cmd):
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"exit {out.returncode}")
    return out.stdout


IGNORED_CONTEXTS = set(CFG["ignored_check_contexts"])  # e.g. tide: merge-pool state, PENDING until merge, not a CI check


def _latest_runs(rollup):
    """A re-run check leaves its cancelled or failed predecessor in the roll-up; keep the latest run per name."""
    latest = {}
    for c in rollup:
        if c.get("__typename") == "CheckRun" and c.get("name"):
            key = ("run", c["name"])
            if key not in latest or (c.get("startedAt") or "") > (latest[key].get("startedAt") or ""):
                latest[key] = c
        else:
            latest[("ctx", c.get("context"), id(c))] = c
    return list(latest.values())


def checks_state(rollup):
    rollup = _latest_runs([c for c in (rollup or []) if c.get("context") not in IGNORED_CONTEXTS])
    if not rollup:
        return "none"
    red = pending = False
    for c in rollup:
        if c.get("__typename") == "StatusContext":
            st = c.get("state")
            if st in ("FAILURE", "ERROR"):
                red = True
            elif st in ("PENDING", "EXPECTED"):
                pending = True
        else:
            if c.get("status") != "COMPLETED":
                pending = True
            elif c.get("conclusion") in BAD:
                red = True
    return "red" if red else "pending" if pending else "green"


def last_activity(pr):
    """The latest real event: a commit, a comment, or a review. updatedAt moves for invisible reasons."""
    events = [c.get("committedDate") for c in pr.get("commits") or []]
    events += [c.get("createdAt") for c in pr.get("comments") or []]
    events += [r.get("submittedAt") for r in pr.get("reviews") or []]
    events = [e for e in events if e]
    return max(events) if events else pr.get("updatedAt")


def _rounds(reviews, login):
    """One round per distinct commit the bot reviewed; a duplicate submission on the same commit is not a new round."""
    commits = set()
    n = 0
    for r in reviews:
        if ((r.get("author") or {}).get("login")) != login:
            continue
        oid = (r.get("commit") or {}).get("oid")
        if oid is None:
            n += 1
        elif oid not in commits:
            commits.add(oid)
            n += 1
    return n


def classify_pr(pr, unresolved):
    labels = {l["name"] for l in pr.get("labels", [])}
    reviews = pr.get("reviews", [])
    return {
        "title": pr.get("title"),
        "head": (pr.get("headRefOid") or "")[:8] or None,
        "branch": pr.get("headRefName"),
        "url": pr.get("url"),
        "state": pr.get("state"),
        "draft": bool(pr.get("isDraft")),
        "mergeable": pr.get("mergeable"),
        "checks": checks_state(pr.get("statusCheckRollup") or []),
        "unresolved_threads": unresolved,
        "rounds": _rounds(reviews, GATING_BOT),
        "advisory_rounds": _rounds(reviews, ADVISORY_BOT),
        "hold": "do-not-merge/hold" in labels,
        "lgtm": "lgtm" in labels,
        "approved": "approved" in labels,
        "reviewers": [r.get("login") or r.get("name") for r in pr.get("reviewRequests", [])],
        "last_activity": last_activity(pr),
        "updated_at": pr.get("updatedAt"),
    }


def compute_drift(row):
    b = row.get("owner_belief")
    if not b:
        return None
    parts = []
    if b.get("rounds") is not None and row.get("rounds") is not None and b["rounds"] != row["rounds"]:
        parts.append(f"owner says rounds {b['rounds']}, GitHub {row['rounds']}")
    if b.get("threads") is not None and row.get("unresolved_threads") is not None and b["threads"] != row["unresolved_threads"]:
        parts.append(f"owner says threads {b['threads']}, GitHub {row['unresolved_threads']}")
    if b.get("hold") is not None and row.get("hold") is not None and b["hold"] != row["hold"]:
        parts.append("owner says hold, GitHub has no hold" if b["hold"] else "owner says no hold, GitHub has hold")
    return "; ".join(parts) or None


def fetch_pr(number):
    return json.loads(_run(["gh", "pr", "view", str(number), "-R", REPO, "--json", PR_FIELDS]))


def _graphql_threads(variables):
    cmd = ["gh", "api", "graphql", "-f", f"query={THREADS_QUERY}"]
    for k, v in variables.items():
        if v is not None:
            cmd += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
    return json.loads(_run(cmd))


def fetch_unresolved(number, graphql=_graphql_threads):
    owner, name = REPO.split("/")
    after, unresolved = None, 0
    while True:
        out = graphql({"o": owner, "r": name, "n": number, "after": after})
        threads = out["data"]["repository"]["pullRequest"]["reviewThreads"]
        unresolved += sum(1 for t in threads["nodes"] if not t["isResolved"])
        if not threads["pageInfo"]["hasNextPage"]:
            return unresolved
        after = threads["pageInfo"]["endCursor"]


def _numbers(extra):
    out = _run(["gh", "pr", "list", "-R", REPO, "--state", "open", *extra, "--json", "number", "--jq", ".[].number"])
    return [int(x) for x in out.split()]


LIST_CMDS = {name: (lambda extra=extra: _numbers(extra)) for name, extra in CFG["scope_queries"].items()}


def scope_numbers(state, list_cmds=LIST_CMDS):
    """Every PR to snapshot. Also records on each PR row which searches found it (`scopes`), for the dashboard's
    groups. A search that fails leaves every row's mark for it as it was."""
    nums = {int(n) for n in state["prs"]}
    for name, fn in list_cmds.items():
        try:
            hits = set(fn())
        except RuntimeError as e:
            print(f"scope list failed: {e}", file=sys.stderr)
            continue
        nums |= hits
        for n in hits:
            S.pr(state, n)
        for n, row in state["prs"].items():
            marks = [s for s in (row["scopes"] or []) if s != name]
            row["scopes"] = marks + [name] if int(n) in hits else marks
    return nums


MERGEABLE_RETRY_WAIT = 5   # seconds; GitHub computes mergeability lazily, so the first read after main moves says UNKNOWN


def snapshot(state, numbers, fetch_pr=fetch_pr, fetch_unresolved=fetch_unresolved, now=None, sleep=time.sleep):
    now = now or S.now_iso()
    result = {"updated": [], "errors": {}}
    for n in sorted(numbers):
        row = S.pr(state, n)
        try:
            prev = row.get("rounds")
            row.update(classify_pr(fetch_pr(n), fetch_unresolved(n)))
            row["rounds_prev"] = prev
            row["snapshot_error"] = None
            row["drift"] = compute_drift(row)
            result["updated"].append(n)
        except Exception as e:  # keep the old row, record why
            row["snapshot_error"] = str(e)
            result["errors"][n] = str(e)
    _retry_unknown_mergeable(state, result["updated"], fetch_pr, sleep)
    state["updated"] = now
    return result


def _retry_unknown_mergeable(state, numbers, fetch_pr, sleep):
    """The first read asked GitHub to compute mergeability; one re-read after a short wait usually has it."""
    unknown = [n for n in numbers
               if state["prs"][str(n)].get("state") == "OPEN" and state["prs"][str(n)].get("mergeable") == "UNKNOWN"]
    if not unknown:
        return
    sleep(MERGEABLE_RETRY_WAIT)
    for n in unknown:
        try:
            value = fetch_pr(n).get("mergeable")
        except Exception:
            continue
        if value:
            state["prs"][str(n)]["mergeable"] = value


BRANCH_PR_FIELDS = "number,state,headRefName,headRefOid,headRepositoryOwner,url,updatedAt"


def _pick(candidates):
    """Several PRs can share a branch name over time: the open one wins, else the newest."""
    return max(candidates, key=lambda p: (p["state"] == "OPEN", p.get("updatedAt") or "")) if candidates else None


def branch_prs(rows, run=_run):
    """PRs for worktree branches: {(repo, branch): pr}, plus errors keyed by repo (the list failed) or (repo, branch).

    One `--author @me` list per repo covers most branches; a per-branch query covers PRs someone else opened.
    A PR only matches when its head is on the worktree's fork, so another person's same-named branch never does.
    """
    found, errors = {}, {}
    by_repo = {}
    for r in rows:
        by_repo.setdefault(r["repo"], {})[r["branch"]] = r.get("fork_owner")

    def matches(p, branch, owner):
        head_owner = (p.get("headRepositoryOwner") or {}).get("login")
        return p.get("headRefName") == branch and (owner is None or head_owner is None or head_owner.lower() == owner.lower())

    def row(repo, p):
        return {"repo": repo, "number": p["number"], "state": p["state"], "url": p.get("url"), "head_oid": p.get("headRefOid")}

    for repo, branches in by_repo.items():
        try:
            mine = json.loads(run(["gh", "pr", "list", "-R", repo, "--state", "all", "--author", "@me",
                                   "--limit", "200", "--json", BRANCH_PR_FIELDS]))
        except (RuntimeError, ValueError) as e:
            errors[repo] = str(e)
            continue
        for branch, owner in branches.items():
            hit = _pick([p for p in mine if matches(p, branch, owner)])
            if hit is None:
                try:
                    hit = _pick([p for p in json.loads(run(["gh", "pr", "list", "-R", repo, "--state", "all", "--head", branch,
                                                            "--json", BRANCH_PR_FIELDS])) if matches(p, branch, owner)])
                except (RuntimeError, ValueError) as e:
                    errors[(repo, branch)] = str(e)
                    continue
            if hit:
                found[(repo, branch)] = row(repo, hit)
    return found, errors


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(Path(__file__).parent / "state.json"))
    ap.add_argument("numbers", nargs="*", type=int)
    a = ap.parse_args(argv)
    state = S.load_state(a.state)
    nums = set(a.numbers) or scope_numbers(state)
    result = snapshot(state, nums)
    S.save_state(state, a.state)
    print(json.dumps(result))
    return 1 if result["errors"] and not result["updated"] else 0


if __name__ == "__main__":
    sys.exit(main())

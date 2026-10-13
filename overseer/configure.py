"""Write overseer/config.json from flags, so an installing agent never hand-edits JSON.

    python3 overseer/configure.py --repo owner/name [--gating-bot LOGIN] [--advisory-bot LOGIN|""]
        [--session-pattern REGEX] [--cwd-prefix PATH ...] [--human "Name"] [--round-cap N]
        [--notify osascript|none] [--show]

Flags merge into the existing config.json (or the defaults in config.py); anything not given
keeps its current value.  --show prints the result without writing.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import config as C

PATH = Path(__file__).with_name("config.json")


def build(args, current):
    cfg = dict(current)
    if args.repo is not None:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
            raise ValueError(f"--repo must look like owner/name, got {args.repo!r}")
        cfg["repo"] = args.repo
    if args.gating_bot is not None:
        cfg["gating_bot"] = args.gating_bot
    if args.advisory_bot is not None:
        cfg["advisory_bot"] = args.advisory_bot
    if args.session_pattern is not None:
        re.compile(args.session_pattern)          # fail now, not on the first tick
        cfg["session_pattern"] = args.session_pattern
    if args.cwd_prefix:
        cfg["cwd_prefixes"] = [str(Path(p).expanduser()) for p in args.cwd_prefix]
    if args.human is not None:
        cfg["human"] = args.human
        cfg["human_short"] = args.human.split()[0].lower() if args.human.strip() else "human"
    if args.round_cap is not None:
        cfg["round_cap"] = args.round_cap
    if args.notify is not None:
        cfg["notify"] = args.notify
    return cfg


def main(argv=None, path=PATH):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo")
    ap.add_argument("--gating-bot")
    ap.add_argument("--advisory-bot")
    ap.add_argument("--session-pattern")
    ap.add_argument("--cwd-prefix", action="append")
    ap.add_argument("--human")
    ap.add_argument("--round-cap", type=int)
    ap.add_argument("--notify")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = build(args, C.load(path))
    except (ValueError, re.error) as e:
        print(f"configure: {e}", file=sys.stderr)
        return 2
    out = json.dumps(cfg, indent=2) + "\n"
    if args.show:
        print(out, end="")
        return 0
    Path(path).write_text(out)
    print(f"wrote {path}")
    print(out, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Archive idle dsh sessions whose cwd contains a marker (default: dsh-im-bridge).

Useful after automated testing to keep the dsh sidebar tidy. Only archives
sessions that are NOT running and whose cwd path contains the marker, so
sessions you care about are never touched.

Usage:
  python scripts/archive_test_sessions.py [--marker dsh-im-bridge] [--dry-run]
"""
import argparse
import json
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dsh_im_bridge.dsh_client import DshClient  # noqa: E402

BASE = "http://127.0.0.1:10010"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--marker", default="dsh-im-bridge", help="cwd substring to match")
    p.add_argument("--dry-run", action="store_true", help="only list, don't archive")
    p.add_argument("--base", default=BASE)
    args = p.parse_args()

    client = DshClient(base_url=args.base)
    sessions = client.list_sessions()
    mine = [
        s
        for s in sessions
        if args.marker in (s.get("cwd") or "") and not s.get("running")
    ]
    if not mine:
        print(f"no idle sessions with cwd containing {args.marker!r}")
        return 0

    print(f"found {len(mine)} session(s):")
    for s in mine:
        print(f"  {s['sessionId']}  cwd={s.get('cwd')}")
    if args.dry_run:
        print("(dry-run: not archiving)")
        return 0

    for s in mine:
        client.call("workspace.archiveSession", {"sessionId": s["sessionId"]})
        print(f"  archived: {s['sessionId']}")
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

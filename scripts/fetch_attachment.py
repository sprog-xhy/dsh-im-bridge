"""Fetch a dsh session attachment (image) and save it to a file.

Usage:
  python scripts/fetch_attachment.py <sessionId> <attachmentId> -o out.png [--base http://127.0.0.1:10010]
"""
import argparse
import base64
import json
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dsh_im_bridge.dsh_client import DshClient  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("session_id")
    p.add_argument("attachment_id")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--base", default="http://127.0.0.1:10010")
    args = p.parse_args()

    client = DshClient(base_url=args.base)
    value = client.attachment(args.session_id, args.attachment_id)
    attachment = value.get("attachment") or {}
    data = base64.b64decode(value.get("data") or "")
    out = Path(args.output)
    out.write_bytes(data)
    print(
        f"saved {len(data)} bytes -> {out}\n"
        f"  mediaType={attachment.get('mediaType')} "
        f"name={attachment.get('name')!r} {attachment.get('width')}x{attachment.get('height')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""One-command full verification of the bridge.

Runs, in order:
  1. self-check  (config + dsh HTTP/WS connectivity, channels, state dir)
  2. confirmation-flow demo  (fake dsh wire: agent asks -> you answer -> continues)

Exit code 0 only if every stage passes. For a first run after setting up:

    python scripts/verify_all.py            # interactive confirmation
    python scripts/verify_all.py --auto 5   # auto-answer after 5s
"""
import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run(cmd: list[str]) -> int:
    print(f"\n$ {subprocess.list2cmdline(cmd)}\n", flush=True)
    return subprocess.call(cmd, cwd=ROOT)


def main() -> int:
    # Windows consoles default to GBK and crash on emoji; force UTF-8 (the
    # child scripts do the same, this covers verify_all's own prints).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=None)
    p.add_argument("--auto", type=float, default=0.0, help="auto-answer after N seconds in the demo")
    args = p.parse_args()

    print("=== dsh-im-bridge 全量自检 ===\n")

    check_args = [PY, "-m", "dsh_im_bridge", "--check"]
    if args.config:
        check_args += ["--config", args.config]
    rc1 = run(check_args)

    demo_args = [PY, str(ROOT / "scripts" / "demo_confirmation.py"), "--wait", "12"]
    if args.auto > 0:
        demo_args += ["--auto", str(args.auto)]
    print("\n=== 确认流程演示（agent 提问 -> 应答 -> 继续）===")
    rc2 = run(demo_args)

    print()
    if rc1 == 0 and rc2 == 0:
        print("✅ 全部通过：环境 OK，确认流程 OK。可以配置飞书/QQ 通道了。")
        return 0
    print(f"❌ 存在问题：self-check exit={rc1}, demo exit={rc2}")
    print("   先跑 `python -m dsh_im_bridge --check` 看哪里标红。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""dsh-qq 插件入口。

只做一件事：把 dsh 桥接到 QQ 官方开放平台机器人（C2C 私聊）。本插件
复用 ``dsh-im-bridge`` 核心（DshClient / BridgeHub / 各通道实现），但通过
``--only qq_official`` 只启用 QQ 官方通道，因此单独安装本插件即可工作，
无需关心整个 dsh-im-bridge 仓库的其他通道。

用法：:

    dsh-qq                     # 使用插件自带的 config.example.yaml（需先复制为 config.yaml）
    dsh-qq --config config.yaml
    dsh-qq --test-notify qq_official --notify-target <openid>
"""

from __future__ import annotations

import sys
from pathlib import Path

from dsh_im_bridge.__main__ import main as bridge_main

# plugins/dsh-qq/config.example.yaml
PLUGIN_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PLUGIN_DIR / "config.example.yaml"

CHANNEL = "qq_official"


def main(argv: list | None = None) -> int:
    """Parse plugin args, force the QQ-official single channel, run the bridge."""
    args = list(sys.argv[1:] if argv is None else argv)
    # default to the plugin's own config template when none is given
    if "--config" not in args:
        args = ["--config", str(DEFAULT_CONFIG)] + args
    # force single-channel mode (ignore any other channel in the config file)
    if "--only" not in args:
        args += ["--only", CHANNEL]
    return bridge_main(args)


if __name__ == "__main__":
    sys.exit(main())

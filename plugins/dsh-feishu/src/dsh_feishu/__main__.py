"""dsh-feishu 插件入口。

只做一件事：把 dsh 桥接到飞书应用机器人。本插件复用 ``dsh-im-bridge``
核心（DshClient / BridgeHub / 各通道实现），但通过 ``--only feishu`` 只启用
飞书通道，因此单独安装本插件即可工作，无需关心整个 dsh-im-bridge 仓库的
其他通道。

用法：:

    dsh-feishu                     # 使用插件自带的 config.example.yaml（需先复制为 config.yaml）
    dsh-feishu --config config.yaml
    dsh-feishu --test-notify feishu --notify-target <chat_id>
"""

from __future__ import annotations

import sys
from pathlib import Path

from dsh_im_bridge.__main__ import main as bridge_main

# plugins/dsh-feishu/config.example.yaml
PLUGIN_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PLUGIN_DIR / "config.example.yaml"

CHANNEL = "feishu"


def main(argv: list | None = None) -> int:
    """Parse plugin args, force the Feishu single channel, run the bridge."""
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

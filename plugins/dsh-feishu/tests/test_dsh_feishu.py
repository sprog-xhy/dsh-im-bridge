"""dsh-feishu 插件测试：验证入口强制飞书单通道、默认配置指向插件自带模板。"""

import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]   # plugins/dsh-feishu
_REPO_ROOT = _PLUGIN_ROOT.parents[1]                 # 仓库根
sys.path.insert(0, str(_PLUGIN_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "src"))

import pytest

import dsh_feishu.__main__ as feishu_main
from dsh_feishu.__main__ import CHANNEL, DEFAULT_CONFIG, PLUGIN_DIR


def test_channel_is_feishu():
    assert CHANNEL == "feishu"


def test_default_config_points_inside_plugin():
    assert DEFAULT_CONFIG == PLUGIN_DIR / "config.example.yaml"
    assert DEFAULT_CONFIG.exists()


def test_main_injects_only_channel_when_missing(monkeypatch):
    captured = {}

    def fake_bridge_main(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(feishu_main, "bridge_main", fake_bridge_main)
    feishu_main.main(["--config", "my.yaml"])  # no --only
    assert "--only" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--only") + 1] == "feishu"


def test_main_injects_default_config_when_missing(monkeypatch):
    captured = {}

    def fake_bridge_main(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(feishu_main, "bridge_main", fake_bridge_main)
    feishu_main.main([])  # no --config
    assert "--config" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--config") + 1] == str(DEFAULT_CONFIG)


def test_main_keeps_user_only(monkeypatch):
    captured = {}

    def fake_bridge_main(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(feishu_main, "bridge_main", fake_bridge_main)
    feishu_main.main(["--config", "my.yaml", "--only", "qq_official"])
    assert captured["argv"][captured["argv"].index("--only") + 1] == "qq_official"


def test_plugin_config_only_enables_feishu():
    from dsh_im_bridge.__main__ import _apply_only
    from dsh_im_bridge.config import load_config

    cfg = load_config(str(DEFAULT_CONFIG))
    # the plugin template enables the feishu channel
    assert cfg.channels["feishu"]["enabled"] is True
    # runtime --only filtering leaves exactly the target channel
    cfg = _apply_only(cfg, "feishu")
    assert set(cfg.channels) == {"feishu"}

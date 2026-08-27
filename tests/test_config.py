"""Tests for configuration loading and validation."""

from pathlib import Path

from dsh_im_bridge.config import Config, load_config


def _cfg(**overrides):
    base = {
        "dsh_base_url": "http://127.0.0.1:10010",
        "bridge_host": "127.0.0.1",
        "bridge_api_port": 8764,
        "state_file": None,
        "default_workspace_id": None,
        "default_cwd": None,
        "agent_preset": None,
        "forward_events": frozenset(),
        "max_message_chars": 2000,
        "catch_up": True,
        "catch_up_max_events": 200,
        "channels": {},
    }
    base.update(overrides)
    return Config(**base)


def test_validate_ok():
    c = _cfg(channels={"console": {"enabled": True}})
    assert c.validate() == ([], [])


def test_validate_workspace_and_cwd_conflict():
    c = _cfg(default_workspace_id="w-1", default_cwd="D:/x")
    errors, _ = c.validate()
    assert any("defaultWorkspaceId or defaultCwd" in e for e in errors)


def test_validate_unknown_channel():
    c = _cfg(channels={"bogus": {"enabled": True}})
    errors, _ = c.validate()
    assert any("unknown channel" in e for e in errors)


def test_validate_feishu_missing_credentials():
    c = _cfg(channels={"feishu": {"enabled": True}})
    _, warnings = c.validate()
    assert any("feishu" in w for w in warnings)


def test_validate_feishu_partial_creds():
    c = _cfg(channels={"feishu": {"enabled": True, "appId": "cli_x"}})
    errors, _ = c.validate()
    assert any("appSecret" in e for e in errors)


def test_validate_qq_missing_wsurl():
    c = _cfg(channels={"qq": {"enabled": True, "wsUrl": ""}})
    errors, _ = c.validate()
    assert any("wsUrl" in e for e in errors)


def test_validate_no_channels_warns():
    c = _cfg(channels={})
    _, warnings = c.validate()
    assert any("no channels" in w for w in warnings)


def test_load_config_defaults(tmp_path):
    c = load_config()
    assert c.dsh_base_url == "http://127.0.0.1:10010"
    assert "console" in c.channels
    assert "webhook" in c.channels


def test_load_config_file(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dsh:\n  baseUrl: http://127.0.0.1:9999\nchannels:\n  console:\n    enabled: false\n  feishu:\n    enabled: true\n    webhookUrl: https://open.feishu.cn/x\n",
        encoding="utf-8",
    )
    c = load_config(str(cfg))
    assert c.dsh_base_url == "http://127.0.0.1:9999"
    assert "console" not in c.channels
    assert c.channels["feishu"]["webhookUrl"].endswith("/x")


def test_load_config_unknown_channels_kept_for_validation():
    c = _cfg(channels={"nope": {"enabled": True}})
    errors, _ = c.validate()
    assert errors

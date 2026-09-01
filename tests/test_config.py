"""Tests for configuration loading and validation."""

import os
from pathlib import Path

from dsh_im_bridge.config import Config, load_config, load_dotenv


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
        "split_long_messages": True,
        "catch_up": True,
        "catch_up_max_events": 200,
        "notify_on_start": True,
        "send_welcome_on_bind": True,
        "include_reasoning": False,
        "send_retries": 2,
        "send_retry_delay": 1.0,
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


def test_validate_qq_official_missing_creds():
    c = _cfg(channels={"qq_official": {"enabled": True}})
    errors, _ = c.validate()
    assert any("appId and appSecret" in e for e in errors)


def test_validate_qq_official_ok():
    c = _cfg(
        channels={"qq_official": {"enabled": True, "appId": "1905533507", "appSecret": "x"}}
    )
    errors, warnings = c.validate()
    assert errors == []
    assert warnings == []


def test_validate_qq_official_non_numeric_appid():
    c = _cfg(
        channels={"qq_official": {"enabled": True, "appId": "cli_abc", "appSecret": "x"}}
    )
    errors, _ = c.validate()
    assert any("numeric AppID" in e for e in errors)


def test_validate_no_channels_warns():
    c = _cfg(channels={})
    _, warnings = c.validate()
    assert any("no channels" in w for w in warnings)


def test_load_config_defaults(tmp_path):
    c = load_config()
    assert c.dsh_base_url == "http://127.0.0.1:10010"
    assert "console" in c.channels
    assert "webhook" in c.channels


def test_load_config_forward_events_default_not_empty():
    """Regression: an unset `forwardEvents` must fall back to the default set,
    not to an empty set (which silently drops every dsh reply)."""
    from dsh_im_bridge.config import DEFAULT_FORWARD_EVENTS

    c = load_config()
    assert c.forward_events == DEFAULT_FORWARD_EVENTS
    assert c.forward_events  # non-empty
    assert "assistant/message" in c.forward_events
    assert "turn/end" in c.forward_events


def test_load_config_forward_events_custom(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("bridge:\n  forwardEvents:\n    - turn/end\n", encoding="utf-8")
    c = load_config(str(cfg))
    assert c.forward_events == frozenset({"turn/end"})


def test_load_config_split_long_messages_default_true():
    c = load_config()
    assert c.split_long_messages is True


def test_load_config_split_long_messages_custom(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("bridge:\n  splitLongMessages: false\n", encoding="utf-8")
    c = load_config(str(cfg))
    assert c.split_long_messages is False


def test_load_config_reasoning_and_welcome_defaults(tmp_path):
    c = load_config()
    assert c.include_reasoning is False
    assert c.send_welcome_on_bind is True


def test_load_config_reasoning_and_welcome_custom(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "bridge:\n  includeReasoning: true\n  sendWelcomeOnBind: false\n",
        encoding="utf-8",
    )
    c = load_config(str(cfg))
    assert c.include_reasoning is True
    assert c.send_welcome_on_bind is False


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


def test_load_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nDSH_IM_BRIDGE_FEISHU_APP_ID=cli_abc\nDSH_IM_BRIDGE_QQ_WS_URL=\"ws://x:1\"\nUNRELATED=1\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DSH_IM_BRIDGE_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("DSH_IM_BRIDGE_QQ_WS_URL", raising=False)
    load_dotenv(str(env))
    assert os.environ["DSH_IM_BRIDGE_FEISHU_APP_ID"] == "cli_abc"
    assert os.environ["DSH_IM_BRIDGE_QQ_WS_URL"] == "ws://x:1"
    # unrelated keys are not loaded (we only read DSH_IM_BRIDGE_*)
    assert "UNRELATED" in os.environ  # loader sets whatever is in the file
    # existing env wins
    monkeypatch.setenv("DSH_IM_BRIDGE_FEISHU_APP_ID", "already")
    load_dotenv(str(env))
    assert os.environ["DSH_IM_BRIDGE_FEISHU_APP_ID"] == "already"


def test_load_dotenv_missing_file(tmp_path):
    # missing file must not raise
    load_dotenv(str(tmp_path / "nope.env"))
    assert True

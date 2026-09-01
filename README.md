# dsh-im-bridge

[English](README.en.md) | 中文

**dsh-im-bridge** 让 [DeepSeek Harness (dsh)](https://github.com/sprog-xhy/dsh-im-bridge) 和 QQ、飞书等 IM 工具互通：在 QQ / 飞书里发消息，就能指挥本机上的 dsh agent 干活；dsh 完成任务或需要你确认时，结果会自动推回 IM。

本项目包含**一款 Python 桥接程序**和**两款 dsh 官方规范插件**（`dsh-qq`、`dsh-feishu`），支持 Windows 与 Ubuntu。

---

## 快速选择

| 你的需求 | 推荐方案 |
|---|---|
| 只用 QQ | **dsh-qq 插件**（QQ 官方开放平台机器人，C2C 私聊） |
| 只用飞书 | **dsh-feishu 插件**（飞书应用机器人，双向收发） |
| 一个进程同时接多个 IM（飞书 / QQ / Webhook / 终端） | **Python 桥接程序** |

两款插件都是 **DeepSeek Harness 官方规范插件**（Node.js Cordis 插件），装进 dsh 的 profile 即可生效，**不是 pip 包**。

- **dsh-qq** → [中文文档](plugins/dsh-qq/README.md) ｜ [English](plugins/dsh-qq/README.en.md)
- **dsh-feishu** → [中文文档](plugins/dsh-feishu/README.md) ｜ [English](plugins/dsh-feishu/README.en.md)

---

## dsh-qq 插件

在 QQ 里私聊机器人，即可指挥本机的 dsh：

```
你：帮我写一个 hello.py 并运行
dsh：✅ 已创建并运行，输出：...
```

特性：

- QQ 官方开放平台机器人 API，官方 WebSocket 长连接，无需第三方协议端
- C2C 私聊双向：QQ 消息 → dsh 执行 → 结果 / 完成推回 QQ
- 自动绑定：每个 QQ 用户对应一个稳定的 dsh 会话
- 超长回复自动拆分（`[1/N]`），不丢内容

安装与 QQ 开放平台配置教程 → [dsh-qq 中文文档](plugins/dsh-qq/README.md) ｜ [English](plugins/dsh-qq/README.en.md)

---

## dsh-feishu 插件

在飞书里私聊机器人 / 群里 @ 机器人，即可指挥本机的 dsh：

```
你：帮我写一个 hello.py 并运行
dsh：✅ 已创建并运行，输出：...
```

特性：

- 飞书开放平台：应用机器人（收发双向）或自定义机器人 webhook（仅发送通知）
- 飞书消息 → dsh 执行 → 结果 / 完成推回飞书
- 自动绑定：每个飞书会话（chat_id）对应一个稳定的 dsh 会话
- 超长回复自动拆分（`[1/N]`），不丢内容

安装与飞书开放平台配置教程 → [dsh-feishu 中文文档](plugins/dsh-feishu/README.md) ｜ [English](plugins/dsh-feishu/README.en.md)

---

## Python 桥接程序

一个独立的 Python 进程，通过 dsh web 的回环 `/api` 协议，把多个 IM 通道（飞书 / QQ / Webhook / 终端）接到 dsh，无需改动 dsh 本体。

```bash
# 安装
python -m venv .venv
.\.venv\Scripts\activate        # Windows
source .venv/bin/activate       # Ubuntu
pip install -e .

# 配置（按需开启 feishu / qq / qq_official 并填入凭据）
cp config.example.yaml config.yaml

# 运行
dsh-im-bridge --config config.yaml
```

### 会话绑定

每个 IM 会话 ↔ 一个 dsh 会话，映射持久化在 `bridge-state.json`：

- 首次发消息自动新建并绑定；
- `/attach <会话ID>` 绑定已有会话；`/new` 新建并绑定；
- 绑定后，dsh 的回复 / 任务完成 / 需要确认都会推回 IM；
- **直接回复即可应答**确认请求（如 `1`、`允许`、`拒绝`）；
- `/cancel` 中断当前任务；`/history [N]` 查看最近记录。

### 常驻运行

- **Windows**：运行 `scripts\install-startup.ps1`，登录时自动拉起 dsh web 和桥接（无需管理员）。
- **Ubuntu**：`sudo ./scripts/install-systemd.sh <绝对路径>` 装成 systemd 服务，开机自启、崩溃自动重启。

---

## 更多文档

- [English README](README.en.md)
- [联调指南 INTEGRATION.md](INTEGRATION.md)

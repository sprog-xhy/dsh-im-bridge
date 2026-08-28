# dsh-im-bridge

Bridge **DeepSeek Harness (dsh)** agents to IM / collaboration tools (QQ, 飞书/Feishu, …), so that:

* when an agent finishes a task, or needs your confirmation (a **question** / **approval**), the agent notifies you in your IM tool;
* you can proactively send a message from your IM tool and the dsh agent on your machine will pick it up and work on it.

Cross-platform: Windows + Ubuntu (Python ≥ 3.10). 已在 Windows 和 Ubuntu(WSL2, Python 3.12)双环境跑通全部单测与确认流程 demo。

> ⚠️ 部署提示: 桥接要和 dsh 在**同一系统/网络**里。若在 WSL(Ubuntu)里跑桥接、dsh 在 Windows, WSL 的 `127.0.0.1` 不是 Windows 的, 连不上——需开 WSL mirrored 网络或让 dsh 绑定非回环地址(见 REPORT.md 第 10 轮)。

> ⚠️ 状态: 第一个可运行的版本已经完成并通过端到端实测（见 [REPORT.md](REPORT.md)）。当前联调目标：**飞书 + QQ**（WOA/WPS 协作因需管理员审核已弃用，代码保留备用）。
>
> 📘 **飞书 / QQ 的逐步联调步骤见 [INTEGRATION.md](INTEGRATION.md)**（建飞书应用、跑 NapCat 等，跟着做就能通）。
> 📋 **把凭据和拍板项一次给我：填 [CREDENTIALS.md](CREDENTIALS.md) 里的表发回即可。**
> 需要真实账号才能联调飞书/QQ 的完整收发。

---

## 架构

```
   QQ / Feishu / WOA / 任意工具
            │  (channel adapters)
            ▼
   ┌──────────────────────┐        loopback /api      ┌────────────────────────────┐
   │  BridgeHub (hub.py)  │ ◄─────  unary RPC + WS ──► │   dsh web (在你的机器上)     │
   │  - 会话绑定/路由       │        events.mux/respond  │   session.prompt / question │
   │  - 问题/审批转发与应答  │                             │   / approval / turn/end    │
   └──────────┬───────────┘                             └────────────────────────────┘
              │
   ┌──────────▼───────────┐
   │ BridgeServer (server.py)  本地管理 HTTP API (状态/注入/应答)   │
   └──────────────────────┘
```

关键点: **桥接进程与 dsh 在同一台机器上**, 通过 dsh web 的回环 `/api` 协议通信(不需要改 dsh 本体, 不需要浏览器)。

### 已实现

| 模块 | 说明 |
|---|---|
| `dsh_client.py` | dsh `/api` 客户端: unary RPC (`session.prompt` / `create` / `history` / `attachment` / …), `events.mux` WebSocket 事件流(自动重连+退避), `/api/respond` 应答问题/审批 |
| `hub.py` | 消息路由: IM→会话 (`session.prompt`), 会话事件→IM 通知, 问题/审批转发与 `/answer` `/allow` `/reject` 指令, 发送重试, 重启补发, 启动通知 |
| `channels/console.py` | 本地终端通道(也是 demo/测试通道) |
| `channels/feishu.py` | 飞书: 自定义机器人 webhook(仅发送) + 应用机器人(收发, 事件长连接, AES 解密) |
| `channels/qq.py` | QQ: OneBot11 反向 WebSocket(NapCat / Lagrange / LLOneBot / go-cqhttp) |
| `channels/webhook.py` | 通用本地 HTTP 入站端点, 任何工具都能 POST 消息进来 |
| `channels/woa.py` | WPS 协作 (WOA) — ⚠️ 已弃用(需管理员审核)，代码保留备用 |
| `server.py` | 本地管理 HTTP API: `/status`(含待确认问题内容) `/prompt` `/message` `/answer` `/approval` `/bind` `/attachment` |

### 会话绑定

每个 IM 会话(conversation) ↔ 一个 dsh 会话(session) 的映射持久化在 `bridge-state.json`:

* 首次发消息自动建一个 dsh 会话并绑定;
* `/attach <会话ID>` 绑定到已有会话; `/new` 新建并绑定;
* 会话绑定后, 该 dsh 会话的 `assistant/message`、`tool/result`(错误/有输出)、`turn/end`(任务完成) 会推送到 IM;
* `question/requested` / `approval/requested` 会推送到 IM, 用 `/answer` `/allow` `/reject` 或 HTTP API 应答;
* `/cancel` 中断绑定会话(例如它卡在等待确认上), `/history [N]` 拉取最近记录;
* 桥接重启后, dsh 会重新推送未答复的问题(只要会话已绑定), 桥接会自动重新捕获并通知你; 另有兜底检测提醒"离线期间未答复的确认请求", 可用 `/cancel` 或 `/history` 处理。

---

## 快速开始

```bash
# 1) 准备 (Windows / Ubuntu 均可)
python -m venv .venv
.\.venv\Scripts\activate        # Windows
source .venv/bin/activate       # Ubuntu

pip install -e .[dev]

# 2) 配置
cp config.example.yaml config.yaml   # 按需开启 feishu/qq 并填入凭据
cp .env.example .env                 # 或把凭据放进 .env(启动时自动读取)

# 3) 全量自检（推荐先跑一次；加 --pytest 连单测一起跑 = 发布体检）
python scripts/verify_all.py --auto 4
python scripts/verify_all.py --auto 4 --pytest   # 单测 + 环境 + 确认流程

# 4) 运行（安装后可直接用 dsh-im-bridge 命令）
dsh-im-bridge --config config.yaml            # 等价: python -m dsh_im_bridge
dsh-im-bridge --config config.yaml --log-file bridge.log   # 记录日志到文件

# 4b) 联调真实通道后，先用 --test-notify 发一条测试消息确认通道已就绪
dsh-im-bridge --config config.yaml --test-notify feishu             # 发到飞书群
dsh-im-bridge --config config.yaml --test-notify qq --notify-target group:12345

# 管理 API: http://127.0.0.1:8764/status
```

### 无凭据先跑通 (console + webhook 通道)

```bash
python -m dsh_im_bridge          # 默认只启用 console + webhook 通道
```

然后另开一个终端, 向 webhook 通道注入消息(首次会自动建会话并绑定):

```bash
curl -X POST http://127.0.0.1:8765/message \
  -H 'content-type: application/json' \
  -d '{"text": "帮我写一个 hello.py 并运行"}'
```

> ⚠️ 注入中文请用 curl/Python(UTF-8)。**Windows PowerShell 的 `Invoke-RestMethod` 会按 ASCII 编码 JSON 体, 把中文变成 `????`**, 导致 agent 看到乱码。

通知会打印在桥接进程的 stdout 上。

### 对真实 dsh 的端到端自测

```bash
# 会在 dsh 里新建会话, 发一条提示, 观察 turn/end 通知转发
python scripts/e2e_smoke.py --cwd "D:/mycode/python/local-dev"
```

### 确认流程演示 (不需要真实账号)

```bash
python scripts/demo_confirmation.py            # 手动回答
python scripts/demo_confirmation.py --auto 4   # 4 秒后自动回答
python scripts/demo_woa.py                     # WOA(WPS 协作)通道端到端演示(已弃用，备用)
python scripts/demo_im.py                      # 飞书 + QQ 端到端演示(假服务器，无凭据预览)
```

用模拟的 wire 跑通"agent 提问 → 你回答 `/answer 1:方案A` → agent 继续"的完整闭环(真实 Hub/通道代码)。`demo_im.py` 演示飞书(长连接)/QQ(OneBot WS)消息 → 桥接 → dsh → 回复发回平台的全链路。

### 常驻运行 (Windows / Ubuntu)

```bash
# Ubuntu: 一键装成 systemd 服务(登录/开机自启、崩溃自动重启)
sudo ./scripts/install-systemd.sh /abs/path/to/dsh-im-bridge
sudo journalctl -u dsh-im-bridge -f

# Windows: 注册为登录自启计划任务
powershell -ExecutionPolicy Bypass -File scripts\install-windows-task.ps1
```

手动前台运行: `scripts\run_bridge.ps1`(Windows) 或 `./scripts/run_bridge.sh`(Ubuntu)。

---

## 桥接管理 API (`server.py`, 默认 127.0.0.1:8764)

| 路由 | 说明 |
|---|---|
| `GET /health` | 存活检查 |
| `GET /status` | dsh + 通道 + 绑定 + 待确认列表 |
| `GET /attachment?sessionId=..&attachmentId=..` | 取回会话图片(base64) |
| `POST /prompt` | `{"sessionId": "...", "text": "..."}` 直接发提示 |
| `POST /message` | `{"channel": "webhook", "conversation_id": "x", "text": "..."}` 经某通道注入 |
| `POST /answer` | `{"channel","conversation_id","text":"1:选项A,2:自定义"}` 回答待确认问题 |
| `POST /approval` | `{"channel","conversation_id","outcome":"allow|reject"}` 审批 |
| `POST /bind` | `{"channel","conversation_id","session_id"}` 绑定会话 |

---

## 开发 & 测试

```bash
python -m pytest -q        # 68 个单测(不依赖真实网络/账号)
```

### 图片/附件

agent 消息里带图时, IM 文本会显示 `[图片: 名字]` + `📎 附件 [attachmentId=...]` 提示。取回真实图片:

```bash
python scripts/fetch_attachment.py <sessionId> <attachmentId> -o out.png
# 或经桥接 API: GET http://127.0.0.1:8764/attachment?sessionId=..&attachmentId=..
```

> 飞书/QQ 通道的图片上传(im/v1/images / OneBot image)等拿到真账号后接入。

测试覆盖: 线协议解析、DshClient(unary/respond/mux 重连)、Hub 路由(自动绑定、事件转发、问题/审批应答、指令、状态持久化)、QQ OneBot 收发、webhook HTTP。

## 目录

```
src/dsh_im_bridge/
  dsh_client.py    dsh /api 客户端
  parser.py        线协议帧解析
  events.py        事件模型
  formatter.py     事件→IM 文本渲染
  hub.py           路由核心
  server.py        管理 HTTP API
  httpx.py         极简线程 HTTP 服务器
  channels/        console / feishu / qq / webhook / woa
scripts/           probe_dsh_api / probe_dsh_mux / probe_session / e2e_smoke
tests/             单测
```

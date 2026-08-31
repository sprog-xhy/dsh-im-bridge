# 联调指南 (Feishu / QQ / WOA)

本文假设你已跑通 `scripts/verify_all.py`(环境 OK)。下面是每个 IM 通道从零到联调的步骤。桥接本体是通用的, 通道只是"适配器", 按需开一个或几个。

---

## 1. 飞书 (Feishu)

飞书有两种接入方式, **二选一**(或都配, webhook 优先用于发送):

### 方式 A: 自定义机器人 webhook(仅发送通知, 最快)

1. 在飞书里建一个群(或已有群) → 群设置 → 群机器人 → 添加机器人 → **自定义机器人**。
2. 复制 webhook 地址, 形如 `https://open.feishu.cn/open-apis/bot/v2/hook/<一串token>`。
3. 配置:
   ```yaml
   channels:
     feishu:
       enabled: true
       webhookUrl: "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx"
   ```
   > 只能发(任务完成通知、要确认的问题都会推到这个群), **不能收消息**。要先收消息(在群里直接喊 dsh)请用方式 B。

### 方式 B: 应用机器人(收发双向, 推荐)

1. 打开 [飞书开放平台](https://open.feishu.cn/app) → 创建企业自建应用。
2. 权限管理里开通并发布:
   - `im:message`(读取接收消息)
   - `im:message:send_as_bot`(以机器人身份发消息)
3. **事件订阅**: 选择"长连接(WebSocket)"模式; 添加事件 `im.message.receive_v1`(接收消息)。
   - ⚠️ **先别点保存**：长连接模式保存时会校验"是否有在线长连接客户端"。请先按第 5 步配好桥接并**启动桥接**（它会主动连飞书长连接），再回到后台点保存，否则报"无在线长连接"。
   - 若开启事件加密, 复制 `Encrypt Key`。
4. 拿到 `App ID` / `App Secret`。
5. 配置:
   ```yaml
   channels:
     feishu:
       enabled: true
       appId: "cli_xxxx"
       appSecret: "xxxx"
       # encryptKey: "xxxx"     # 仅当你在事件订阅里开了加密
       receiveChatTypes: ["p2p", "group"]
   ```
6. 启动桥接 `dsh-im-bridge --config config.yaml --log-file bridge.log`，日志出现 `feishu ws endpoint: ...` 说明长连接已连上；此时回飞书后台**保存**事件订阅配置。
7. 把机器人拉进你的群(或直接私聊机器人), 群里 @机器人 / 私聊发消息即可驱动 dsh。
   - 首次发消息会自动建一个 dsh 会话并绑定; 之后该会话的完成/确认都会推回来。

**自测**: 启动桥接(`dsh-im-bridge --config config.yaml --log-file bridge.log`), 日志出现 `feishu long-connection endpoint: ...` 说明连上了。然后在群里发一句话看是否触发 dsh。

---

## 2. QQ 官方开放平台机器人 API（推荐，无需协议端）

用 [q.qq.com](https://q.qq.com) 的**官方机器人应用**接入，桥接自己连官方 WebSocket 长连接收发 C2C 私聊消息，**不需要**跑 NapCat 等协议端、也不需要 QQ 小号。私聊直接驱动 dsh。

1. 打开 [QQ 开放平台](https://q.qq.com) → 创建机器人应用（或进入已有应用）。
2. 在「开发设置 / 凭证」里拿到 **AppID**（纯数字）和 **AppSecret**。
3. 配置（`config.yaml` 或环境变量 `DSH_IM_BRIDGE_QQ_OFFICIAL_*`）:
   ```yaml
   channels:
     qq_official:
       enabled: true
       appId: "1905533507"        # 你的 AppID（纯数字）
       appSecret: "xxxx"          # 你的 AppSecret
       sandbox: false             # true=沙箱(sandbox.api.bot.qq.com), false=正式
       # allowUsers: []           # 可选：只允许这些用户 openid 私聊（留空=全部）
   ```
4. 启动桥接 `dsh-im-bridge --config config.yaml --log-file bridge.log`，日志出现 `qq_official WS connected` 说明官方长连接已连上。
5. 私聊你的机器人发消息即可驱动 dsh（首次自动建会话并绑定，回复/完成/确认都会推回私聊）。

> 注意：机器人必须先被你的 QQ 号**添加为好友**（或在应用里配好 C2C 权限）才能收到私聊消息。正式环境（`sandbox: false`）下机器人的 C2C 私聊能力可能需要应用过审后才生效。

**自测**: `dsh-im-bridge --config config.yaml --test-notify qq_official --notify-target <你的openid>`（openid 可在收到第一条消息后从日志/状态里看到）。

---

## 3. QQ (OneBot11，可选备选方案)

QQ 侧需要跑一个 **OneBot11 协议端**(把 QQ 变成可编程机器人), 推荐 NapCat(或 Lagrange.OneBot / LLOneBot / go-cqhttp)。

1. 下载并运行 **NapCat**(QQNT 框架): 用你的一个 QQ 小号登录(协议端有一定封号风险, 请自行评估, 建议小号)。
2. NapCat 网络配置 → 添加 **WebSocket 服务器** 模式, 记下:
   - `ws://127.0.0.1:3001`(端口默认 3001, 可改)
   - 若设了 `access_token`, 一并记下
   - 机器人自己的 QQ 号(selfId)
3. 配置:
   ```yaml
   channels:
     qq:
       enabled: true
       wsUrl: "ws://127.0.0.1:3001"
       # accessToken: "xxxx"
       selfId: "10001"          # 机器人 QQ 号(避免收到自己的回显)
       # allowGroups: [123456]  # 只允许这些群(留空=全部)
       # allowUsers: []         # 只允许这些私聊(留空=全部)
   ```
4. 给机器人 QQ 发私聊或拉进群发消息即可驱动 dsh。

**自测**: 启动桥接, 日志出现 `qq OneBot connected to ws://...` 说明连上协议端。发一句话看是否触发。

---

## 4. WOA（WPS 协作）— ⚠️ 已弃用

WPS 协作 (WPS365) 机器人需要企业**管理员审核**才能用，已放弃联调。通道代码保留在 `channels/woa.py`（含 `wps_relay.py` 公网中继）供将来备用，但**默认禁用**，不在当前联调范围。当前联调目标：**飞书 + QQ 官方开放平台**（见上两节）。

---

## 5. 配置与启动速查

```bash
# 自检
python scripts/verify_all.py --auto 4

# 前台运行(带日志)
dsh-im-bridge --config config.yaml --log-file bridge.log

# Ubuntu 常驻
sudo ./scripts/install-systemd.sh /abs/path/to/dsh-im-bridge
sudo journalctl -u dsh-im-bridge -f

# Windows 常驻(登录自启, 日志 -> bridge.log)
powershell -ExecutionPolicy Bypass -File scripts\install-windows-task.ps1
```

## 6. 常见排查

| 现象 | 排查 |
|---|---|
| `--check` 里 dsh 标红 | 桥接要和 dsh 同系统/同网络(见 REPORT 第 10 轮 WSL 说明) |
| 发送报 `230006 Bot ability is not activated` | 应用**未发布**：应用能力里加"机器人"、权限 `im:message`/`im:message:send_as_bot`/`im:message.p2p_msg`/`im:message.group_msg` 生效后，去"版本管理与发布"→ 创建版本 → 发布 |
| 飞书能发不能收 | 用了自定义机器人(仅发送), 改用应用机器人 + 长连接 |
| 飞书日志没有 `endpoint` | 应用没开"长连接"事件订阅 / 没发布权限 |
| qq_official 日志没 `WS connected` | AppID/AppSecret 错、或未配机器人能力；看错误里的 HTTP 响应 |
| qq_official 收不到私聊 | 机器人还没被你的 QQ 加为好友 / C2C 权限未生效 / 开了 `allowUsers` 但你的 openid 不在列表 |
| QQ (OneBot) 连不上 | OneBot 协议端没跑 / wsUrl 端口不对 / 要 access_token |
| 收不到任何通知 | 先 `--check`; 看 `--log-file`; 确认该会话已绑定(`/attach` 或首条消息自动绑定) |

> 命令速记: 在聊天里 `/help` 看指令(`/answer` `/allow` `/cancel` `/history` `/sessions` `/attach` `/new` 等)。

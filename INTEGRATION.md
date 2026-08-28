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
6. 把机器人拉进你的群(或直接私聊机器人), 群里 @机器人 / 私聊发消息即可驱动 dsh。
   - 首次发消息会自动建一个 dsh 会话并绑定; 之后该会话的完成/确认都会推回来。

**自测**: 启动桥接(`dsh-im-bridge --config config.yaml --log-file bridge.log`), 日志出现 `feishu long-connection endpoint: ...` 说明连上了。然后在群里发一句话看是否触发 dsh。

---

## 2. QQ (OneBot11)

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

## 3. WOA（WPS 协作 / WPS365 企业机器人）

协议已实现（发送 + HTTP 回调接收）。接入步骤：

1. **WPS 开放平台建应用**：登录 [WPS 开放平台](https://open.wps.cn/)（海外/日本版 [jp-open.wps.com](https://jp-open.wps.com/)）→ 创建**内部企业应用**。
2. 应用里开启**机器人/消息能力**，消息模式选 **HTTP 回调模式**；开通发送/接收消息所需权限。
3. 拿到三项凭据：
   - **App ID**（`appId`）
   - **Secret Key**（`secretKey`，用于 API 签名 + 回调解密）
   - **Encrypt Key**（`encryptKey`，回调加密密钥，可选）
4. **回调地址**：填桥接的 webhook，例如 `http://<公网可达地址>:8766/webhook`。
   > ⚠️ 部署要点：WPS 平台需要**能访问到**这个地址。三条路（任选）：
   > 1. 桥接机器有公网 IP / 已映射端口 → 直接填。
   > 2. 内网穿透 / 反向代理（frp、cloudflare tunnel、ngrok）把 `8766` 转发到公网。
   > 3. **公网中继**（推荐给"桥接必须贴内网 dsh"的场景）：在公网小服务器上跑 `scripts/wps_relay.py`，它接收 WPS 回调、验签解密后转发给内网桥接核心的 `/message` API；回复仍由桥接核心直接出站 HTTPS 发回 WPS（NAT 也能发）：
   >    ```bash
   >    # 公网服务器上：
   >    python scripts/wps_relay.py --app-id <AppID> --secret-key <Secret> \
   >        --host 0.0.0.0 --port 8766 --forward http://<内网桥接机>:8764/message
   >    # 回调地址填 http://<公网服务器>:8766/webhook
   >    ```
5. 配置：
   ```yaml
   channels:
     woa:
       enabled: true
       appId: "wps-app-id"
       secretKey: "wps-secret-key"
       # encryptKey: "xxx"
       apiUrl: "https://openapi.wps.cn"     # 日本/海外平台地址按实际填写
       webhookHost: "0.0.0.0"
       webhookPort: 8766
       webhookPath: "/webhook"
   ```
6. 验证：`dsh-im-bridge --config config.yaml --test-notify woa`（会向目标会话发一条测试消息）。然后私聊机器人或群里 @机器人 发消息，即可驱动 dsh（群聊需要 @机器人 才响应）。

**自测**：日志出现 `woa webhook listening on ...` 且 `--test-notify woa` 成功，说明发送/接收都通了。

---

## 4. 配置与启动速查

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

## 5. 常见排查

| 现象 | 排查 |
|---|---|
| `--check` 里 dsh 标红 | 桥接要和 dsh 同系统/同网络(见 REPORT 第 10 轮 WSL 说明) |
| 飞书能发不能收 | 用了自定义机器人(仅发送), 改用应用机器人 + 长连接 |
| 飞书日志没有 `endpoint` | 应用没开"长连接"事件订阅 / 没发布权限 |
| QQ 连不上 | OneBot 协议端没跑 / wsUrl 端口不对 / 要 access_token |
| 收不到任何通知 | 先 `--check`; 看 `--log-file`; 确认该会话已绑定(`/attach` 或首条消息自动绑定) |

> 命令速记: 在聊天里 `/help` 看指令(`/answer` `/allow` `/cancel` `/history` `/sessions` `/attach` `/new` 等)。

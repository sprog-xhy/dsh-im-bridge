# 开发报告 (第 1 轮) — dsh-im-bridge

> 写给明天(睡醒后)的我们: 本轮做了什么、实测证明了什么、哪些还没验证、有哪些需要你拍板的问题。

## TL;DR

已经写好了一个可运行的 **dsh ↔ IM 桥接程序**(纯 Python, Windows/Ubuntu 通用),放在 `D:\mycode\python\local-dev\dsh-im-bridge`。
并且已经**连到你本机正在跑的 dsh web 做了端到端实测**:

```
[01:13:28] 助手
OK
[01:13:28] 🟰 步骤结束
[01:13:28] ✅ 回合结束
```

这条输出就是: 桥接进程 → `session.prompt` 给 dsh 发了一条“请只回复 OK 两个字” → dsh 的 agent 真的执行并回复了 → 桥接进程通过 `events.mux` 事件流收到 `assistant/message` 和 `turn/end`(任务完成)→ 格式化成通知推给了通道。**整条链路是真的通的, 不是模拟。**

代码仓库: `dsh-im-bridge/`, 34 个单测全过。README.md 有完整用法。

---

## 1. 探索结论: dsh 提供了我们需要的“原生 API”

我逆向研究了 dsh web 前端的线协议(`dsh-client-connection` 等 npm 包,并对着你本机 127.0.0.1:10010 实测),结论是**不需要改 dsh 本体, 不需要浏览器, 桥接进程直接和 dsh web 说话**:

| 能力 | 端点 | 说明 |
|---|---|---|
| 发提示词 | `POST /api/session.prompt` | `{"sessionId","mode":"queue","content":[{"type":"text","text":"..."}]}` |
| 建会话 | `POST /api/session.create` | 可指定 workspace/cwd/agentPreset |
| 事件流 | `ws://…/api/events.mux` | 全会话事件: `assistant/message`、`tool/result`、`turn/end`(任务完成)、`question/requested`(要你确认)、`approval/requested`(要你审批) |
| 回答确认/审批 | `POST /api/respond` | 对问题/审批回 `client-response` |

统一信封: 请求 `{"type":"client-request","rpcId":uuid,"method":m,"payload":p}`, 响应 `{"type":"server-response",…,"result":{"ok":true,"value":…}}`。桥接进程跑在**同一台机器**(回环地址)即可通过, 我在你机器上已验证 `host.describe` / `session.list` / `workspace.list` / `events.mux` 全部 200。

> 也就是说: 你在 QQ/飞书里发的话 → 桥接 → `session.prompt` → 你本机的 dsh agent 干活; agent 干完/要确认 → 桥接 → 推回 IM。这正是你要的“双向交互”。

## 2. 已实现(全部有测试)

- **dsh 客户端** `dsh_client.py`: unary RPC + mux WebSocket(断线自动重连+指数退避) + respond 应答。
- **路由核心** `hub.py`: 会话绑定(IM会话↔dsh会话, 持久化)、自动建会话、事件转发策略、问题/审批的推送与 `/answer` `/allow` `/reject` 指令、`/status` `/sessions` `/attach` `/new` 指令。
- **通道** `channels/`:
  - `console` 终端通道(本地 demo/测试);
  - `feishu` 飞书(自定义机器人 webhook=仅发送, 最省事; 应用机器人=收发, 事件长连接 + AES 解密);
  - `qq` QQ(OneBot11 反向 WebSocket, 兼容 NapCat/Lagrange/LLOneBot/go-cqhttp);
  - `webhook` 通用本地 HTTP 入站(任何工具 POST 就能驱动 agent);
  - `woa` 占位(见问题 1)。
- **管理 API** `server.py`: `GET /status`、`POST /prompt|/message|/answer|/approval|/bind`, 任何脚本/工具都能调用。
- 34 个单测: 线协议解析、客户端(含重连)、路由、通道、持久化。

## 3. 已实测(证据)

- ✅ 连本机 dsh web: `host.describe`/`session.list`/`workspace.list`/`events.mux` 均成功。
- ✅ 端到端: 建会话→`session.prompt`→agent 回复→`turn/end` 通知推送到通道(见上)。
- ✅ 单测 34/34 通过(`python -m pytest -q`)。
- ⚠️ 过程中在 dsh 里留了几个测试会话(`session-9b10cf8e…` 等), 无害, 可忽略或手动归档。

## 4. 还没验证/已知限制(明天一起处理)

1. **飞书完整收发未联调**: 需要真实的应用机器人凭据(app_id/app_secret)、开通 `im:message` 等权限、配好事件订阅(长连接模式)。代码按公开协议写的, 但没真连过。
2. **QQ 收发未联调**: 需要一个 OneBot 客户端(NapCat/Lagrange 等)。你的 QQ 是打算用机器人协议端(有封号风险, 需自行评估)还是别的方案?
3. **问题/审批流只做过单测, 没在真实 agent 上触发过**: 要让 agent 真发 `ask_user_question`/审批比较难自然触发, 明天可以专门构造一次来验证。
4. **图片/附件没有转发**(仅文本)。会话里 agent 贴图时 IM 只显示 `[图片]`。
5. **消息长文截断**: 默认 2000 字符, 超长截断。
6. **桥接进程重启后不补发错过的实时事件**(mux 是实时流; 重启期间的任务完成通知会漏)。可加: 重启时读一遍最近会话 `session.history` 补通知。
7. **PowerShell 下中文显示乱码**(GBK 显示问题, 文件本身是 UTF-8, 不影响功能; Ubuntu 无此问题)。

## 5. 需要你拍板的问题

1. **WOA 到底是什么?** 我查不到叫“WOA 协作”的标准协议。猜测: WPS 协作? 企业微信? 公司内部平台? —— 目前 WOA 通道 = 通用 HTTP 入站端点(任何能 POST 的工具都能接), 等你说清楚我再写专门适配。
2. **飞书**: 用自定义机器人 webhook(几分钟就能收到通知, 但不能收消息)还是应用机器人(能双向)? 后者需要你在飞书开放平台建应用、给我 app_id/app_secret 和事件订阅配置。
3. **QQ**: 用哪个 OneBot 实现? 你的 QQ 账号方便挂协议端吗?
4. **默认工作区**: 桥接自动建会话时, 放哪个目录/workspace? 现在默认 dsh 的 cwd(建议给一个固定目录, 比如 `D:\mycode\python\local-dev` 或单独一个 `dsh-im-bridge\workdir`)。
5. **通知策略**: 你想收到哪些事件? 我默认转发: 最终回复、工具错误/有输出、任务完成(turn/end)、需要确认/审批。要不要: 只发“完成+确认”, 不刷中间过程?
6. **桥接进程怎么常驻?** Windows 开机自启/服务, 还是 Ubuntu systemd? 要不要我写启动脚本。
7. **安全**: 管理 API 默认只绑 127.0.0.1(本机), 不暴露外网。如果哪天要让别的机器/云上工具调用, 再谈鉴权。

## 6. 下一步建议

1. 你确认上面问题(尤其 1/2/3), 我把对应通道联调起来(需要凭据);
2. 构造一次真实 `ask_user_question`, 验证手机端“确认”往返;
3. 加“重启补发最近通知” + 图片转发;
4. 写 Windows/Ubuntu 的常驻启动脚本;
5. 如果想把桥接塞进 dsh 的插件体系(作为 client-plugin 跑在 web 里), 也可以, 但当前独立进程方案改动最小、不依赖 dsh 升级。

---

祝睡个好觉 🌙 明天见。

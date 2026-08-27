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

# 开发报告 (第 2 轮) — 新增与实测

> 第 2 轮在等待你答复的同时, 自主补完了几个不依赖凭据/决策的点。上面第 1 轮报告仍然有效。

## 本轮新增(全部有测试)

1. **确认流程演示 (demo_confirmation.py)** — 不需要真实账号, 用模拟的 dsh wire 跑通完整的"agent 提问 → 你回答 → agent 继续"闭环, 用的是**真实的 Hub/通道代码**。运行:
   ```
   python scripts/demo_confirmation.py            # 手动回答
   python scripts/demo_confirmation.py --auto 4   # 4 秒后自动回答
   ```
   实测输出(见下图): 问题带选项推送到通道, `/answer 1:方案A` 应答后 `答案已提交 ✅`, agent 继续并 `回合结束`。
2. **重启补发通知 (restart catch-up)** — 桥接重启后, 对之前正在跑的会话自动补发停机期间错过的通知(只补有进度水印的会话, 新会话不刷历史)。`config.yaml` 里 `catchUp` / `catchUpMaxEvents` 可关。
3. **Windows/Ubuntu 常驻脚本** — `scripts/run_bridge.sh|.ps1`、`scripts/install-systemd.sh`(Ubuntu 一键安装为 systemd 服务)、`scripts/install-windows-task.ps1`(Windows 登录自启任务)。
4. **跨平台 UTF-8 修复** — 之前 console 通道在 Windows(GBK 控制台)遇到 emoji(❓✅🛠)会崩, 现已统一按 UTF-8 输出; `python -m dsh_im_bridge` 启动时也会把 stdout/stderr 设为 UTF-8。
5. **git 仓库** — 项目已 `git init` 并提交首个版本(commit b3a34a8)。
6. **测试扩到 42 个** — 新增: console 通道入站投递、UTF-8 输出、桥接管理 API(/status /prompt /message /answer /approval /bind)、重启补发逻辑。

## 实测输出(确认流程, --auto 4)

```
[08:00:01] 用户: 开始任务
[08:00:01] 助手
我发现有两个可行方案，需要你确认选哪个。
❓ **需要你确认**
请选择方案: 采用哪个方案继续?
   - 方案A — 快速但不稳定
   - 方案B — 稍慢但稳定
请回复答案（例如：1 或选项文字 / 直接输入自定义答案）…
答案已提交 ✅
[08:00:01] 助手
[08:00:01] 🔧 工具结果: 执行完成，产出已保存。
[08:00:01] ✅ 回合结束
```

## 第 2 轮实测结果

- ✅ 42/42 单测通过(`python -m pytest -q`)
- ✅ 确认流程 demo 全链路跑通(见上)
- ✅ 真实 dsh 端到端再验证(建会话→prompt→OK→turn/end 通知), 且已把 agent 的"思考过程"从通知里剔除, 只显示最终回答
- ✅ **真实 dsh 上完成了完整的"问题确认"往返**(见下, 这是本程序最核心的场景)
- ✅ git 首版已提交

## 🎯 最重要的实测: 真实 dsh 上的"需要你确认"往返

第 2 轮里, 真实 dsh 的 agent 真的一次 `ask_user_question` 提问, 桥接程序把它捕获并推给通道, 我用 `/answer` 应答后 agent 继续并 `turn/end`:

```
(agent 收到消息后主动提问)
❓ **需要你确认**
...
POST /api/answer  ->  {"accepted": true}     ← 真实 dsh 接受了答案
(agent 继续) assistant/message -> turn/end
pending 数 -> 0
```

这个过程中还**修掉了一个真 bug**: 之前 `/answer 1:选项A` 把"1"当成问题 id 提交, 真实 dsh 会拒绝(`accepted: false`)——因为真实问题的 id 是随机的字符串, 不是数字。现在 `/answer` 会用 1-based 序号解析到**真实的问题 id**, 选项文字匹配 `selected`, 其余走 `custom`。已加单测。

## 第 2 轮发现的两个新问题(都在第 3 轮验证/修正)

1. ~~真实 dsh 不会在桥接重启后重放"未答复的问题"~~ **已推翻(第 3 轮实证)**: 真实 dsh **会**在 mux 重连时重放未答复的问题, 前提是**会话在连接时刻已绑定**。第 2 轮的"没收到"其实是测试时序问题(绑定建在 mux 连接之后, 问题先到、当时未绑定被丢弃)。第 3 轮已实测: 带绑定重启 → 问题被重新捕获(pending=1) → `/answer` 应答被真实 dsh 接受 → 解除。**真正的边界情况**: 问题到达时会话未绑定, 才会被丢弃(此时本来也没人能答)。因此桥接最好保持常驻, 且绑定应先于连接建立(正常启动顺序 `load_state → mux connect` 已保证)。
2. **PowerShell 的 `Invoke-RestMethod` 会把中文发成 `????`**(它按 ASCII 编码 JSON 体), 这会让 agent 看到乱码而去反问——正好帮我意外验证了问题流程, 但也说明**注入中文消息请用 curl/Python**(README 已注明)。另外 PowerShell `Set-Content -Encoding UTF8` 写文件带 BOM, 桥接读取状态文件已改为容忍 BOM(`utf-8-sig`)。

## 仍待你拍板(同第 1 轮第 5 节, 未变)

1. WOA 到底是什么?
2. 飞书: 自定义机器人(仅通知)还是应用机器人(双向)?
3. QQ: 用哪个 OneBot 实现, 账号是否方便挂协议端?
4. 自动建会话的默认目录/workspace
5. 通知策略(现在默认: 最终回答 + 工具错误/有输出 + 任务完成 + 需要确认/审批; 要不要只发"完成+确认"?)
6. 常驻方式确认(脚本已写好, 装不装由你定)
7. 管理 API 是否要暴露给别的机器(默认仅本机)

> 备注: 过程中在 dsh 里留下几个测试会话(`session-9b10cf8e…`、`session-26d65f46…` 等), 无害, 可忽略或手动归档。

---

# 开发报告 (第 3 轮) — 健壮性补强

## 本轮新增(全部有测试, 测试 53 个)

1. **重启后"未答复确认请求"的兜底提醒** — 第 2 轮曾以为 dsh 不重放未答复问题, 第 3 轮实证**它其实会重放**(见下), 所以兜底检测主要用于真正的边界: 问题到达时会话未绑定(比如还没绑定的会话)。桥接重启做 catch-up 时扫描 `session.history` 里的 `ask_user_question` `tool/call`(用真实 callId 数据验证), 若无对应 `tool/result` 且会话已绑定, 会主动提醒"⚠️ 检测到未答复确认请求…可 /cancel 中断或 /history 查看"。
2. **`/cancel` 指令** — 中断当前绑定的 dsh 会话; `/cancel-question` 保留为取消当前待确认问题。
3. **`/history [N]` 指令** — 把绑定会话最近 N 条记录拉进 IM。
4. **飞书加密与收包路径的单测**(此前完全没测过):
   - AES-256-CBC(Feishu 格式: sha256(encryptKey) 作密钥、前 16 字节作 IV、PKCS7)往返解密 ✅;
   - `im.message.receive_v1` 事件 → InboundMessage 映射(chat_id/chat_type、`@_user_1` 清洗、chat 类型白名单)✅。
   - **顺带修了一个真 bug**: 飞书事件里 `chat_id`/`chat_type` 直接在 message 对象上, 我原来错误地按 `message.chat` 取, 会导致飞书消息收不到。已修 + 测试锁定。
5. **状态文件容忍 UTF-8 BOM** — PowerShell `Set-Content -Encoding UTF8` 写出的状态文件带 BOM, 之前会导致加载失败; 现用 `utf-8-sig` 读取。加测试。
6. `cryptography` 列为 `[feishu]` 可选依赖(只有飞书开 encryptKey 才需要)。

## 🎯 第 3 轮最重要的实测: 重启后"未答复问题"能被重新捕获并应答

针对第 2 轮的疑问, 这次用一个真实挂起的问题(agent 之前问的、一直没答)做了完整验证:

```
1. 桥接带绑定 + 进度水印重启
2. mux 连接后, dsh 把未答复的问题重新推给桥接   →  log: question/requested 捕获
3. /status → pending: 1                          ← 问题被桥接重新捕获
4. POST /api/answer → {"accepted": true}         ← 真实 dsh 接受
5. /status → pending: 0                          ← 问题解除, agent 可继续
```

结论修正: **dsh 会在 mux 重连时重放未答复的问题**(前提: 会话在连接时刻已绑定)。第 2 轮说的"不重放"是测试时序误判。

## 第 3 轮实测结果

- ✅ 53/53 单测通过
- ✅ 真实 dsh 上验证"重启 → 重新捕获未答复问题 → /answer 应答 → 解除"全链路
- ✅ 用真实 dsh 的 `tool/call`(ask_user_question)数据结构验证了兜底检测
- ✅ 飞书加密/收包在无账号的情况下通过单测(联调仍需真账号)

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 4 轮) — 上手自检与配置校验

## 本轮新增(测试 63 个)

1. **`--check` 自检模式** — `python -m dsh_im_bridge --check [--config x.yaml]`: 校验配置、探测 dsh 连通性(HTTP /api + mux WebSocket 是否通、能否收到帧)、检查通道与状态目录可写, 输出清晰的 ✅/⚠️/❌ 报告后退出。**明天你睡醒第一件事跑它**, 5 秒就知道环境对不对。已在本机实测通过。
2. **配置校验 `config.validate()`** — 启动时自动跑, 有问题直接拒绝启动(exit 2)而不是带病运行; 覆盖: workspaceId 与 cwd 冲突、未知通道、飞书缺凭据/只配了一半、QQ 缺 wsUrl、无通道等。加了一组单测。
3. 测试 53 → **63 个**。

## 第 4 轮实测结果

- ✅ `--check` 在真实 dsh 上输出 OK(HTTP 通、mux 通且有帧、状态目录可写)
- ✅ 构造坏配置(冲突 + 未知通道 + 飞书无凭据), `--check` 全部准确标红/标黄
- ✅ 63/63 单测通过

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 5 轮) — 图片/附件支持

## 本轮新增(测试 68 个)

1. **图片/附件支持** — 之前 agent 消息里带图只显示 `[图片]` 占位。本轮打通:
   - `session.attachment` 客户端方法 + `extract_attachments`/`AttachmentRef`(解析 content 里的 image 块);
   - IM 文本显示 `[图片: 名字]`, 并追加 `📎 附件 [attachmentId=...]` 提示;
   - 桥接 API 新增 `GET /attachment?sessionId=..&attachmentId=..` 取回图片(base64);
   - `scripts/fetch_attachment.py <sessionId> <attachmentId> -o out.png` 一键落盘。
2. 修了一个路由 bug: 带 query 的 GET(`/attachment?x=1`)之前匹配不到路由, 已改为按去掉 query 的路径匹配。
3. 测试 63 → **68 个**(覆盖 attachment 客户端/解析/渲染/路由)。

> 飞书/QQ 通道的图片上传(im/v1/images / OneBot image)等拿到真账号后接入; 桥接层已就绪。

## 第 5 轮实测结果

- ✅ 68/68 单测通过
- ✅ `/attachment` 路由对真实 dsh 的 `session.attachment` 协议实现(单测锁定)
- ⚠️ 真实图片附件联调需等真账号(本机测试会话里没有 agent 生成的图)

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 6 轮) — 飞书发送路径测试 + 工程化收尾

## 本轮新增(测试 73 个)

1. **飞书 HTTP 发送路径单测**(此前只有加密/收包有测, 发送没测): 用假的飞书 API 服务器验证
   - 自定义机器人 webhook: 请求体 `{msg_type, content:{text}}` ✅;
   - 应用机器人: tenant token 获取 + `Authorization: Bearer` + `im/v1/messages` 请求体 ✅;
   - token 缓存(两次发送只取一次 token)✅;
   - 飞书返回错误码 → `FeishuError`; 未配置凭据 → `FeishuError` ✅。
   - 为可测性把飞书 `baseUrl` 做成可配置(默认官方域名, 测试指向假服务器)。
2. **Makefile**(Ubuntu 方便命令: `make install/test/check/run/demo`)。
3. 清理了飞书模块一个未使用的 import。
4. 测试 68 → **73 个**。

## 第 6 轮实测结果

- ✅ 73/73 单测通过
- ✅ 飞书 webhook + 应用机器人发送路径(请求构造/token 缓存/错误处理)全部单测锁定

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 7 轮) — 通知策略调优 + 启动通知

## 本轮新增(测试 75 个)

1. **默认通知策略调优** — 默认转发集从 `user/message + assistant/message + tool/result + turn/end + step/end` 调整为**去掉 step/end**: 每个步骤都会发一次 `step/end`, 多步骤任务会刷屏, 而 `turn/end` 已代表"任务完成"。对应报告第 1 轮问题 5 的建议(只发完成+确认)。`config.yaml` 里 `forwardEvents` 仍可自定义。
2. **启动通知 `notifyOnStart`** — 桥接启动时向每个已绑定会话发一条 "✅ dsh-im-bridge 已启动", 让你知道桥接在跑/重连成功。默认开, 可关。
3. 测试 73 → **75 个**(默认集不含 step/end、启动通知只发给已绑定会话)。

## 第 7 轮实测结果

- ✅ 75/75 单测通过
- ✅ 真实 dsh 上验证: 新默认策略下只收到 `助手 OK` + `✅ 回合结束`, 不再有步骤刷屏

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 8 轮) — 接收协议补测 + 一键全量自检

## 本轮新增(测试 79 个)

1. **接收协议路径补测**(之前只测了消息映射, 没测握手/收包循环):
   - 飞书长连接 `Challenge` 握手(含 encryptKey 时的 fake_challenge)✅;
   - 飞书 `Event` 帧经 `_handle_frame` 投递 ✅;
   - QQ OneBot **完整接收循环**: 真实 WS 连接 → 收到 group 消息 → 投递给 hub; 同一连接上 `send_group_msg` 往返 ✅。
2. **一键全量自检 `scripts/verify_all.py`** — `python scripts/verify_all.py [--auto 4]` 依次跑: ① 环境自检(`--check`)→ ② 确认流程演示(agent 提问→应答→继续)→ 全部通过才退出 0。实测通过。
   - 顺手修了 verify_all 自己的两个 bug(漏掉解释器前缀、自身 stdout 未设 UTF-8)。
3. 测试 75 → **79 个**。

## 第 8 轮实测结果

- ✅ 79/79 单测通过
- ✅ `verify_all.py` 全量自检通过(环境 OK + 确认流程 OK)

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 9 轮) — 代码清理 + 回归

## 本轮新增

1. **死代码清理** — 移除了 `formatter.py` 里未使用的常量(`FINAL_MESSAGE_EVENTS`/`TURN_START`/`TURN_END`/`STEP_END`)和 `event_kind_label`, 以及 `hub.py` 里对应的死 import 和一处函数内冗余 import。纯清理, 无行为变化。
2. **回归** — 79/79 单测通过; `verify_all.py` 全量自检再次通过(环境 OK + 确认流程 OK)。

## 第 9 轮实测结果

- ✅ 79/79 单测通过
- ✅ `verify_all.py` 全量自检通过

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 10 轮) — Ubuntu 真实环境验证 ✅

## 本轮新增

**在你机器上的 Ubuntu (WSL2) 里做了真实跨平台验证:**

- ✅ **79/79 单测在 Ubuntu 24.04 / Python 3.12.3 上全部通过**(Windows 上同样全过)——包括 WebSocket、HTTP、AES 加密、异步等所有路径, 代码本身完全跨平台。
- ✅ **确认流程 demo 在 Ubuntu 上跑通**: agent 提问 → `/answer` 应答 → `答案已提交 ✅` → `回合结束`; 中文/emoji 在 Ubuntu 原生 UTF-8 下显示正常(无 Windows 的 GBK 问题)。
- ✅ 两个 shell 脚本(`run_bridge.sh`、`install-systemd.sh`)通过 `bash -n` 语法检查。
- 新增 `scripts/wsl_probe.sh`(检查本机能否连到 dsh 的连通性小工具)。

## ⚠️ 发现一个真实部署问题(写给你)

**在 WSL(Ubuntu)里跑桥接、而 dsh 跑在 Windows 上时, 桥接默认连不到 dsh**: WSL2 里 `127.0.0.1:10010` 是 WSL 自己的回环, 不是 Windows 的, 实测 `Connection refused`。
解决办法(按情况选):
1. **桥接和 dsh 放同一系统**: 都在 Windows(最省事)或都在 Ubuntu 宿主机(非 WSL);
2. 若必须 WSL + Windows 混搭: 在 `~/.wslconfig` 开 `networkingMode=mirrored`, 或让 dsh 绑定非回环地址(`--host 0.0.0.0`)+ 配 `trustedHosts`, 桥接用 Windows 的 IP 连。

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 11 轮) — 清理测试会话

## 本轮新增

1. **清理我在你 dsh 里留下的测试会话** — 用 `workspace.archiveSession` 归档了 9 个我测试期间创建的会话(全部 cwd 含 `dsh-im-bridge` 或由桥接测试创建、均已空闲), 你的 dsh 侧边栏不再被我的测试会话占着。当前正在跑的目标会话未动。
2. **新增 `scripts/archive_test_sessions.py`** — 以后测试完可一键归档空闲且 cwd 匹配标记的会话(`--dry-run` 先预览)。

## 第 11 轮实测结果

- ✅ 9 个测试会话已归档(workspace 级, 可逆)
- ✅ 79/79 单测通过

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 12 轮) — 部署便利(env/日志/命令)

## 本轮新增(测试 81 个)

1. **`.env` 文件支持** — 启动时自动读取当前目录 `.env`(无额外依赖的小型解析器), 已有环境变量优先。凭据既可以放环境变量也可以放 `.env`。
2. **`--log-file` 日志落盘** — 日志同时写文件, 对 Windows 隐藏的计划任务特别有用; `install-windows-task.ps1` 已改为自动加 `--log-file bridge.log` 并给出查看日志的命令。
3. **`dsh-im-bridge` 命令** — 安装后可直接用命令(等价 `python -m dsh_im_bridge`)。
4. 测试 79 → **81 个**(.env 解析/优先级/缺失文件容错)。

## 第 12 轮实测结果

- ✅ 81/81 单测通过
- ✅ `dsh-im-bridge --check` 命令可用; `--log-file` 写入日志正常(实测含启动+连接 mux)

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 13 轮) — 联调指南

## 本轮新增

1. **新增 [INTEGRATION.md](INTEGRATION.md) 联调指南** — 把飞书(webhook 机器人 / 应用机器人 + 长连接)、QQ(OneBot11 + NapCat)、WOA(通用端点)从零到跑通的**逐步配置步骤**写成文档, 含常见排查表。你明天照着做就能把真实通道接上, 不用再翻代码。
2. README 顶部加了指向 INTEGRATION.md 的链接。

## 第 13 轮实测结果

- ✅ 81/81 单测通过(无代码改动, 回归)

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 14 轮) — 发送重试

## 本轮新增(测试 83 个)

1. **通道发送自动重试** — 给 IM 发消息时, 一次瞬时的网络抖动/通道错误不再静默丢失通知: 默认最多重试 2 次、退避 1s/2s(`config.yaml` 里 `sendRetries` / `sendRetryDelay` 可调, 设 0 关闭)。对"任务完成/需要确认"这类重要通知尤其有意义。
2. 测试 81 → **83 个**(瞬时失败后重试成功; 持续失败重试耗尽不抛错)。

## 第 14 轮实测结果

- ✅ 83/83 单测通过
- ✅ `verify_all.py` 全量自检通过

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 15 轮) — 状态可读性 + 全量回归

## 本轮新增(测试 84 个)

1. **`/status` 显示待确认问题的内容** — 之前只显示 rpcId/sessionId, 现在每个 pending 问题带 `question`(问题文本)和 `questionCount`, 你一眼能看到 agent 在等你答什么。
2. **全量回归** — 84/84 单测通过; `verify_all.py` 全量自检通过; 真实 dsh 端到端再验证(prompt→OK→turn/end 通知), 并清理了新产生的测试会话。

## 第 15 轮实测结果

- ✅ 84/84 单测通过
- ✅ `verify_all.py` 全量自检通过
- ✅ 真实 dsh 端到端(prompt→回复→通知)通过, 测试会话已归档

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 16 轮) — 全新安装验证

## 本轮新增

**模拟"用户第一次安装"做了全新安装验证:**

- ✅ 用全新 venv `pip install .`(非 editable)从 git 提交状态安装, 包能干净装上、`import dsh_im_bridge` 正常(version 0.1.0)。
- ✅ 在**安装后的包**(不是 src)上跑完整测试: 84/84 通过(装 `[feishu]` 可选依赖后; 裸装缺 `cryptography`, 只有 2 个飞书加密测试跳过——符合预期, `cryptography` 本来就是可选依赖)。
- 结论: 交付物是自包含、可安装的; 用户按 README 走 `pip install -e .[dev]`(或 `[feishu]`)即可, 不会缺文件。

## 第 16 轮实测结果

- ✅ 全新安装 + 84/84 测试通过
- ✅ git 工作区干净

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

# 开发报告 (第 17 轮) — 飞书长连接全链路测试

## 本轮新增(测试 85 个)

1. **飞书长连接全链路单测** — 用假 WS 服务器验证完整收包循环: 连接 → `Challenge` 握手(发 `ChallengeResponse`) → 收到 `Event` 帧 → 投递 → 断线后尝试重连。这是收包路径最后一个没测的环节。
   - 测试还顺带澄清了一个真实时序点: 飞书服务端会**等待握手响应**再继续, 我们的实现也是先回 `ChallengeResponse` 再收消息——假服务器按真实行为实现。
2. 测试 84 → **85 个**。

## 第 17 轮实测结果

- ✅ 85/85 单测通过
- ✅ 飞书长连接"握手→收消息→断线重连"全链路单测锁定

## 仍待你拍板(不变)

WOA 定义 / 飞书用哪种机器人 / QQ 用哪个 OneBot / 默认工作区 / 通知策略 / 常驻方式 / 管理 API 是否外露 —— 见第 1 轮第 5 节。

---

祝睡个好觉 🌙 明天见。

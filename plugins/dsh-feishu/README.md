# dsh-feishu

**让 dsh（DeepSeek Harness）通过飞书应用机器人收发消息的独立插件。**

在飞书里私聊机器人 / 群里 @机器人，就能指挥本机上的 dsh agent；dsh 完成任务、需要你确认时，结果会主动推回飞书。

本插件**只负责飞书**，复用 `dsh-im-bridge` 核心（dsh 客户端 / 路由 / 问题审批应答 / 飞书通道），但通过 `--only feishu` 只启用飞书通道——你不需要了解 `dsh-im-bridge` 的其他 IM 通道。

> ⚠️ 需要你的飞书开放平台应用凭据（App ID / App Secret）。支持两种接入：自定义机器人 webhook（仅发送）或应用机器人（双向收发）。

---

## 特性

- ✅ 飞书开放平台：自定义机器人 webhook（仅发送）或应用机器人（双向收发，事件长连接，AES 解密）
- ✅ 双向：飞书发消息 → dsh 执行 → 结果/完成/确认推回飞书
- ✅ 自动绑定：首次发消息自动创建 dsh 会话并绑定；`/attach` `/new` 可切换
- ✅ 直接回复即可回答 dsh 的确认问题（`1` / 选项文字 / `允许` / `拒绝` / `跳过` / `取消`）
- ✅ 超长消息自动拆成多条 `[1/N]` 顺序发送，不丢内容
- ✅ 跨平台：Windows / Ubuntu（Python ≥ 3.10）

---

## 安装

### 前置

1. Python ≥ 3.10
2. dsh web 已在本机运行（默认 `http://127.0.0.1:10010`）——桥接与 dsh 必须在同一台机器/网络

### 方式 A：从源码安装（本仓库内）

```bash
# 1) 进入本插件目录
cd dsh-im-bridge/plugins/dsh-feishu

# 2) 创建虚拟环境并安装（会自动拉取 dsh-im-bridge 核心依赖）
python -m venv .venv
.\.venv\Scripts\activate        # Windows
source .venv/bin/activate       # Ubuntu
pip install -e .

# 3) 生成配置并填写飞书凭据（见下方配置教程）
cp config.example.yaml config.yaml
```

> 如果你的 `dsh-im-bridge` 核心已通过 `pip install -e .` 装过，可直接 `pip install -e .` 本插件。

### 方式 B：独立安装（不 clone 整个仓库）

```bash
pip install dsh-im-bridge        # 核心（从 PyPI 或你的私有源）
pip install dsh-feishu           # 本插件
dsh-feishu --help
```

> 安装后得到 `dsh-feishu` 命令；运行时它只会启用飞书通道。

---

## 飞书开放平台配置教程

飞书有两种接入方式，**二选一**（或都配，webhook 优先用于发送）。

### 方式 A：自定义机器人 webhook（仅发送通知，最快）

1. 在飞书里建一个群 → 群设置 → 群机器人 → 添加机器人 → **自定义机器人**。
2. 复制 webhook 地址，形如 `https://open.feishu.cn/open-apis/bot/v2/hook/<一串token>`。
3. 配置：
   ```yaml
   channels:
     feishu:
       enabled: true
       webhookUrl: "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx"
   ```
   > 只能发（任务完成/确认会推到这个群），**不能收消息**。要先收消息（在群里直接喊 dsh）请用方式 B。

### 方式 B：应用机器人（收发双向，推荐）

1. 打开 [飞书开放平台](https://open.feishu.cn/app) → 创建企业自建应用。
2. 权限管理里开通并发布：
   - `im:message`（读取接收消息）
   - `im:message:send_as_bot`（以机器人身份发消息）
3. **事件订阅**：选择"长连接(WebSocket)"模式；添加事件 `im.message.receive_v1`（接收消息）。
   - ⚠️ **先别点保存**：长连接模式保存时会校验"是否有在线长连接客户端"。请先按第 5 步配好桥接并**启动桥接**（它会主动连飞书长连接），再回后台点保存，否则报"无在线长连接"。
   - 若开启事件加密，复制 `Encrypt Key`。
4. 拿到 `App ID` / `App Secret`。
5. 配置：
   ```yaml
   channels:
     feishu:
       enabled: true
       appId: "cli_xxxx"
       appSecret: "xxxx"
       # encryptKey: "xxxx"     # 仅当你在事件订阅里开了加密
       receiveChatTypes: ["p2p", "group"]
   ```
6. 启动桥接 `dsh-feishu --config config.yaml --log-file bridge.log`，日志出现 `feishu ws endpoint: ...` 说明长连接已连上；此时回飞书后台**保存**事件订阅配置。
7. 把机器人拉进你的群（或直接私聊机器人），群里 @机器人 / 私聊发消息即可驱动 dsh。

---

## 配置

把 `config.example.yaml` 复制为 `config.yaml`，填上凭据（方式 A 填 `webhookUrl`，方式 B 填 `appId`/`appSecret`）。

凭据也可用环境变量（运行时优先）：

```bash
export DSH_IM_BRIDGE_FEISHU_APP_ID=cli_xxxx
export DSH_IM_BRIDGE_FEISHU_APP_SECRET=xxxx
```

---

## 运行

```bash
# 自检（配置 + dsh 连通性 + 通道状态）
dsh-feishu --config config.yaml --check

# 启动（前台）
dsh-feishu --config config.yaml --log-file bridge.log

# 发送一条测试消息到指定会话（应用机器人需要 chat_id）
dsh-feishu --config config.yaml --test-notify feishu --notify-target <chat_id>
```

启动后日志出现 `feishu ws endpoint: ...` 即代表已连上飞书长连接。此时**在飞书里私聊机器人 / 群里 @机器人**即可驱动 dsh。

首次发消息会自动创建一个 dsh 会话并绑定，之后该会话的完成/确认都会推回飞书。

### 常用指令（在飞书里发给机器人）

| 指令 | 说明 |
|---|---|
| `/help` | 查看全部指令 |
| `/status` | 查看桥接与 dsh 状态 |
| `/sessions` | 列出 dsh 会话 |
| `/attach <会话ID>` | 绑定到已有会话 |
| `/new` | 新建会话并绑定 |
| `/cancel` | 中断当前会话（例如卡在等待确认） |
| `/history [N]` | 拉取最近 N 条记录 |

### 回答 dsh 的确认问题

dsh 弹出问题/审批时，**直接回复即可**：

- `1` / `1,2` → 按选项序号选择
- 选项文字 → 选中该选项
- 任意文本 → 作为自定义答案
- `跳过` / `取消` → 取消当前问题
- `允许` / `拒绝` → 处理工具调用审批

---

## 常见问题

| 现象 | 排查 |
|---|---|
| 发送报 `230006 Bot ability is not activated` | 应用**未发布**：应用能力里加"机器人"、权限生效后，去"版本管理与发布"→ 创建版本 → 发布 |
| 飞书能发不能收 | 用了自定义机器人(仅发送)，改用应用机器人 + 长连接 |
| 飞书日志没有 `endpoint` | 应用没开"长连接"事件订阅 / 没发布权限；或事件订阅配置里"在线长连接客户端"校验失败 |
| 长消息只看到一部分 | 已默认自动拆分（`[1/N]`）；若仍截断，检查 `splitLongMessages` 是否被关掉 |
| 需要排查 | 加 `--log-file bridge.log` 看日志；或 `dsh-feishu --check` 自检 |

---

## 目录结构

```
plugins/dsh-feishu/
├── pyproject.toml          # 独立可安装的 Python 包
├── config.example.yaml     # 配置模板（只含 feishu 通道）
├── README.md               # 本文件
└── src/dsh_feishu/
    └── __main__.py         # 入口：强制 --only feishu 后调用核心桥接
```

核心实现（DshClient / BridgeHub / 事件 / 格式化 / feishu 通道）位于 `dsh-im-bridge` 核心包，本插件不重复实现。

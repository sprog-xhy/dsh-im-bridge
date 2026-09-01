# dsh-qq

**让 dsh（DeepSeek Harness）通过 QQ 官方开放平台机器人收发 C2C 私聊消息的独立插件。**

在 QQ 里私聊你的机器人，就能指挥本机上的 dsh agent；dsh 完成任务、需要你确认时，结果会主动推回 QQ。

本插件**只负责 QQ**（官方开放平台机器人 API），复用 `dsh-im-bridge` 核心（dsh 客户端 / 路由 / 问题审批应答），但通过 `--only qq_official` 只启用 QQ 官方通道——你不需要了解 `dsh-im-bridge` 的其他 IM 通道。

> ⚠️ 需要你的 QQ 开放平台机器人应用凭据（AppID / AppSecret）。不用跑 NapCat 等协议端、不需要 QQ 小号。

---

## 特性

- ✅ QQ 官方开放平台机器人 API（q.qq.com），官方 WebSocket 长连接收发，无需第三方协议端
- ✅ C2C 私聊双向：QQ 发消息 → dsh 执行 → 结果/完成/确认推回 QQ
- ✅ 自动绑定：首次私聊自动创建 dsh 会话并绑定；`/attach` `/new` 可切换
- ✅ 直接回复即可回答 dsh 的确认问题（`1` / 选项文字 / `允许` / `拒绝` / `跳过` / `取消`）
- ✅ 超长消息自动拆成多条 `[1/N]` 顺序发送，不丢内容
- ✅ 问题/审批用 QQ markdown 渲染（`msg_type=2`），失败自动回退纯文本
- ✅ 跨平台：Windows / Ubuntu（Python ≥ 3.10）

---

## 安装

### 前置

1. Python ≥ 3.10
2. dsh web 已在本机运行（默认 `http://127.0.0.1:10010`）——桥接与 dsh 必须在同一台机器/网络

### 方式 A：从源码安装（本仓库内）

```bash
# 1) 进入本插件目录
cd dsh-im-bridge/plugins/dsh-qq

# 2) 创建虚拟环境并安装（会自动拉取 dsh-im-bridge 核心依赖）
python -m venv .venv
.\.venv\Scripts\activate        # Windows
source .venv/bin/activate       # Ubuntu
pip install -e .

# 3) 生成配置并填写 QQ 开放平台凭据（见下方配置教程）
cp config.example.yaml config.yaml
```

> 如果你的 `dsh-im-bridge` 核心已通过 `pip install -e .` 装过，可直接 `pip install -e .` 本插件。

### 方式 B：独立安装（不 clone 整个仓库）

```bash
pip install dsh-im-bridge        # 核心（从 PyPI 或你的私有源）
pip install dsh-qq               # 本插件
dsh-qq --help
```

> 安装后得到 `dsh-qq` 命令；运行时它只会启用 QQ 官方通道。

---

## QQ 开放平台配置教程（q.qq.com）

1. 打开 [QQ 开放平台](https://q.qq.com)（用你的 QQ 登录），进入**开发设置 / 我的机器人**。
2. 创建一个机器人应用（或进入已有应用），在「凭证」里拿到 **AppID**（纯数字）和 **AppSecret**。
3. 在应用里确认已启用 **机器人能力** 与 **C2C（单聊）私信**能力；正式使用需按平台要求完成**身份认证/过审**（沙箱环境可先测）。
4. 把机器人**加为你的 QQ 好友**（或在平台内配置测试成员），才能收到你的私聊消息。

### 沙箱 / 正式

| 环境 | 配置 | 说明 |
|---|---|---|
| 正式 | `sandbox: false`（默认） | 面向真实用户，需应用过审 |
| 沙箱 | `sandbox: true` | 免审核测试，用沙箱的 AppID/AppSecret，域名 `sandbox.api.bot.qq.com` |

---

## 配置

把 `config.example.yaml` 复制为 `config.yaml`，填上凭据：

```yaml
channels:
  qq_official:
    enabled: true
    appId: "1905533507"        # ← 你的 AppID
    appSecret: "xxxx"          # ← 你的 AppSecret
    sandbox: false
```

凭据也可用环境变量（运行时优先）：

```bash
export DSH_IM_BRIDGE_QQ_OFFICIAL_APP_ID=1905533507
export DSH_IM_BRIDGE_QQ_OFFICIAL_APP_SECRET=xxxx
```

---

## 运行

```bash
# 自检（配置 + dsh 连通性 + 通道状态）
dsh-qq --config config.yaml --check

# 启动（前台）
dsh-qq --config config.yaml --log-file bridge.log

# 发送一条测试消息到你自己的 openid（先私聊机器人一句，从日志/状态拿 openid）
dsh-qq --config config.yaml --test-notify qq_official --notify-target <你的openid>
```

启动后日志出现 `qq_official WS connected` 即代表已连上官方长连接。此时**用 QQ 私聊机器人**即可驱动 dsh：

```
你: 帮我写一个 hello.py 并运行
dsh: ✅ 已创建并运行，输出：...
```

首次私聊会自动创建一个 dsh 会话并绑定，之后该会话的完成/确认都会推回 QQ。

### 常用指令（在 QQ 里发给机器人）

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
| 日志没有 `WS connected` | AppID/AppSecret 错误、未启用机器人能力、网络不通 |
| 收不到你的消息 | 机器人还没加你为好友；C2C 权限未生效；开了 `allowUsers` 但你的 openid 不在列表 |
| 发送报错 `msg_id 已过期` | 被动回复有时效（约 5-60 分钟），收到消息后尽早回复；桥接会自动带 `msg_id` |
| 长消息只看到一部分 | 已默认自动拆分（`[1/N]`）；若仍截断，检查 `splitLongMessages` 是否被关掉 |
| 需要排查 | 加 `--log-file bridge.log` 看日志；或 `dsh-qq --check` 自检 |

---

## 目录结构

```
plugins/dsh-qq/
├── pyproject.toml          # 独立可安装的 Python 包
├── config.example.yaml     # 配置模板（只含 qq_official 通道）
├── README.md               # 本文件
└── src/dsh_qq/
    └── __main__.py         # 入口：强制 --only qq_official 后调用核心桥接
```

核心实现（DshClient / BridgeHub / 事件 / 格式化 / qq_official 通道）位于 `dsh-im-bridge` 核心包，本插件不重复实现。

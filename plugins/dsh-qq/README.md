# dsh-qq

**dsh-qq：让 dsh（DeepSeek Harness）通过 QQ 官方开放平台机器人收发 C2C 私聊消息的官方规范插件。**

在 QQ 里私聊你的机器人，就能指挥本机上的 dsh agent；dsh 完成任务、需要你确认时，结果会主动推回 QQ。

> ⚠️ 本插件是 **DeepSeek Harness 官方插件规范的 Node.js Cordis 插件**（npm 包 + `dsh.bundle.patch` + `cordis.patch.yml`），**不是**独立 Python 进程，也**不需要** pip 安装。安装方式见下方「安装」。

---

## 特性

- ✅ 符合 DeepSeek Harness 插件规范：Cordis 插件（`name` / `inject` / `Config` / `apply(ctx, config)`）
- ✅ QQ 官方开放平台机器人 API（q.qq.com），官方 WebSocket 长连接收发，无需第三方协议端 / QQ 小号
- ✅ C2C 私聊双向：QQ 发消息 → dsh agent 执行 → 结果/完成推回 QQ
- ✅ 自动绑定：每个 QQ 用户自动对应一个稳定的 dsh 会话（`qq-official-<openid>`）
- ✅ 通过 dsh 的 `session/event` 事件流把 assistant 回复 / 任务结束 / 失败推回 QQ
- ✅ `/qq-test` 命令一键验证凭据连通性
- ✅ 凭据经 dsh 凭据服务/环境解析（`QQ_OFFICIAL_APP_ID` / `QQ_OFFICIAL_APP_SECRET`）
- ✅ 超长消息由核心自动拆分（`[1/N]`），不丢内容

---

## 安装（按 dsh 插件规范）

本插件是一个 npm 包，装进 dsh 的 **profile**（例如 `web`），与 dsh 官方插件 `dsh-web-search-wps` 完全相同的机制。

### 前置

- 已安装 `dsh`（本机已有：`dsh` CLI / `~/.dsh/profiles/web`）
- dsh web 正在运行（默认 `http://127.0.0.1:10010`）

### 方式 A：从本仓库源码安装（推荐）

```bash
# 1) 在 dsh 的 profile 里登记这个 bundle（把它加入 profile 的依赖与 bundles 列表）
cd ~/.dsh/profiles/web
pnpm add link:<本仓库绝对路径>/plugins/dsh-qq
```

然后在 `~/.dsh/profiles/web/package.json` 的 `dsh.profile.bundles` 数组里加上 `"dsh-qq"`：

```json
{
  "dependencies": { "dsh-qq": "link:<...>/plugins/dsh-qq" },
  "dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app", "dsh-qq"] } }
}
```

> 或者用 `dsh plugin --profile web <pnpm args>`（等价于在 profile 目录跑 pnpm）安装。

### 方式 B：发布到 npm / 私有源后

```bash
cd ~/.dsh/profiles/web
pnpm add dsh-qq
# 同样把 "dsh-qq" 加进 dsh.profile.bundles
```

### 加载

- 修改 profile 后**重启 dsh web** 才会加载插件（dsh 插件机制要求；与官方插件一致）。
- 重启后插件自动连接 QQ 官方长连接；日志可见 `dsh-qq: connecting ...`。

---

## QQ 开放平台配置教程（q.qq.com）

1. 打开 [QQ 开放平台](https://q.qq.com)（用你的 QQ 登录），创建机器人应用。
2. 在「凭证」里拿到 **AppID**（纯数字）和 **AppSecret**。
3. 确认启用 **机器人能力** 与 **C2C（单聊）私信**；正式使用需按平台要求过审（沙箱可先测）。
4. 把机器人**加为你的 QQ 好友**，才能收到私聊消息。

### 配置凭据

插件通过 dsh 凭据服务/环境变量解析 `QQ_OFFICIAL_APP_ID` / `QQ_OFFICIAL_APP_SECRET`（默认 `cordis.patch.yml` 里的 `appIdEnv` / `appSecretEnv`）：

- 在 dsh 凭据里存这两个 key（dsh web 的 Models/凭据页），**或**
- 在启动环境导出：
  ```bash
  export QQ_OFFICIAL_APP_ID=1905533507
  export QQ_OFFICIAL_APP_SECRET=xxxx
  ```
- 也可直接在 `cordis.patch.yml` 的插件行 `config` 里内联。

### 沙箱 / 正式

`cordis.patch.yml` 插件行 `config.sandbox`：`false`（正式，默认）或 `true`（沙箱 `sandbox.api.bot.qq.com`）。

---

## 使用

启动后，**用 QQ 私聊机器人**即可驱动 dsh：

```
你: 帮我写一个 hello.py 并运行
dsh: ✅ 已创建并运行，输出：...
```

dsh 回复会推回 QQ；任务失败/中断也会有提示。

### 验证

```bash
dsh-qq 桥接运行后，在 dsh 里执行 /qq-test
```

（`/qq-test` 命令由本插件注册，返回凭据有效性。）

---

## 目录结构

```
plugins/dsh-qq/
├── package.json          # npm 包 + dsh.bundle.patch
├── cordis.patch.yml      # 把 dsh-qq 插件行插入 profile 配置树
├── lib/
│   ├── index.js          # Cordis 插件：agents.create + followup + session/event + 命令
│   ├── client.js         # QQ 官方协议传输层（token / WS 长连接 / C2C / 发送）
│   └── types/index.d.ts  # 类型声明
├── tests/client.test.js  # 协议层单测（假 fetch / 假 WebSocket）
└── README.md             # 本文件
```

## 开发

```bash
node --test plugins/dsh-qq/tests/client.test.js   # 协议层单测
node --check plugins/dsh-qq/lib/index.js           # 语法检查
```

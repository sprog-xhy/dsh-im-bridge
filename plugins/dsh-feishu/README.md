# dsh-feishu

**dsh-feishu：让 dsh（DeepSeek Harness）通过飞书应用机器人收发消息的官方规范插件。**

在飞书里私聊机器人 / 群里 @机器人，就能指挥本机上的 dsh agent；dsh 完成任务、需要你确认时，结果会主动推回飞书。

> ⚠️ 本插件是 **DeepSeek Harness 官方插件规范的 Node.js Cordis 插件**（npm 包 + `dsh.bundle.patch` + `cordis.patch.yml`），**不是**独立 Python 进程，也**不需要** pip 安装。

---

## 特性

- ✅ 符合 DeepSeek Harness 插件规范：Cordis 插件（`name` / `inject` / `Config` / `apply(ctx, config)`）
- ✅ 飞书开放平台：自定义机器人 webhook（仅发送）或应用机器人（双向收发，事件长连接，AES 解密）
- ✅ 双向：飞书发消息 → dsh agent 执行 → 结果/完成推回飞书
- ✅ 自动绑定：每个飞书会话（chat_id）自动对应一个稳定的 dsh 会话（`feishu-<chat_id>`）
- ✅ 通过 dsh 的 `session/event` 事件流把 assistant 回复 / 任务结束 / 失败推回飞书
- ✅ `/feishu-test` 命令一键验证凭据连通性
- ✅ 凭据经 dsh 凭据服务/环境解析（`FEISHU_APP_ID` / `FEISHU_APP_SECRET`）
- ✅ 超长消息由核心自动拆分（`[1/N]`），不丢内容

---

## 安装（按 dsh 插件规范）

本插件是一个 npm 包，装进 dsh 的 **profile**（例如 `web`），与 dsh 官方插件 `dsh-web-search-wps` 完全相同的机制。

### 前置

- 已安装 `dsh`（本机已有：`dsh` CLI / `~/.dsh/profiles/web`）
- dsh web 正在运行（默认 `http://127.0.0.1:10010`）

### 方式 A：从本仓库源码安装（推荐）

```bash
# 1) 在 dsh 的 profile 里登记这个 bundle
cd ~/.dsh/profiles/web
pnpm add link:<本仓库绝对路径>/plugins/dsh-feishu
```

然后在 `~/.dsh/profiles/web/package.json` 的 `dsh.profile.bundles` 数组里加上 `"dsh-feishu"`。

> 或者用 `dsh plugin --profile web <pnpm args>`（等价于在 profile 目录跑 pnpm）安装。

### 方式 B：发布到 npm / 私有源后

```bash
cd ~/.dsh/profiles/web
pnpm add dsh-feishu
# 同样把 "dsh-feishu" 加进 dsh.profile.bundles
```

### 加载

- 修改 profile 后**重启 dsh web** 才会加载插件（与官方插件一致）。
- 重启后插件自动连接飞书长连接；日志可见 `dsh-feishu: connecting ...`。

---

## 飞书开放平台配置教程

飞书有两种接入方式，**二选一**（或都配，webhook 优先用于发送）。

### 方式 A：自定义机器人 webhook（仅发送通知，最快）

1. 在飞书里建一个群 → 群设置 → 群机器人 → 添加机器人 → **自定义机器人**。
2. 复制 webhook 地址，形如 `https://open.feishu.cn/open-apis/bot/v2/hook/<一串token>`。
3. 在 `cordis.patch.yml` 插件行配置 `webhookUrl`。
   > 只能发（任务完成/确认会推到这个群），**不能收消息**。要先收消息请用方式 B。

### 方式 B：应用机器人（收发双向，推荐）

1. 打开 [飞书开放平台](https://open.feishu.cn/app) → 创建企业自建应用。
2. 权限管理里开通并发布：`im:message`（读取接收消息）、`im:message:send_as_bot`（以机器人身份发消息）。
3. **事件订阅**：选择"长连接(WebSocket)"模式；添加事件 `im.message.receive_v1`。
   - ⚠️ **先别点保存**：长连接模式保存时会校验"是否有在线长连接客户端"。请先启动桥接（插件会主动连飞书长连接），再回后台点保存。
   - 若开启事件加密，复制 `Encrypt Key` 配置到 `encryptKey`。
4. 拿到 `App ID` / `App Secret`。
5. 配置 `appIdEnv` / `appSecretEnv` 对应的凭据。
6. 启动后，把机器人拉进群（或直接私聊机器人）即可驱动 dsh。

---

## 配置凭据

插件通过 dsh 凭据服务/环境变量解析 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`（默认 `cordis.patch.yml` 里的 `appIdEnv` / `appSecretEnv`）：

- 在 dsh 凭据里存这两个 key（dsh web 的 Models/凭据页），**或**
- 在启动环境导出：
  ```bash
  export FEISHU_APP_ID=cli_xxxx
  export FEISHU_APP_SECRET=xxxx
  ```
- 也可直接在 `cordis.patch.yml` 的插件行 `config` 里内联。

---

## 使用

启动后，**在飞书里私聊机器人 / 群里 @机器人**即可驱动 dsh：

```
你: 帮我写一个 hello.py 并运行
dsh: ✅ 已创建并运行，输出：...
```

### 验证

```bash
dsh-feishu 桥接运行后，在 dsh 里执行 /feishu-test
```

---

## 目录结构

```
plugins/dsh-feishu/
├── package.json          # npm 包 + dsh.bundle.patch
├── cordis.patch.yml      # 把 dsh-feishu 插件行插入 profile 配置树
├── lib/
│   ├── index.js          # Cordis 插件：agents.create + followup + session/event + 命令
│   ├── client.js         # 飞书协议传输层（token / webhook / im 发送 / 长连接 protobuf 帧 / AES）
│   └── types/index.d.ts  # 类型声明
├── tests/client.test.js  # 协议层单测（假 fetch / 假 WebSocket）
└── README.md             # 本文件
```

## 开发

```bash
node --test plugins/dsh-feishu/tests/client.test.js   # 协议层单测
node --check plugins/dsh-feishu/lib/index.js          # 语法检查
```

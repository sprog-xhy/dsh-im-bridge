# dsh-feishu

[English](README.en.md) | 中文

**dsh-feishu** 是 [DeepSeek Harness (dsh)](https://github.com/sprog-xhy/dsh-im-bridge) 的**官方规范插件**：通过**飞书应用机器人**收发消息，让 dsh 与飞书互通。

在飞书里私聊机器人 / 群里 @ 机器人，就能指挥本机上的 dsh agent；dsh 完成任务、需要你确认时，结果会自动推回飞书。

> 本插件是 **DeepSeek Harness 官方插件规范的 Node.js Cordis 插件**（npm 包 + `dsh.bundle.patch` + `cordis.patch.yml`），**不是** Python 进程，也**不需要** pip 安装。

---

## 特性

- ✅ 飞书开放平台：应用机器人（收发双向）或自定义机器人 webhook（仅发送通知）
- ✅ 双向：飞书发消息 → dsh 执行 → 结果 / 完成推回飞书
- ✅ 自动绑定：每个飞书会话（chat_id）自动对应一个稳定的 dsh 会话（`feishu-<chat_id>`）
- ✅ 超长消息自动拆分（`[1/N]`），不丢内容
- ✅ `/feishu-test` 命令一键验证凭据连通性

---

## 安装

前置条件：已安装 `dsh`（如 `~/.dsh/profiles/web`），且 dsh web 正在运行（默认 `http://127.0.0.1:10010`）。

```bash
# 1) 在 dsh 的 profile 里登记这个插件（链接到本仓库源码）
cd ~/.dsh/profiles/web
pnpm add link:<本仓库绝对路径>/plugins/dsh-feishu
```

然后在 `~/.dsh/profiles/web/package.json` 的 `dsh.profile.bundles` 数组里加上 `"dsh-feishu"`：

```json
{
  "dependencies": { "dsh-feishu": "link:<...>/plugins/dsh-feishu" },
  "dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app", "dsh-feishu"] } }
}
```

**重启 dsh web** 后插件生效，并自动连接飞书长连接。

---

## 配置：飞书开放平台

飞书有两种接入方式，**二选一**（或都配，webhook 优先用于发送）。

### 方式 A：自定义机器人 webhook（仅发送通知，最快）

1. 在飞书里建一个群 → 群设置 → 群机器人 → 添加机器人 → **自定义机器人**。
2. 复制 webhook 地址，形如 `https://open.feishu.cn/open-apis/bot/v2/hook/<token>`。
3. 在 `cordis.patch.yml` 插件行的 `config` 里配置 `webhookUrl`。
   > 只能发送（任务完成 / 确认会推到这个群），**不能收消息**。要收发请用方式 B。

### 方式 B：应用机器人（收发双向，推荐）

1. 打开 [飞书开放平台](https://open.feishu.cn/app) → 创建企业自建应用。
2. 权限管理里开通并发布：`im:message`（读取接收消息）、`im:message:send_as_bot`（以机器人身份发消息）。
3. **事件订阅**：选择「长连接 (WebSocket)」模式；添加事件 `im.message.receive_v1`。
   - ⚠️ **先别点保存**：长连接模式保存时会校验「是否有在线长连接客户端」。请先启动插件（它会主动连飞书长连接），再回后台点保存。
   - 若开启事件加密，复制 `Encrypt Key` 配置到 `encryptKey`。
4. 拿到 **App ID** / **App Secret**。
5. 启动后，把机器人拉进群（或直接私聊机器人）即可驱动 dsh。

---

## 配置凭据

插件通过 dsh 凭据服务 / 环境变量解析 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`（即 `cordis.patch.yml` 里的 `appIdEnv` / `appSecretEnv`）：

- 在 dsh web 的凭据页存这两个 key，**或**
- 在启动环境导出：

```bash
export FEISHU_APP_ID=cli_xxxx
export FEISHU_APP_SECRET=xxxx
```

- 也可直接在 `cordis.patch.yml` 插件行的 `config` 里内联。

---

## 使用

启动后，**在飞书里私聊机器人 / 群里 @ 机器人**即可驱动 dsh：

```
你：帮我写一个 hello.py 并运行
dsh：✅ 已创建并运行，输出：...
```

dsh 的回复会推回飞书；任务失败 / 中断也会有提示。

验证：在 dsh 里执行 `/feishu-test`，返回凭据是否有效。

---

## 更多

- [English](README.en.md)

# dsh-qq

[English](README.en.md) | 中文

**dsh-qq** 是 [DeepSeek Harness (dsh)](https://github.com/sprog-xhy/dsh-im-bridge) 的**官方规范插件**：通过 **QQ 官方开放平台机器人**收发 C2C 私聊消息，让 dsh 与 QQ 互通。

在 QQ 里私聊你的机器人，就能指挥本机上的 dsh agent；dsh 完成任务、需要你确认时，结果会自动推回 QQ。

> 本插件是 **DeepSeek Harness 官方插件规范的 Node.js Cordis 插件**（npm 包 + `dsh.bundle.patch` + `cordis.patch.yml`），**不是** Python 进程，也**不需要** pip 安装。

---

## 特性

- ✅ QQ 官方开放平台机器人 API（q.qq.com），官方 WebSocket 长连接，无需第三方协议端 / 小号
- ✅ C2C 私聊双向：QQ 发消息 → dsh 执行 → 结果 / 完成推回 QQ
- ✅ 自动绑定：每个 QQ 用户自动对应一个稳定的 dsh 会话（`qq-official-<openid>`）
- ✅ 超长消息自动拆分（`[1/N]`），不丢内容
- ✅ `/qq-test` 命令一键验证凭据连通性

---

## 安装

前置条件：已安装 `dsh`（如 `~/.dsh/profiles/web`），且 dsh web 正在运行（默认 `http://127.0.0.1:10010`）。

```bash
# 1) 在 dsh 的 profile 里登记这个插件（链接到本仓库源码）
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

**重启 dsh web** 后插件生效，并自动连接 QQ 官方长连接。

---

## 配置：QQ 开放平台（q.qq.com）

1. 打开 [QQ 开放平台](https://q.qq.com)，用你的 QQ 登录，创建机器人应用。
2. 在「凭证」里拿到 **AppID**（纯数字）和 **AppSecret**。
3. 确认启用**机器人能力**与 **C2C（单聊）私信**；正式使用需按平台要求过审（沙箱可先测）。
4. 把机器人**加为你的 QQ 好友**，才能收到私聊消息。

### 配置凭据

插件通过 dsh 凭据服务 / 环境变量解析 `QQ_OFFICIAL_APP_ID` / `QQ_OFFICIAL_APP_SECRET`（即 `cordis.patch.yml` 里的 `appIdEnv` / `appSecretEnv`）：

- 在 dsh web 的凭据页存这两个 key，**或**
- 在启动环境导出：

```bash
export QQ_OFFICIAL_APP_ID=1905533507
export QQ_OFFICIAL_APP_SECRET=xxxx
```

- 也可直接在 `cordis.patch.yml` 插件行的 `config` 里内联。

---

## 使用

启动后，**用 QQ 私聊机器人**即可驱动 dsh：

```
你：帮我写一个 hello.py 并运行
dsh：✅ 已创建并运行，输出：...
```

dsh 的回复会推回 QQ；任务失败 / 中断也会有提示。

验证：在 dsh 里执行 `/qq-test`，返回凭据是否有效。

---

## 更多

- [English](README.en.md)

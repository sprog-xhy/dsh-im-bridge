# dsh-feishu

English | [中文](README.md)

**dsh-feishu** is an **official plugin** for [DeepSeek Harness (dsh)](https://github.com/sprog-xhy/dsh-im-bridge) that connects dsh to the **Feishu (飞书) app bot** for messaging.

Private-message the bot in Feishu or @ it in a group to drive the dsh agent on your machine; when dsh finishes a task or needs your confirmation, results are pushed back to Feishu.

> This plugin is a **Node.js Cordis plugin built to the official DeepSeek Harness plugin spec** (npm package + `dsh.bundle.patch` + `cordis.patch.yml`). It is **not** a Python process and needs **no pip install**.

---

## Features

- ✅ Feishu open platform: app bot (two-way) or custom-bot webhook (send-only notifications)
- ✅ Two-way: Feishu message → dsh runs → result / completion pushed back to Feishu
- ✅ Auto-binding: each Feishu chat (chat_id) maps to a stable dsh session (`feishu-<chat_id>`)
- ✅ Over-long messages auto-split into `[1/N]` parts, nothing lost
- ✅ `/feishu-test` command for one-click credential connectivity checks

---

## Install

Prerequisites: `dsh` installed (e.g. `~/.dsh/profiles/web`), and dsh web running (default `http://127.0.0.1:10010`).

```bash
# 1) Register this plugin in your dsh profile (link to this repo's source)
cd ~/.dsh/profiles/web
pnpm add link:<absolute path to this repo>/plugins/dsh-feishu
```

Then add `"dsh-feishu"` to the `dsh.profile.bundles` array in `~/.dsh/profiles/web/package.json`:

```json
{
  "dependencies": { "dsh-feishu": "link:<...>/plugins/dsh-feishu" },
  "dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app", "dsh-feishu"] } }
}
```

**Restart dsh web** for the plugin to load; it then auto-connects the Feishu long connection.

---

## Setup: Feishu open platform

Feishu offers two integration modes, **choose one** (or configure both; the webhook takes precedence for sending).

### Option A: custom-bot webhook (send-only notifications, fastest)

1. In Feishu, create a group → Group Settings → Group Bot → Add Bot → **Custom Bot**.
2. Copy the webhook URL, like `https://open.feishu.cn/open-apis/bot/v2/hook/<token>`.
3. Set `webhookUrl` in the plugin row `config` of `cordis.patch.yml`.
   > Send-only (task completion / confirmations go to this group); **cannot receive messages**. Use Option B to receive.

### Option B: app bot (two-way, recommended)

1. Open the [Feishu open platform](https://open.feishu.cn/app) → create an enterprise self-built app.
2. In Permissions, enable and publish: `im:message` (read incoming messages), `im:message:send_as_bot` (send as the bot).
3. **Event subscription**: choose "Long connection (WebSocket)" mode; add the `im.message.receive_v1` event.
   - ⚠️ **Don't save yet**: long-connection mode validates "is there an online long-connection client" when saving. Start the plugin first (it actively connects to the Feishu long connection), then go back and save.
   - If event encryption is enabled, copy the `Encrypt Key` into `encryptKey`.
4. Get **App ID** / **App Secret**.
5. After startup, add the bot to a group (or private-message it) to drive dsh.

---

## Credentials

The plugin resolves `FEISHU_APP_ID` / `FEISHU_APP_SECRET` through the dsh credentials service / environment (the `appIdEnv` / `appSecretEnv` in `cordis.patch.yml`):

- Store both keys in the dsh web credentials page, **or**
- Export them in the launching environment:

```bash
export FEISHU_APP_ID=cli_xxxx
export FEISHU_APP_SECRET=xxxx
```

- Or set them inline in the plugin row `config` of `cordis.patch.yml`.

---

## Usage

After startup, **private-message the bot in Feishu / @ it in a group** to drive dsh:

```
You: write a hello.py and run it
dsh: ✅ created and ran it, output: ...
```

dsh replies are pushed back to Feishu; task failures / interruptions are also reported.

Verify: run `/feishu-test` inside dsh — it returns whether the credentials are valid.

---

## More

- [中文](README.md)

# dsh-qq

English | [中文](README.md)

**dsh-qq** is an **official plugin** for [DeepSeek Harness (dsh)](https://github.com/sprog-xhy/dsh-im-bridge) that connects dsh to the **QQ open-platform bot** over C2C (private-chat) messages.

Private-message your bot in QQ to drive the dsh agent on your machine; when dsh finishes a task or needs your confirmation, results are pushed back to QQ.

> This plugin is a **Node.js Cordis plugin built to the official DeepSeek Harness plugin spec** (npm package + `dsh.bundle.patch` + `cordis.patch.yml`). It is **not** a Python process and needs **no pip install**.

---

## Features

- ✅ QQ official open-platform bot API (q.qq.com), official WebSocket long connection — no third-party protocol daemon / throwaway account
- ✅ Two-way C2C private chat: QQ message → dsh runs → result / completion pushed back to QQ
- ✅ Auto-binding: each QQ user maps to a stable dsh session (`qq-official-<openid>`)
- ✅ Over-long messages auto-split into `[1/N]` parts, nothing lost
- ✅ `/qq-test` command for one-click credential connectivity checks

---

## Install

Prerequisites: `dsh` installed (e.g. `~/.dsh/profiles/web`), and dsh web running (default `http://127.0.0.1:10010`).

```bash
# 1) Register this plugin in your dsh profile (link to this repo's source)
cd ~/.dsh/profiles/web
pnpm add link:<absolute path to this repo>/plugins/dsh-qq
```

Then add `"dsh-qq"` to the `dsh.profile.bundles` array in `~/.dsh/profiles/web/package.json`:

```json
{
  "dependencies": { "dsh-qq": "link:<...>/plugins/dsh-qq" },
  "dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app", "dsh-qq"] } }
}
```

**Restart dsh web** for the plugin to load; it then auto-connects the QQ official long connection.

---

## Setup: QQ open platform (q.qq.com)

1. Open the [QQ open platform](https://q.qq.com) (log in with your QQ) and create a bot application.
2. In "Credentials", get your **AppID** (numeric) and **AppSecret**.
3. Enable **bot capability** and **C2C (single-chat) private messaging**; production use requires platform review (the sandbox can be tested first).
4. **Add the bot as your QQ friend** to receive private messages.

### Credentials

The plugin resolves `QQ_OFFICIAL_APP_ID` / `QQ_OFFICIAL_APP_SECRET` through the dsh credentials service / environment (the `appIdEnv` / `appSecretEnv` in `cordis.patch.yml`):

- Store both keys in the dsh web credentials page, **or**
- Export them in the launching environment:

```bash
export QQ_OFFICIAL_APP_ID=1905533507
export QQ_OFFICIAL_APP_SECRET=xxxx
```

- Or set them inline in the plugin row `config` of `cordis.patch.yml`.

---

## Usage

After startup, **private-message the bot in QQ** to drive dsh:

```
You: write a hello.py and run it
dsh: ✅ created and ran it, output: ...
```

dsh replies are pushed back to QQ; task failures / interruptions are also reported.

Verify: run `/qq-test` inside dsh — it returns whether the credentials are valid.

---

## More

- [中文](README.md)

# dsh-im-bridge

English | [中文](README.md)

**dsh-im-bridge** connects [DeepSeek Harness (dsh)](https://github.com/sprog-xhy/dsh-im-bridge) to IM tools like QQ and Feishu: send a message in QQ / Feishu to drive the dsh agent on your machine; when dsh finishes a task or needs your confirmation, results are pushed back to your IM.

This project contains **one Python bridge program** and **two official dsh plugins** (`dsh-qq`, `dsh-feishu`). Works on Windows and Ubuntu.

---

## Quick pick

| What you need | Recommended |
|---|---|
| QQ only | **dsh-qq plugin** (QQ official open-platform bot, C2C private chats) |
| Feishu only | **dsh-feishu plugin** (Feishu app bot, two-way) |
| One process for multiple IMs (Feishu / QQ / Webhook / terminal) | **Python bridge program** |

Both plugins are **official DeepSeek Harness plugins** (Node.js Cordis plugins) installed into a dsh profile — **not pip packages**.

- **dsh-qq** → [中文](plugins/dsh-qq/README.md) ｜ [English docs](plugins/dsh-qq/README.en.md)
- **dsh-feishu** → [中文](plugins/dsh-feishu/README.md) ｜ [English docs](plugins/dsh-feishu/README.en.md)

---

## dsh-qq plugin

Private-message the bot in QQ to drive dsh on your machine:

```
You: write a hello.py and run it
dsh: ✅ created and ran it, output: ...
```

Features:

- QQ official open-platform bot API, official WebSocket long connection — no third-party protocol daemon
- Two-way C2C private chat: QQ message → dsh runs → result / completion pushed back to QQ
- Auto-binding: each QQ user maps to a stable dsh session
- Over-long replies auto-split into `[1/N]` parts, nothing lost

Install & QQ open-platform setup guide → [dsh-qq 中文](plugins/dsh-qq/README.md) ｜ [English docs](plugins/dsh-qq/README.en.md)

---

## dsh-feishu plugin

Private-message the bot in Feishu or @ it in a group to drive dsh on your machine:

```
You: write a hello.py and run it
dsh: ✅ created and ran it, output: ...
```

Features:

- Feishu open platform: app bot (two-way) or custom-bot webhook (send-only notifications)
- Feishu message → dsh runs → result / completion pushed back to Feishu
- Auto-binding: each Feishu chat (chat_id) maps to a stable dsh session
- Over-long replies auto-split into `[1/N]` parts, nothing lost

Install & Feishu open-platform setup guide → [dsh-feishu 中文](plugins/dsh-feishu/README.md) ｜ [English docs](plugins/dsh-feishu/README.en.md)

---

## Python bridge program

A standalone Python process that connects several IM channels (Feishu / QQ / Webhook / terminal) to dsh over the dsh web loopback `/api` protocol — no changes to dsh itself.

```bash
# Install
python -m venv .venv
.\.venv\Scripts\activate        # Windows
source .venv/bin/activate       # Ubuntu
pip install -e .

# Configure (enable feishu / qq / qq_official and fill in credentials)
cp config.example.yaml config.yaml

# Run
dsh-im-bridge --config config.yaml
```

### Session binding

Each IM conversation ↔ one dsh session, persisted in `bridge-state.json`:

- first message auto-creates and binds a session;
- `/attach <sessionId>` binds to an existing session; `/new` creates a new one;
- once bound, dsh replies / task completion / confirmations are pushed back to IM;
- **reply directly to answer** confirmation requests (e.g. `1`, `allow`, `reject`);
- `/cancel` interrupts the current task; `/history [N]` shows recent records.

### Run as a service

- **Windows**: run `scripts\install-startup.ps1` to auto-start dsh web + the bridge at login (no admin needed).
- **Ubuntu**: `sudo ./scripts/install-systemd.sh <absolute path>` installs a systemd service (auto-start on boot, auto-restart on crash).

---

## More docs

- [中文 README](README.md)
- [INTEGRATION.md](INTEGRATION.md) (step-by-step Feishu / QQ integration guide)

/**
 * QQ official open-platform bot client (q.qq.com / bot.q.qq.com) — C2C private
 * chat support, written in pure JS.
 *
 * Protocol details are documented in the dsh-im-bridge research archive
 * (`_research/qq_c2c_protocol_report.md`) and match the official wiki
 * (bot.q.qq.com, api-v2) and the official `tencent-connect/botpy` SDK.
 *
 * This module is the *transport* layer only: token exchange, the WebSocket
 * long connection (HELLO → IDENTIFY / RESUME, heartbeat, C2C dispatch), and
 * the C2C send API. It deliberately knows nothing about dsh — the Cordis
 * plugin (`apply` in index.js) wires events from here into dsh agents.
 *
 * @module dsh-qq/client
 */

/** Token endpoint (same for sandbox and production; verified live). */
const TOKEN_URL = "https://api.bot.qq.com/app/getAppAccessToken";
/** Production API root (wiki's current recommended host). */
const PROD_API_ROOT = "https://api.bot.qq.com";
const SANDBOX_API_ROOT = "https://sandbox.api.bot.qq.com";
const PROD_WS_URL = "wss://api.bot.qq.com/websocket";
const SANDBOX_WS_URL = "wss://sandbox.api.bot.qq.com/websocket";

/** C2C private messages need GROUP_AND_C2C_EVENT. */
export const INTENT_GROUP_AND_C2C = 1 << 25;

/** Gateway op codes. */
export const OP_DISPATCH = 0;
export const OP_HEARTBEAT = 1;
export const OP_IDENTIFY = 2;
export const OP_RESUME = 6;
export const OP_RECONNECT = 7;
export const OP_INVALID_SESSION = 9;
export const OP_HELLO = 10;
export const OP_HEARTBEAT_ACK = 11;

/** Receive-side `message_type` values for C2C_MESSAGE_CREATE. */
export const MT_TEXT = 0;
export const MT_CARD = 3;
export const MT_PARALLEL = 101;
export const MT_CHAT_HISTORY = 102;
export const MT_QUOTE = 103;

/**
 * A tiny errors class with a stable `code` so callers can route failures
 * (credential missing / rejected, provider error, aborted).
 */
export class QqError extends Error {
  constructor(message, code = "QQ_ERROR", options = undefined) {
    super(message, options);
    this.name = "QqError";
    this.code = code;
  }
}

/** True for HTTP status codes that indicate an invalid or rejected credential. */
function isCredentialStatus(status) {
  return status === 401 || status === 403;
}

/**
 * Resolve the gateway WebSocket URL. Prefers the official discovery endpoint
 * (`GET /gateway/bot`) and falls back to the fixed default on any failure.
 * @param token - QQBot access token (used as the discovery Authorization).
 * @param root - API root already selected (prod or sandbox).
 */
async function resolveGatewayUrl(token, root, fetchImpl = fetch) {
  try {
    const resp = await fetchImpl(`${root}/gateway/bot`, {
      headers: { Authorization: `QQBot ${token}`, "X-Union-Appid": undefined },
    });
    const data = await resp.json();
    const url = data?.url ?? data?.data?.url;
    if (typeof url === "string" && url.length > 0) return url;
  } catch {
    // fall through to the fixed default
  }
  return root.startsWith("https://sandbox.") ? SANDBOX_WS_URL : PROD_WS_URL;
}

/** Normalize a received C2C event into plain text / placeholder. */
export function extractC2cText(d) {
  const mtype = d.message_type ?? d.msg_type ?? d.msgType;
  const content = d.content ?? d.content_text ?? "";
  if (mtype === undefined || mtype === null || mtype === MT_TEXT || mtype === "0" || mtype === "") {
    return String(content);
  }
  if (mtype === MT_CARD || mtype === "3") return "[卡片消息]";
  if (mtype === MT_PARALLEL || mtype === "101") return "[并行消息]";
  if (mtype === MT_CHAT_HISTORY || mtype === "102") return "[聊天记录]";
  if (mtype === MT_QUOTE || mtype === "103") return "[引用消息]";
  return `[消息类型${mtype}]`;
}

/**
 * A stateful QQ client: token cache + one WebSocket long connection.
 *
 * `onMessage(openid, text, raw)` is called for each inbound C2C text message.
 * The client keeps the socket alive (heartbeat), deduplicates repeated pushes
 * of the same `msg_id`, and reconnects with RESUME after short drops.
 */
export class QqClient {
  /**
   * @param opts - `{ appId, appSecret, sandbox, fetchImpl?, wsImpl? }`.
   *   `wsImpl` defaults to the global `WebSocket` (Node ≥ 22 / undici);
   *   inject a fake for tests.
   */
  constructor(opts = {}) {
    this.appId = String(opts.appId ?? "");
    this.appSecret = String(opts.appSecret ?? "");
    this.sandbox = Boolean(opts.sandbox);
    this.fetchImpl = opts.fetchImpl ?? fetch;
    this.wsImpl = opts.wsImpl ?? (typeof WebSocket !== "undefined" ? WebSocket : undefined);
    this.onMessage = opts.onMessage ?? (() => {});
    this.onState = opts.onState ?? (() => {});
    this.log = opts.log ?? ((...args) => console.log("[dsh-qq]", ...args));

    this._token = undefined;
    this._tokenExpiresAt = 0;
    this._sessionId = undefined;
    this._lastSeq = 0;
    this._recentMsgIds = new Set();
    this._nextMsgSeq = 0;
    this._stopped = false;
    this._heartbeatTimer = undefined;
    this._socket = undefined;
  }

  apiRoot() {
    return this.sandbox ? SANDBOX_API_ROOT : PROD_API_ROOT;
  }

  /**
   * Return a cached, still-valid access token (fetch + cache otherwise).
   * @returns the token string.
   * @throws {QqError} on missing credentials or a failed exchange.
   */
  async accessToken() {
    const now = Date.now() / 1000;
    if (this._token && this._tokenExpiresAt > now + 60) return this._token;
    if (!this.appId || !this.appSecret) {
      throw new QqError(
        "dsh-qq: appId/appSecret 未配置（在 dsh 凭据里存 QQ_OFFICIAL_APP_ID / QQ_OFFICIAL_APP_SECRET）",
        "QQ_CREDENTIAL_MISSING",
      );
    }
    let resp;
    try {
      resp = await this.fetchImpl(TOKEN_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ appId: this.appId, clientSecret: this.appSecret }),
      });
    } catch (error) {
      throw new QqError(`QQ token request failed: ${String(error)}`, "QQ_PROVIDER_ERROR", { cause: error });
    }
    const data = await resp.json();
    const token = data?.access_token ?? data?.data?.access_token;
    if (typeof token !== "string" || token.length === 0) {
      throw new QqError(`QQ access_token 获取失败: ${JSON.stringify(data)}`, "QQ_PROVIDER_ERROR");
    }
    this._token = token;
    const expiresIn = Number(data?.expires_in ?? data?.data?.expires_in ?? 7200);
    this._tokenExpiresAt = now + expiresIn - 60;
    return token;
  }

  /**
   * Send a C2C (private) text message to a user.
   * @param openid - the user's openid (from C2C_MESSAGE_CREATE.author.user_openid).
   * @param text - message body.
   * @param msgId - the received message id for a passive reply (optional).
   */
  async sendC2c(openid, text, msgId = undefined) {
    const token = await this.accessToken();
    this._nextMsgSeq += 1;
    const payload = {
      content: text,
      msg_type: 0,
      msg_seq: this._nextMsgSeq,
    };
    if (msgId) payload.msg_id = msgId; // passive-reply window
    let resp;
    try {
      resp = await this.fetchImpl(`${this.apiRoot()}/v2/users/${openid}/messages`, {
        method: "POST",
        headers: {
          Authorization: `QQBot ${token}`,
          "X-Union-Appid": this.appId,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });
    } catch (error) {
      throw new QqError(`QQ send request failed: ${String(error)}`, "QQ_PROVIDER_ERROR", { cause: error });
    }
    const data = resp.json ? await resp.json().catch(() => ({})) : {};
    const errCode = data?.err_code ?? 0;
    if (resp.status >= 300 || errCode) {
      throw new QqError(`QQ send C2C failed (HTTP ${resp.status}): ${JSON.stringify(data)}`, "QQ_PROVIDER_ERROR");
    }
    return data;
  }

  /**
   * Open the long connection and keep it alive until {@link stop} is called.
   * Reconnects with backoff; uses RESUME (op 6) on short drops.
   */
  async connect() {
    if (!this.wsImpl) throw new QqError("dsh-qq: no WebSocket implementation available", "QQ_PROVIDER_ERROR");
    this._stopped = false;
    let backoff = 1000;
    while (!this._stopped) {
      try {
        const token = await this.accessToken();
        const url = await resolveGatewayUrl(token, this.apiRoot(), this.fetchImpl);
        this.log(`connecting ${url} (${this.sandbox ? "sandbox" : "prod"})`);
        const ws = new this.wsImpl(url);
        this._socket = ws;
        await this._runSocket(ws, token);
        backoff = 1000;
      } catch (error) {
        if (this._stopped) break;
        this.log(`connection error: ${String(error)}; retrying in ${backoff}ms`);
        await sleep(backoff);
        backoff = Math.min(backoff * 2, 30000);
      }
    }
  }

  async _runSocket(ws, token) {
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", () => resolve());
      ws.addEventListener("error", (ev) => reject(new QqError(`QQ ws error: ${String(ev?.message ?? ev)}`, "QQ_PROVIDER_ERROR")));
    });
    const handshake = await this._handshake(ws, token);
    this._heartbeatTimer = setInterval(() => {
      try {
        ws.send(JSON.stringify({ op: OP_HEARTBEAT, d: this._lastSeq }));
      } catch {
        /* socket gone; loop will reconnect */
      }
    }, Math.max(handshake.heartbeatIntervalMs, 1000));
    try {
      // Drive frames from a message-event queue (a real WebSocket is not an
      // async iterable, so we bridge events → a promise queue).
      let resolveMessage;
      const pending = [];
      const onMessage = (ev) => {
        if (resolveMessage) {
          const r = resolveMessage;
          resolveMessage = undefined;
          r(ev.data);
        } else {
          pending.push(ev.data);
        }
      };
      const onClose = () => {
        if (resolveMessage) {
          const r = resolveMessage;
          resolveMessage = undefined;
          r(undefined);
        }
      };
      ws.addEventListener("message", onMessage);
      ws.addEventListener("close", onClose);
      try {
        while (true) {
          const raw = pending.length > 0 ? pending.shift() : await new Promise((r) => {
            resolveMessage = r;
          });
          if (raw === undefined) break; // socket closed
          let frame;
          try {
            frame = JSON.parse(String(raw));
          } catch {
            continue;
          }
          await this._handleFrame(ws, frame);
        }
      } finally {
        ws.removeEventListener("message", onMessage);
        ws.removeEventListener("close", onClose);
      }
    } finally {
      clearInterval(this._heartbeatTimer);
      this._heartbeatTimer = undefined;
    }
  }

  async _handshake(ws, token) {
    const first = JSON.parse(await nextMessage(ws));
    if (first.op !== OP_HELLO) throw new QqError(`expected HELLO, got op ${first.op}`, "QQ_PROTOCOL_ERROR");
    const heartbeatIntervalMs = Number(first.d?.heartbeat_interval ?? 41250);
    // resume if we hold a session id
    if (this._sessionId) {
      ws.send(JSON.stringify({
        op: OP_RESUME,
        d: { token: `QQBot ${token}`, session_id: this._sessionId, seq: this._lastSeq },
      }));
      const resp = JSON.parse(await nextMessage(ws));
      if (resp.op === OP_DISPATCH && resp.t === "RESUMED") {
        return { heartbeatIntervalMs };
      }
      if (resp.op === OP_INVALID_SESSION) {
        this._sessionId = undefined;
        this._lastSeq = 0;
      }
    }
    ws.send(JSON.stringify({
      op: OP_IDENTIFY,
      d: {
        token: `QQBot ${token}`,
        intents: INTENT_GROUP_AND_C2C,
        shard: [0, 1],
        properties: { $os: "linux", $browser: "dsh-qq", $device: "dsh-qq" },
      },
    }));
    const ready = JSON.parse(await nextMessage(ws));
    if (ready.op !== OP_DISPATCH || ready.t !== "READY") {
      throw new QqError(`expected READY, got ${JSON.stringify(ready)}`, "QQ_PROTOCOL_ERROR");
    }
    this._sessionId = ready.d?.session_id;
    return { heartbeatIntervalMs };
  }

  async _handleFrame(ws, frame) {
    const op = frame.op;
    if (op === OP_DISPATCH) {
      this._lastSeq = Number(frame.s ?? this._lastSeq ?? 0);
      const t = frame.t;
      if (t === "READY") {
        this._sessionId = frame.d?.session_id;
      } else if (t === "RESUMED") {
        /* nothing */
      } else if (t === "C2C_MESSAGE_CREATE") {
        await this._onC2c(frame.d ?? {});
      } else {
        this.log(`dispatch ${t} ignored`);
      }
    } else if (op === OP_HEARTBEAT_ACK || op === OP_HELLO) {
      /* nothing */
    } else if (op === OP_RECONNECT) {
      this.log("server requested reconnect");
      ws.close();
    } else if (op === OP_INVALID_SESSION) {
      this._sessionId = undefined;
      this._lastSeq = 0;
    }
  }

  async _onC2c(d) {
    const author = d.author ?? {};
    const openid = author.user_openid ?? d.user_openid ?? d.openid ?? "";
    if (!openid) return;
    const msgId = String(d.id ?? d.msg_id ?? "");
    if (msgId && this._recentMsgIds.has(msgId)) return; // dedupe repeated pushes
    if (msgId) {
      this._recentMsgIds.add(msgId);
      if (this._recentMsgIds.size > 500) {
        // keep the dedupe set bounded
        const first = this._recentMsgIds.values().next().value;
        this._recentMsgIds.delete(first);
      }
    }
    const text = extractC2cText(d);
    if (!text.trim()) return;
    await this.onMessage(openid, text.trim(), d);
  }

  /** Tear down the connection and timers. */
  async stop() {
    this._stopped = true;
    if (this._heartbeatTimer) clearInterval(this._heartbeatTimer);
    this._heartbeatTimer = undefined;
    if (this._socket) {
      try {
        this._socket.close();
      } catch {
        /* ignore */
      }
      this._socket = undefined;
    }
  }
}

function nextMessage(ws) {
  return new Promise((resolve, reject) => {
    const onMessage = (ev) => {
      cleanup();
      resolve(ev.data);
    };
    const onError = (ev) => {
      cleanup();
      reject(new QqError(`QQ ws error: ${String(ev?.message ?? ev)}`, "QQ_PROVIDER_ERROR"));
    };
    const onClose = () => {
      cleanup();
      reject(new QqError("QQ ws closed during handshake", "QQ_PROVIDER_ERROR"));
    };
    function cleanup() {
      ws.removeEventListener("message", onMessage);
      ws.removeEventListener("error", onError);
      ws.removeEventListener("close", onClose);
    }
    ws.addEventListener("message", onMessage);
    ws.addEventListener("error", onError);
    ws.addEventListener("close", onClose);
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export {
  PROD_API_ROOT,
  PROD_WS_URL,
  SANDBOX_API_ROOT,
  SANDBOX_WS_URL,
  TOKEN_URL,
  resolveGatewayUrl,
};

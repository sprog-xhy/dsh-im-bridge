/**
 * Feishu (飞书 / Lark) open-platform bot client — text messages, in pure JS.
 *
 * Supports two modes, mirroring the dsh-im-bridge Python channel:
 *
 * * **Custom-bot webhook** (`webhookUrl`): send-only. Posts text to a
 *   `https://open.feishu.cn/open-apis/bot/v2/hook/<token>` URL.
 * * **App bot** (`appId` + `appSecret`): full two-way. Sends via
 *   `im/v1/messages` with a cached tenant access token, and receives message
 *   events over the Feishu event **long connection** (WebSocket, pbbp2.Frame
 *   protobuf envelope).
 *
 * This module is the *transport* layer only; the Cordis plugin (`apply` in
 * index.js) wires events from here into dsh agents.
 *
 * @module dsh-feishu/client
 */

import { createHash, createDecipheriv } from "node:crypto";

const FEISHU_BASE = "https://open.feishu.cn";

/** @param {string} message @param {string} [code] */
export class FeishuError extends Error {
  constructor(message, code = "FEISHU_ERROR", options = undefined) {
    super(message, options);
    this.name = "FeishuError";
    this.code = code;
  }
}

// ── pbbp2.Frame protobuf envelope (implemented directly, no protobuf dep) ───
function pbVarint(n) {
  n = BigInt.asUintN(64, BigInt(n));
  const out = [];
  while (n >= 0x80n) {
    out.push(Number((n & 0x7fn) | 0x80n));
    n >>= 7n;
  }
  out.push(Number(n));
  return Buffer.from(out);
}

function pbLen(data) {
  return Buffer.concat([pbVarint(data.length), data]);
}

function encodeHeader(key, value) {
  const keyBuf = Buffer.from(key, "utf8");
  const valBuf = Buffer.from(value, "utf8");
  return Buffer.concat([
    pbVarint((1 << 3) | 2), pbLen(keyBuf),
    pbVarint((2 << 3) | 2), pbLen(valBuf),
  ]);
}

/**
 * Encode one pbbp2.Frame.
 * @param {{seq?:number, log?:number, service?:number, method?:number, headers?:[string,string][], payload?:Buffer}} f
 */
export function encodeFrame(f = {}) {
  const parts = [];
  if (f.seq) parts.push(pbVarint((1 << 3) | 0), pbVarint(f.seq));
  if (f.log) parts.push(pbVarint((2 << 3) | 0), pbVarint(f.log));
  parts.push(pbVarint((3 << 3) | 0), pbVarint(f.service ?? 0));
  parts.push(pbVarint((4 << 3) | 0), pbVarint(f.method ?? 0));
  for (const [k, v] of f.headers ?? []) {
    parts.push(pbVarint((5 << 3) | 2), pbLen(encodeHeader(k, v)));
  }
  if (f.payload && f.payload.length > 0) {
    parts.push(pbVarint((8 << 3) | 2), pbLen(f.payload));
  }
  return Buffer.concat(parts);
}

function readVarint(data, state) {
  let val = 0n;
  let shift = 0n;
  while (state.i < data.length) {
    const b = data[state.i++];
    val |= BigInt(b & 0x7f) << shift;
    if ((b & 0x80) === 0) return val;
    shift += 7n;
  }
  throw new FeishuError("truncated varint", "FEISHU_PROTOCOL_ERROR");
}

/**
 * Decode one pbbp2.Frame.
 * @param {Buffer} data
 * @returns {{seq:number, log:number, service:number, method:number, headers:[string,string][], payload:Buffer}}
 */
export function decodeFrame(data) {
  const result = { seq: 0, log: 0, service: 0, method: 0, headers: [], payload: Buffer.alloc(0) };
  const state = { i: 0 };
  while (state.i < data.length) {
    const key = Number(readVarint(data, state));
    const field = key >> 3;
    const wire = key & 7;
    if (wire === 0) {
      const val = Number(readVarint(data, state));
      if (field === 1) result.seq = val;
      else if (field === 2) result.log = val;
      else if (field === 3) result.service = val;
      else if (field === 4) result.method = val;
    } else if (wire === 2) {
      const len = Number(readVarint(data, state));
      const chunk = data.subarray(state.i, state.i + len);
      state.i += len;
      if (field === 5) {
        // header submessage: key=1(value) value=2(value)
        const h = { i: 0 };
        let hk = "";
        let hv = "";
        while (h.i < chunk.length) {
          const k2 = Number(readVarint(chunk, h));
          const f2 = k2 >> 3;
          const l2 = Number(readVarint(chunk, h));
          const s2 = chunk.subarray(h.i, h.i + l2).toString("utf8");
          h.i += l2;
          if (f2 === 1) hk = s2;
          else if (f2 === 2) hv = s2;
        }
        result.headers.push([hk, hv]);
      } else if (field === 8) {
        result.payload = chunk;
      }
    } else {
      throw new FeishuError(`unsupported protobuf wire type ${wire}`, "FEISHU_PROTOCOL_ERROR");
    }
  }
  return result;
}

/**
 * Decrypt a Feishu-encrypted event payload (AES-256-CBC, PKCS7, IV=payload[:16]).
 * @param {string} encryptKey
 * @param {string} payload - raw string whose first 16 chars are the IV.
 */
export function aesDecrypt(encryptKey, payload) {
  const key = createHash("sha256").update(encryptKey, "utf8").digest();
  const iv = Buffer.from(payload.slice(0, 16), "utf8");
  const ciphertext = Buffer.from(payload.slice(16), "base64");
  const decipher = createDecipheriv("aes-256-cbc", key, iv);
  const decrypted = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  return decrypted.toString("utf8");
}

/**
 * Stateful Feishu client: tenant token cache + WebSocket long connection.
 *
 * `onMessage(chatId, text, raw)` is called for each inbound text message.
 */
export class FeishuClient {
  /**
   * @param {object} opts - `{ appId, appSecret, webhookUrl?, encryptKey?,
   *   receiveChatTypes?, baseUrl?, fetchImpl?, wsImpl?, log? }`.
   */
  constructor(opts = {}) {
    this.appId = String(opts.appId ?? "");
    this.appSecret = String(opts.appSecret ?? "");
    this.webhookUrl = opts.webhookUrl;
    this.encryptKey = opts.encryptKey;
    this.receiveChatTypes = opts.receiveChatTypes ?? ["p2p", "group"];
    this.baseUrl = String(opts.baseUrl ?? FEISHU_BASE).replace(/\/+$/, "");
    this.fetchImpl = opts.fetchImpl ?? fetch;
    this.wsImpl = opts.wsImpl ?? (typeof WebSocket !== "undefined" ? WebSocket : undefined);
    this.onMessage = opts.onMessage ?? (() => {});
    this.onState = opts.onState ?? (() => {});
    this.log = opts.log ?? ((...args) => console.log("[dsh-feishu]", ...args));

    this.tokenUrl = `${this.baseUrl}/open-apis/auth/v3/tenant_access_token/internal`;
    this.sendUrl = `${this.baseUrl}/open-apis/im/v1/messages?receive_id_type=chat_id`;
    this.wsEndpointUrl = `${this.baseUrl}/callback/ws/endpoint`;

    this._token = undefined;
    this._tokenExpiresAt = 0;
    this._socket = undefined;
    this._pingTimer = undefined;
    this._stopped = false;
  }

  async tenantToken() {
    const now = Date.now() / 1000;
    if (this._token && this._tokenExpiresAt > now + 60) return this._token;
    if (!this.appId || !this.appSecret) {
      throw new FeishuError(
        "dsh-feishu: appId/appSecret 未配置（在 dsh 凭据里存 FEISHU_APP_ID / FEISHU_APP_SECRET）",
        "FEISHU_CREDENTIAL_MISSING",
      );
    }
    let resp;
    try {
      resp = await this.fetchImpl(this.tokenUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_id: this.appId, app_secret: this.appSecret }),
      });
    } catch (error) {
      throw new FeishuError(`Feishu token request failed: ${String(error)}`, "FEISHU_PROVIDER_ERROR", { cause: error });
    }
    const data = await resp.json();
    if (data.code !== 0) {
      throw new FeishuError(`Feishu tenant token error: ${JSON.stringify(data)}`, "FEISHU_PROVIDER_ERROR");
    }
    this._token = data.tenant_access_token;
    this._tokenExpiresAt = now + Number(data.expire ?? 7200) - 60;
    return this._token;
  }

  /**
   * Send one text message.
   * @param {string} conversationId - chat_id (app bot) or ignored (webhook).
   * @param {string} text
   */
  async send(conversationId, text) {
    if (this.webhookUrl) {
      await this._post(this.webhookUrl, { msg_type: "text", content: { text } });
    } else {
      const token = await this.tenantToken();
      const resp = await this._post(
        this.sendUrl,
        {
          receive_id: conversationId,
          msg_type: "text",
          content: JSON.stringify({ text }),
        },
        { Authorization: `Bearer ${token}` },
      );
      if (resp.code !== 0) {
        throw new FeishuError(`Feishu im send error: ${JSON.stringify(resp)}`, "FEISHU_PROVIDER_ERROR");
      }
    }
  }

  async _post(url, body, extraHeaders = {}) {
    let resp;
    try {
      resp = await this.fetchImpl(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...extraHeaders },
        body: JSON.stringify(body),
      });
    } catch (error) {
      throw new FeishuError(`Feishu request failed: ${String(error)}`, "FEISHU_PROVIDER_ERROR", { cause: error });
    }
    return resp.json();
  }

  /** Request the long-connection WebSocket endpoint URL. */
  async requestWsEndpoint() {
    if (!this.appId || !this.appSecret) {
      throw new FeishuError("dsh-feishu: appId/appSecret 未配置", "FEISHU_CREDENTIAL_MISSING");
    }
    let resp;
    try {
      resp = await this.fetchImpl(this.wsEndpointUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", locale: "zh", "User-Agent": "dsh-feishu" },
        body: JSON.stringify({ AppID: this.appId, AppSecret: this.appSecret }),
      });
    } catch (error) {
      throw new FeishuError(`Feishu ws endpoint request failed: ${String(error)}`, "FEISHU_PROVIDER_ERROR", { cause: error });
    }
    const data = await resp.json();
    if (data.code !== 0) throw new FeishuError(`Feishu ws endpoint error: ${JSON.stringify(data)}`, "FEISHU_PROVIDER_ERROR");
    const url = data.data?.URL;
    if (!url) throw new FeishuError("Feishu ws endpoint returned no URL", "FEISHU_PROVIDER_ERROR");
    return url;
  }

  /** Open the long connection and keep it alive until {@link stop}. */
  async connect() {
    if (!this.wsImpl) throw new FeishuError("dsh-feishu: no WebSocket implementation available", "FEISHU_PROVIDER_ERROR");
    this._stopped = false;
    let backoff = 1000;
    while (!this._stopped) {
      try {
        const endpoint = await this.requestWsEndpoint();
        this.log(`connecting ${endpoint}`);
        const ws = new this.wsImpl(endpoint);
        this._socket = ws;
        await this._runSocket(ws);
        backoff = 1000;
      } catch (error) {
        if (this._stopped) break;
        this.log(`connection error: ${String(error)}; retrying in ${backoff}ms`);
        await sleep(backoff);
        backoff = Math.min(backoff * 2, 30000);
      }
    }
  }

  async _runSocket(ws) {
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", () => resolve());
      ws.addEventListener("error", (ev) => reject(new FeishuError(`Feishu ws error: ${String(ev?.message ?? ev)}`, "FEISHU_PROVIDER_ERROR")));
    });
    // PING control frame every ~120s.
    this._pingTimer = setInterval(() => {
      try {
        ws.send(encodeFrame({ method: 0, headers: [["type", "ping"]] }));
      } catch {
        /* socket gone */
      }
    }, 120000);
    try {
      let resolveMessage;
      const pending = [];
      const onMessage = (ev) => {
        const data = toBuffer(ev.data);
        if (resolveMessage) {
          const r = resolveMessage;
          resolveMessage = undefined;
          r(data);
        } else {
          pending.push(data);
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
          if (raw === undefined) break;
          await this._handleFrame(ws, raw);
        }
      } finally {
        ws.removeEventListener("message", onMessage);
        ws.removeEventListener("close", onClose);
      }
    } finally {
      clearInterval(this._pingTimer);
      this._pingTimer = undefined;
    }
  }

  async _handleFrame(ws, raw) {
    let frame;
    try {
      frame = decodeFrame(raw);
    } catch (error) {
      this.log(`bad frame: ${String(error)}`);
      return;
    }
    const method = frame.method; // 0=CONTROL 1=DATA
    const headers = new Map(frame.headers);
    const mtype = headers.get("type");
    if (method === 0) return; // CONTROL (ping/pong) — nothing to reply
    if (method !== 1) return;
    if (mtype !== "event") return;
    const payload = frame.payload;
    if (!payload || payload.length === 0) return;
    let event;
    try {
      event = JSON.parse(payload.toString("utf8"));
    } catch (error) {
      this.log(`event payload not JSON: ${String(error)}`);
      return;
    }
    await this._handleEvent(event);
    // ACK: send the same frame back with payload {"code":200}
    const ack = encodeFrame({
      seq: frame.seq, log: frame.log, service: frame.service,
      method, headers: frame.headers,
      payload: Buffer.from(JSON.stringify({ code: 200 }), "utf8"),
    });
    try {
      ws.send(ack);
    } catch {
      /* ignore */
    }
  }

  async _handleEvent(event) {
    // optional encryption
    let ev = event;
    if (ev.encrypt && this.encryptKey) {
      ev = JSON.parse(aesDecrypt(this.encryptKey, ev.encrypt));
    } else if (ev.encrypt) {
      this.log("encrypted event but no encryptKey configured; ignoring");
      return;
    }
    const header = ev.header ?? {};
    if (header.event_type !== "im.message.receive_v1") return;
    const message = ev.event?.message ?? {};
    const sender = ev.event?.sender ?? {};
    const chat = message.chat ?? {};
    const chatType = chat.chat_type ?? message.chat_type;
    if (!this.receiveChatTypes.includes(chatType)) return;
    const chatId = chat.chat_id ?? message.chat_id ?? chat.id ?? "";
    const content = message.content ?? "{}";
    let contentObj;
    try {
      contentObj = JSON.parse(content);
    } catch {
      contentObj = { text: String(content) };
    }
    let text = contentObj.text ?? contentObj.content ?? "";
    text = text.replace(/@_user_1/g, ""); // strip feishu <at> markup
    if (!text.trim()) return;
    const senderId = sender.sender_id?.open_id ?? sender.id;
    await this.onMessage(chatId, text.trim(), { chatType, senderId, raw: ev });
  }

  async stop() {
    this._stopped = true;
    if (this._pingTimer) clearInterval(this._pingTimer);
    this._pingTimer = undefined;
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

/** Normalize a WS message payload to a Buffer. */
function toBuffer(data) {
  if (Buffer.isBuffer(data)) return data;
  if (data instanceof ArrayBuffer) return Buffer.from(data);
  if (ArrayBuffer.isView(data)) return Buffer.from(data.buffer, data.byteOffset, data.byteLength);
  if (typeof data === "string") return Buffer.from(data, "utf8");
  // Blob
  return data.arrayBuffer ? Buffer.from(data.arrayBuffer()) : Buffer.from(String(data));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export { FEISHU_BASE };

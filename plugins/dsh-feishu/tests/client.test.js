/**
 * Tests for the Feishu client (`lib/client.js`).
 *
 * Runs standalone with Node's built-in test runner; no dsh packages needed.
 * Fakes `fetch` and `WebSocket`; no live Feishu app required.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { createCipheriv, createHash } from "node:crypto";

import {
  FEISHU_BASE,
  FeishuClient,
  FeishuError,
  aesDecrypt,
  decodeFrame,
  encodeFrame,
} from "../lib/client.js";

// ── protobuf frame ───────────────────────────────────────────────────────────
test("frame roundtrip", () => {
  const payload = Buffer.from('{"schema":"2.0","header":{},"event":{}}');
  const raw = encodeFrame({
    seq: 1, log: 2, service: 3, method: 1,
    headers: [["type", "event"], ["trace_id", "t"]],
    payload,
  });
  const frame = decodeFrame(raw);
  assert.equal(frame.seq, 1);
  assert.equal(frame.log, 2);
  assert.equal(frame.service, 3);
  assert.equal(frame.method, 1);
  assert.deepEqual(frame.headers, [["type", "event"], ["trace_id", "t"]]);
  assert.deepEqual(frame.payload, payload);
});

test("control frame roundtrip", () => {
  const raw = encodeFrame({ method: 0, headers: [["type", "ping"]] });
  const frame = decodeFrame(raw);
  assert.equal(frame.method, 0);
  assert.deepEqual(frame.headers, [["type", "ping"]]);
});

// ── AES decrypt ─────────────────────────────────────────────────────────────
test("aesDecrypt roundtrip", () => {
  // build the Feishu wire shape: IV as first 16 chars + base64(ciphertext)
  const iv = Buffer.from("0123456789abcdef");
  const key = createHash("sha256").update("k", "utf8").digest();
  const cipher = createCipheriv("aes-256-cbc", key, iv);
  const ciphertext = Buffer.concat([
    cipher.update('{"type":"Event"}', "utf8"),
    cipher.final(),
  ]);
  const wire = iv.toString("latin1") + ciphertext.toString("base64");
  assert.equal(JSON.parse(aesDecrypt("k", wire)).type, "Event");
});

// ── token ───────────────────────────────────────────────────────────────────
test("tenantToken caches", async () => {
  let calls = 0;
  const client = new FeishuClient({
    appId: "cli_x",
    appSecret: "secret",
    fetchImpl: async () => {
      calls += 1;
      return { json: async () => ({ code: 0, tenant_access_token: "tok", expire: 7200 }) };
    },
  });
  assert.equal(await client.tenantToken(), "tok");
  assert.equal(await client.tenantToken(), "tok"); // cached
  assert.equal(calls, 1);
});

test("tenantToken error", async () => {
  const client = new FeishuClient({
    appId: "x",
    appSecret: "y",
    fetchImpl: async () => ({ json: async () => ({ code: 10003, msg: "bad" }) }),
  });
  await assert.rejects(() => client.tenantToken(), FeishuError);
});

// ── send ────────────────────────────────────────────────────────────────────
test("send webhook posts text", async () => {
  const calls = [];
  const client = new FeishuClient({
    webhookUrl: "https://example/hook",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return { json: async () => ({ code: 0 }) };
    },
  });
  await client.send("ignored", "hello");
  assert.equal(calls.length, 1);
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.msg_type, "text");
  assert.equal(body.content.text, "hello");
});

test("send im uses tenant token", async () => {
  const calls = [];
  const client = new FeishuClient({
    appId: "cli_x",
    appSecret: "secret",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      if (url.includes("/auth/v3/tenant_access_token")) {
        return { json: async () => ({ code: 0, tenant_access_token: "tok", expire: 7200 }) };
      }
      return { json: async () => ({ code: 0 }) };
    },
  });
  await client.send("oc_chat", "hi");
  const send = calls.find((c) => c.url.includes("/im/v1/messages"));
  assert.ok(send, "expected an im/v1/messages call");
  assert.equal(send.init.headers.Authorization, "Bearer tok");
  const body = JSON.parse(send.init.body);
  assert.equal(body.receive_id, "oc_chat");
  assert.equal(JSON.parse(body.content).text, "hi");
});

// ── long connection event handling ──────────────────────────────────────────
test("handleEvent routes text to onMessage", async () => {
  const inbound = [];
  const client = new FeishuClient({
    receiveChatTypes: ["p2p"],
    onMessage: (chatId, text, meta) => inbound.push({ chatId, text, meta }),
    log: () => {},
  });
  await client._handleEvent({
    header: { event_type: "im.message.receive_v1" },
    event: {
      message: { chat: { chat_id: "oc_x", chat_type: "p2p" }, content: JSON.stringify({ text: "你好 @_user_1" }) },
      sender: { sender_id: { open_id: "ou_y" } },
    },
  });
  assert.equal(inbound.length, 1);
  assert.equal(inbound[0].chatId, "oc_x");
  assert.equal(inbound[0].text, "你好"); // <at> stripped
  assert.equal(inbound[0].meta.senderId, "ou_y");
});

test("handleEvent ignores other event types / chats", async () => {
  const inbound = [];
  const client = new FeishuClient({
    receiveChatTypes: ["p2p"],
    onMessage: (chatId, text) => inbound.push({ chatId, text }),
    log: () => {},
  });
  await client._handleEvent({ header: { event_type: "im.message.deleted" }, event: {} });
  await client._handleEvent({
    header: { event_type: "im.message.receive_v1" },
    event: { message: { chat: { chat_type: "group", chat_id: "g1" }, content: JSON.stringify({ text: "x" }) } },
  });
  assert.equal(inbound.length, 0);
});

test("encrypted event is decrypted when encryptKey set", async () => {
  const iv = Buffer.from("abcdefghijklmnop");
  const key = createHash("sha256").update("ek", "utf8").digest();
  const cipher = createCipheriv("aes-256-cbc", key, iv);
  const plaintext = JSON.stringify({
    header: { event_type: "im.message.receive_v1" },
    event: { message: { chat: { chat_id: "oc_e", chat_type: "p2p" }, content: JSON.stringify({ text: "enc" }) } },
  });
  const ciphertext = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const wire = iv.toString("latin1") + ciphertext.toString("base64");

  const inbound = [];
  const client = new FeishuClient({
    encryptKey: "ek",
    receiveChatTypes: ["p2p"],
    onMessage: (chatId, text) => inbound.push({ chatId, text }),
    log: () => {},
  });
  await client._handleEvent({ encrypt: wire });
  assert.equal(inbound.length, 1);
  assert.equal(inbound[0].text, "enc");
});

test("full socket delivers event and ACKs", async () => {
  const listeners = new Map();
  const sent = [];
  const ws = {
    sent,
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    removeEventListener(type, fn) {
      const arr = listeners.get(type) ?? [];
      const i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    },
    send(data) {
      sent.push(Buffer.isBuffer(data) ? data : Buffer.from(String(data)));
    },
    close() {
      (listeners.get("close") ?? []).forEach((fn) => fn({}));
    },
    _push(obj) {
      (listeners.get("message") ?? []).forEach((fn) => fn({ data: obj }));
    },
  };
  const inbound = [];
  const client = new FeishuClient({
    appId: "cli_x",
    appSecret: "secret",
    receiveChatTypes: ["p2p"],
    onMessage: (chatId, text) => inbound.push({ chatId, text }),
    log: () => {},
    // fake endpoint request: /callback/ws/endpoint -> a fake URL
    fetchImpl: async (url) => {
      if (url.includes("/callback/ws/endpoint")) {
        return { json: async () => ({ code: 0, data: { URL: "wss://fake/ws" } }) };
      }
      return { json: async () => ({ code: 0 }) };
    },
    wsImpl: class {
      constructor() {
        return ws;
      }
    },
  });
  // Deterministically emit open once the client has attached its listener.
  async function emitOpenWhenReady() {
    for (let i = 0; i < 200; i++) {
      if ((listeners.get("open") ?? []).length > 0) {
        (listeners.get("open") ?? []).forEach((fn) => fn({}));
        return;
      }
      await new Promise((r) => setTimeout(r, 1));
    }
    throw new Error("open listener never attached");
  }

  const connPromise = client.connect();
  await emitOpenWhenReady();
  const payload = {
    header: { event_type: "im.message.receive_v1" },
    event: { message: { chat: { chat_id: "oc_ws", chat_type: "p2p" }, content: JSON.stringify({ text: "from ws" }) } },
  };
  ws._push(encodeFrame({ seq: 7, log: 8, service: 3, method: 1, headers: [["type", "event"]], payload: Buffer.from(JSON.stringify(payload)) }));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(inbound.length, 1);
  assert.equal(inbound[0].text, "from ws");
  // an ACK frame must be sent back
  const ack = sent.find((b) => b.includes(Buffer.from("{\"code\":200}")));
  assert.ok(ack, "expected an ACK frame");
  client.stop();
  await connPromise.catch(() => {});
});

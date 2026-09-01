/**
 * Tests for the QQ official protocol client (`lib/client.js`).
 *
 * Runs standalone with Node's built-in test runner; no dsh packages needed.
 * Fakes `fetch` (token / send endpoints) and `WebSocket` (gateway frames) so
 * no live QQ account is required.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  INTENT_GROUP_AND_C2C,
  QqClient,
  QqError,
  extractC2cText,
  resolveGatewayUrl,
} from "../lib/client.js";

// ── pure helpers ─────────────────────────────────────────────────────────────
test("intent for C2C is GROUP_AND_C2C (1<<25 = 33554432)", () => {
  assert.equal(INTENT_GROUP_AND_C2C, 33554432);
});

test("extractC2cText maps message types", () => {
  assert.equal(extractC2cText({ message_type: 0, content: "hello 你好" }), "hello 你好");
  assert.equal(extractC2cText({ content: "no type" }), "no type");
  assert.equal(extractC2cText({ message_type: 3 }), "[卡片消息]");
  assert.equal(extractC2cText({ message_type: 101 }), "[并行消息]");
  assert.equal(extractC2cText({ message_type: 102 }), "[聊天记录]");
  assert.equal(extractC2cText({ message_type: 103 }), "[引用消息]");
  assert.equal(extractC2cText({ message_type: 99 }), "[消息类型99]");
});

test("resolveGatewayUrl falls back to fixed default on failure", async () => {
  const url = await resolveGatewayUrl("tok", "https://api.bot.qq.com", async () => {
    throw new Error("network down");
  });
  assert.equal(url, "wss://api.bot.qq.com/websocket");
});

test("resolveGatewayUrl uses discovery when available", async () => {
  const url = await resolveGatewayUrl("tok", "https://api.bot.qq.com", async () => ({
    json: async () => ({ url: "wss://gateway.example/websocket" }),
  }));
  assert.equal(url, "wss://gateway.example/websocket");
});

// ── token ────────────────────────────────────────────────────────────────────
test("accessToken caches and refreshes", async () => {
  let calls = 0;
  const client = new QqClient({
    appId: "1234567890",
    appSecret: "secret",
    fetchImpl: async () => {
      calls += 1;
      return { json: async () => ({ access_token: "tok-1", expires_in: "7200" }) };
    },
  });
  assert.equal(await client.accessToken(), "tok-1");
  assert.equal(await client.accessToken(), "tok-1"); // cached
  assert.equal(calls, 1);
});

test("accessToken error surfaces as QqError", async () => {
  const client = new QqClient({
    appId: "x",
    appSecret: "y",
    fetchImpl: async () => ({ json: async () => ({ code: 100007, message: "appid invalid" }) }),
  });
  await assert.rejects(() => client.accessToken(), QqError);
});

test("accessToken requires credentials", async () => {
  const client = new QqClient({ appId: "", appSecret: "" });
  await assert.rejects(() => client.accessToken(), QqError);
});

// ── send ─────────────────────────────────────────────────────────────────────
test("sendC2c posts to the official API with QQBot auth", async () => {
  const calls = [];
  let tokenCalls = 0;
  const client = new QqClient({
    appId: "1234567890",
    appSecret: "secret",
    fetchImpl: async (url, init) => {
      if (url.includes("/app/getAppAccessToken")) {
        tokenCalls += 1;
        return { json: async () => ({ access_token: "tok", expires_in: "7200" }) };
      }
      calls.push({ url, init });
      return { status: 200, json: async () => ({ id: "ok" }) };
    },
  });
  await client.sendC2c("openid_123", "hi back", "ROBOT1.0_msg");
  assert.equal(tokenCalls, 1);
  assert.ok(calls[0].url.endsWith("/v2/users/openid_123/messages"));
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.content, "hi back");
  assert.equal(body.msg_type, 0);
  assert.equal(body.msg_id, "ROBOT1.0_msg");
  assert.equal(calls[0].init.headers.Authorization, "QQBot tok");
});

test("sendC2c raises on err_code", async () => {
  const client = new QqClient({
    appId: "x",
    appSecret: "y",
    fetchImpl: async (url) => {
      if (url.includes("/app/getAppAccessToken")) {
        return { json: async () => ({ access_token: "tok", expires_in: "7200" }) };
      }
      return { status: 400, json: async () => ({ err_code: 40034005, message: "msg_id 过期" }) };
    },
  });
  await assert.rejects(() => client.sendC2c("o", "hi"), QqError);
});

// ── WebSocket handshake / receive ───────────────────────────────────────────
/** Macro-task tick so chained async steps (fetch → ws → open) settle. */
function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function fakeWebSocketFactory() {
  const listeners = new Map();
  const sent = [];
  const ws = {
    sent,
    readyState: 0,
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
      sent.push(String(data));
    },
    close() {
      this.readyState = 3;
      (listeners.get("close") ?? []).forEach((fn) => fn({}));
    },
    _push(obj) {
      (listeners.get("message") ?? []).forEach((fn) => fn({ data: JSON.stringify(obj) }));
    },
    // Auto-open on the next macrotask, after the open listener is attached.
    _autoOpen() {
      setTimeout(() => {
        (listeners.get("open") ?? []).forEach((fn) => fn({}));
      }, 0);
    },
  };
  return { ws, listeners };
}

function makeClient(ws, onMessage) {
  return new QqClient({
    appId: "a",
    appSecret: "b",
    fetchImpl: async (url) => {
      if (url.includes("/gateway/bot")) return { json: async () => ({ url: "wss://fake/ws" }) };
      return { json: async () => ({ access_token: "tok", expires_in: "7200" }) };
    },
    wsImpl: class {
      constructor() {
        return ws;
      }
    },
    onMessage: onMessage ?? (() => {}),
    log: () => {},
  });
}

test("connect performs HELLO->IDENTIFY->READY and receives C2C", async () => {
  const { ws } = fakeWebSocketFactory();
  const inbound = [];
  const client = makeClient(ws, (openid, text) => inbound.push({ openid, text }));
  ws._autoOpen();

  const connPromise = client.connect();
  await tick();
  // server: HELLO
  ws._push({ op: 10, d: { heartbeat_interval: 41250 } });
  await tick();
  // client should send IDENTIFY (op 2)
  const ident = ws.sent.find((raw) => JSON.parse(raw).op === 2);
  assert.ok(ident, "expected an IDENTIFY frame");
  assert.equal(JSON.parse(ident).d.token, "QQBot tok");
  assert.equal(JSON.parse(ident).d.intents & INTENT_GROUP_AND_C2C, INTENT_GROUP_AND_C2C);
  // server: READY
  ws._push({ op: 0, s: 1, t: "READY", d: { session_id: "sess-1" } });
  await tick();
  // server: C2C_MESSAGE_CREATE
  ws._push({
    op: 0,
    s: 2,
    t: "C2C_MESSAGE_CREATE",
    d: { id: "m1", author: { user_openid: "openid_ws" }, content: "hello from ws", message_type: 0 },
  });
  await tick();
  assert.equal(inbound.length, 1);
  assert.equal(inbound[0].openid, "openid_ws");
  assert.equal(inbound[0].text, "hello from ws");
  // duplicate msg_id is deduped
  ws._push({
    op: 0,
    s: 3,
    t: "C2C_MESSAGE_CREATE",
    d: { id: "m1", author: { user_openid: "openid_ws" }, content: "dup", message_type: 0 },
  });
  await tick();
  assert.equal(inbound.length, 1);

  client.stop();
  await connPromise.catch(() => {});
});

test("connect resumes (op 6) when a session_id is held", async () => {
  const { ws } = fakeWebSocketFactory();
  const client = makeClient(ws);
  client._sessionId = "sess-old";
  client._lastSeq = 42;
  ws._autoOpen();

  const connPromise = client.connect();
  await tick();
  ws._push({ op: 10, d: { heartbeat_interval: 41250 } });
  await tick();
  const resume = ws.sent.find((raw) => JSON.parse(raw).op === 6);
  assert.ok(resume, "expected a RESUME frame");
  assert.equal(JSON.parse(resume).d.session_id, "sess-old");
  assert.equal(JSON.parse(resume).d.seq, 42);
  ws._push({ op: 0, s: 43, t: "RESUMED" });
  await tick();
  client.stop();
  await connPromise.catch(() => {});
});

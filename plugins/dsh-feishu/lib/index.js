/**
 * @deepseek-ai/dsh-feishu — bridge the Feishu (飞书) open-platform app bot into
 * dsh agents as a Cordis plugin.
 *
 * The plugin opens the Feishu event long connection itself (transport lives in
 * `./client.js`), routes every inbound text message into a per-chat dsh agent
 * (`ctx.agents.create` + `agent.followup`), subscribes to `session/event` to
 * push assistant replies / turn outcomes back to Feishu, and exposes a
 * `/feishu-test` command for connectivity checks.
 *
 * Configuration (`cordis.patch.yml` / plugin settings):
 *   appIdEnv        — env / credential ref for the Feishu App ID (default FEISHU_APP_ID)
 *   appSecretEnv    — env / credential ref for the Feishu App Secret (default FEISHU_APP_SECRET)
 *   webhookUrl      — optional custom-bot webhook (send-only), overrides app bot for send
 *   encryptKey      — optional event-encryption key
 *   receiveChatTypes— ["p2p", "group"] which chat types to accept
 *   allowUsers      — optional allow-list of sender openids (empty = allow all)
 *   cwd             — optional cwd for created agents
 *
 * @module @deepseek-ai/dsh-feishu
 */

import z from "@deepseek-ai/schemastery";
import { createUserMessage } from "@deepseek-ai/dsh-llm";
import { SessionId } from "@deepseek-ai/dsh-session";
import { credentialRef } from "@deepseek-ai/dsh-credentials";
import { launchEnvironmentOf } from "@deepseek-ai/dsh-launch-environment";

import { FeishuClient } from "./client.js";

/** Stable Cordis plugin name. */
export const name = "dsh-feishu";
/** Core services required before the Feishu bridge can start. */
export const inject = ["agentDefaultModel", "agents", "sessions", "commands", "credentials", "settings"];

const DEFAULT_APP_ID_ENV = "FEISHU_APP_ID";
const DEFAULT_APP_SECRET_ENV = "FEISHU_APP_SECRET";

export const Config = z.object({
  appIdEnv: z.string().role("credential-ref").default(DEFAULT_APP_ID_ENV),
  appSecretEnv: z.string().role("credential-ref").default(DEFAULT_APP_SECRET_ENV),
  webhookUrl: z.string().default(""),
  encryptKey: z.string().role("secret").default(""),
  receiveChatTypes: z.array(z.string()).default(["p2p", "group"]),
  allowUsers: z.array(z.string()).default([]),
  /** Optional: create agents under this cwd (defaults to the harness cwd). */
  cwd: z.string().default(""),
});

/** One Feishu chat → one stable dsh session identity. */
function sessionIdFor(chatId) {
  return SessionId(`feishu-${chatId}`);
}

/**
 * Resolve a credential through the credentials service or the launching
 * environment, mirroring the dsh-web-search-wps pattern.
 */
function makeResolver(ctx, envName, fallbackEnv) {
  return async () => {
    const ref = credentialRef(envName || fallbackEnv);
    const credentials = ctx.get("credentials");
    if (credentials !== undefined) {
      const resolved = await credentials.resolve(ref).catch(() => undefined);
      if (resolved?.value !== undefined && resolved.value.length > 0) return resolved.value;
    }
    const ambient = launchEnvironmentOf(ctx).get(envName || fallbackEnv);
    return ambient !== undefined && ambient.value.length > 0 ? ambient.value : undefined;
  };
}

/**
 * Mount the Feishu bridge.
 * @param ctx - plugin context carrying dsh core services.
 * @param config - validated Feishu bridge config.
 */
export function apply(ctx, config) {
  const agents = ctx.get("agents");
  const sessions = ctx.get("sessions");
  const defaultModel = ctx.get("agentDefaultModel");
  if (agents === undefined || sessions === undefined || defaultModel === undefined) {
    throw new Error("dsh-feishu: core services (agents/sessions/agentDefaultModel) are required");
  }

  const resolveAppId = makeResolver(ctx, config.appIdEnv, DEFAULT_APP_ID_ENV);
  const resolveAppSecret = makeResolver(ctx, config.appSecretEnv, DEFAULT_APP_SECRET_ENV);
  const allowSet = new Set(config.allowUsers ?? []);

  /** Ensure one live agent per Feishu chat; returns the agent or undefined. */
  async function ensureAgent(chatId) {
    const sessionId = sessionIdFor(chatId);
    const existing = agents.get(sessionId);
    if (existing !== undefined) return existing;
    const selection = defaultModel.currentSelection();
    try {
      const handle = await agents.create({
        sessionId,
        meta: { cwd: config.cwd ? config.cwd : process.cwd() },
        agentOptions: { provider: selection.provider, model: selection.model },
      });
      return handle.agent;
    } catch (error) {
      ctx.logger?.warn?.(`dsh-feishu: agent create failed for ${chatId}: ${String(error)}`);
      return undefined;
    }
  }

  // ── Feishu transport ─────────────────────────────────────────────────────
  const client = new FeishuClient({
    appId: "",
    appSecret: "",
    webhookUrl: config.webhookUrl || undefined,
    encryptKey: config.encryptKey || undefined,
    receiveChatTypes: config.receiveChatTypes ?? ["p2p", "group"],
    log: (message) => ctx.logger?.info?.(message),
    onMessage: async (chatId, text, meta) => {
      if (allowSet.size > 0 && meta.senderId && !allowSet.has(meta.senderId)) return;
      const agent = await ensureAgent(chatId);
      if (agent === undefined) {
        await client.send(chatId, "dsh-feishu: 无法创建 dsh 会话，请稍后重试。").catch(() => {});
        return;
      }
      agent.followup(
        createUserMessage({
          content: [{ type: "text", text }],
          source: { kind: "plugin", plugin: "dsh-feishu" },
        }),
      );
    },
  });

  // Refresh credentials into the client once resolved.
  (async () => {
    try {
      client.appId = (await resolveAppId()) ?? "";
      client.appSecret = (await resolveAppSecret()) ?? "";
      if (!client.appId || !client.appSecret) {
        ctx.logger?.warn?.(
          `dsh-feishu: 凭据缺失（${config.appIdEnv} / ${config.appSecretEnv}）。请在 dsh 凭据/环境中配置后重载。`,
        );
      }
    } catch (error) {
      ctx.logger?.warn?.(`dsh-feishu: 凭据解析失败: ${String(error)}`);
    }
  })();

  // ── push session events back to Feishu ───────────────────────────────────
  ctx.on("session/event", (session, event) => {
    const chatId = chatIdForSession(session.id);
    if (chatId === undefined) return;
    if (event.type === "assistant/message") {
      const content = event.data?.message?.content ?? [];
      const text = content.filter((b) => b?.type === "text").map((b) => b.text ?? "").join("");
      if (text) client.send(chatId, text).catch((error) => ctx.logger?.warn?.(String(error)));
    } else if (event.type === "turn/end") {
      const reason = event.data?.reason;
      if (reason?.kind === "error") {
        client.send(chatId, `❌ 任务失败: ${reason.error?.code ?? ""} ${reason.error?.message ?? ""}`).catch(() => {});
      } else if (reason?.kind === "aborted") {
        client.send(chatId, "⏹️ 任务已中断。").catch(() => {});
      }
    }
  });

  // ── command: one-click connectivity test ─────────────────────────────────
  const commands = ctx.get("commands");
  commands?.register({
    name: "feishu-test",
    description: "Test the Feishu bridge (check credentials / endpoint)",
    handler: async () => {
      try {
        const appId = await resolveAppId();
        const appSecret = await resolveAppSecret();
        const probe = new FeishuClient({
          appId: appId ?? "",
          appSecret: appSecret ?? "",
          baseUrl: client.baseUrl,
        });
        const endpoint = await probe.requestWsEndpoint();
        return {
          kind: "success",
          text: `✅ dsh-feishu 凭据有效（长连接 endpoint 已获取）。启动桥接后私聊/群里 @机器人即可驱动 dsh。`,
        };
      } catch (error) {
        const code = error?.code ?? "UNKNOWN";
        return { kind: "error", text: `❌ dsh-feishu 测试失败 (${code}): ${error?.message ?? String(error)}` };
      }
    },
  });

  // ── lifecycle ────────────────────────────────────────────────────────────
  const start = async () => {
    try {
      await client.connect();
    } catch (error) {
      ctx.logger?.error?.(`dsh-feishu: ${String(error)}`);
    }
  };
  start();
  ctx.effect(() => {
    return () => client.stop().catch(() => {});
  });
}

/** Map a dsh session id back to its Feishu chat id (ours only). */
function chatIdForSession(sessionId) {
  const id = String(sessionId);
  const prefix = "feishu-";
  return id.startsWith(prefix) ? id.slice(prefix.length) : undefined;
}

export { FeishuClient };

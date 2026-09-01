/**
 * @deepseek-ai/dsh-qq — bridge the QQ official open-platform bot (C2C private
 * chats) into dsh agents as a Cordis plugin.
 *
 * The plugin opens the QQ WebSocket long connection itself (transport lives in
 * `./client.js`), routes every inbound C2C text message into a per-user dsh
 * agent (`ctx.agents.create` + `agent.followup`), subscribes to `session/event`
 * to push assistant replies / turn outcomes back to QQ, and exposes a
 * `/qq-test` command for connectivity checks.
 *
 * Configuration (`cordis.patch.yml` / plugin settings):
 *   appIdEnv      — env / credential ref for the QQ AppID (default QQ_OFFICIAL_APP_ID)
 *   appSecretEnv  — env / credential ref for the QQ AppSecret (default QQ_OFFICIAL_APP_SECRET)
 *   sandbox       — true = QQ sandbox environment (default false = production)
 *   allowUsers    — optional allow-list of user openids (empty = allow all)
 *
 * @module @deepseek-ai/dsh-qq
 */

import z from "@deepseek-ai/schemastery";
import { createUserMessage } from "@deepseek-ai/dsh-llm";
import { SessionId } from "@deepseek-ai/dsh-session";
import { credentialRef } from "@deepseek-ai/dsh-credentials";
import { launchEnvironmentOf } from "@deepseek-ai/dsh-launch-environment";

import { QqClient } from "./client.js";

/** Stable Cordis plugin name. */
export const name = "dsh-qq";
/** Core services required before the QQ bridge can start. */
export const inject = ["agentDefaultModel", "agents", "sessions", "commands", "credentials", "settings"];

const DEFAULT_APP_ID_ENV = "QQ_OFFICIAL_APP_ID";
const DEFAULT_APP_SECRET_ENV = "QQ_OFFICIAL_APP_SECRET";

export const Config = z.object({
  appIdEnv: z.string().role("credential-ref").default(DEFAULT_APP_ID_ENV),
  appSecretEnv: z.string().role("credential-ref").default(DEFAULT_APP_SECRET_ENV),
  sandbox: z.boolean().default(false),
  allowUsers: z.array(z.string()).default([]),
  /** Optional: create agents under this cwd (defaults to the harness cwd). */
  cwd: z.string().default(""),
});

/** Settings namespace that surfaces the QQ bridge's key references. */
const SETTINGS_NAMESPACE = "dsh-qq";

/** One QQ user → one stable dsh session identity. */
function sessionIdFor(openid) {
  return SessionId(`qq-official-${openid}`);
}

/**
 * Resolve the QQ AppID / AppSecret through the credentials service or the
 * launching environment, mirroring the dsh-web-search-wps pattern.
 */
function resolveCredentials(ctx, config) {
  return async (refEnv) => {
    const ref = credentialRef(config[refEnv] ?? (refEnv === "appIdEnv" ? DEFAULT_APP_ID_ENV : DEFAULT_APP_SECRET_ENV));
    const credentials = ctx.get("credentials");
    if (credentials !== undefined) {
      const resolved = await credentials.resolve(ref).catch(() => undefined);
      if (resolved?.value !== undefined && resolved.value.length > 0) return resolved.value;
    }
    const ambient = launchEnvironmentOf(ctx).get(config[refEnv] ?? refEnv);
    return ambient !== undefined && ambient.value.length > 0 ? ambient.value : undefined;
  };
}

/**
 * Mount the QQ bridge.
 * @param ctx - plugin context carrying dsh core services.
 * @param config - validated QQ bridge config.
 */
export function apply(ctx, config) {
  const agents = ctx.get("agents");
  const sessions = ctx.get("sessions");
  const defaultModel = ctx.get("agentDefaultModel");
  if (agents === undefined || sessions === undefined || defaultModel === undefined) {
    throw new Error("dsh-qq: core services (agents/sessions/agentDefaultModel) are required");
  }

  const resolve = resolveCredentials(ctx, config);
  const allowSet = new Set(config.allowUsers ?? []);

  /** Ensure one live agent per QQ user; returns the handle or undefined. */
  async function ensureAgent(openid, firstMessage) {
    const sessionId = sessionIdFor(openid);
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
      ctx.logger?.warn?.(`dsh-qq: agent create failed for ${openid}: ${String(error)}`);
      return undefined;
    }
  }

  // ── QQ transport ─────────────────────────────────────────────────────────
  const client = new QqClient({
    appId: "", // set after first credential resolution below
    appSecret: "",
    sandbox: Boolean(config.sandbox),
    allowUsers: allowSet,
    log: (message) => ctx.logger?.info?.(message),
    onMessage: async (openid, text, raw) => {
      if (allowSet.size > 0 && !allowSet.has(openid)) return;
      const agent = await ensureAgent(openid, text);
      if (agent === undefined) {
        await client.sendC2c(openid, "dsh-qq: 无法创建 dsh 会话，请稍后重试。");
        return;
      }
      agent.followup(
        createUserMessage({
          content: [{ type: "text", text }],
          source: { kind: "plugin", plugin: "dsh-qq" },
        }),
      );
    },
  });

  // Refresh credentials into the client once resolved (they can change later).
  (async () => {
    try {
      const appId = await resolve("appIdEnv");
      const appSecret = await resolve("appSecretEnv");
      client.appId = appId ?? "";
      client.appSecret = appSecret ?? "";
      if (!client.appId || !client.appSecret) {
        ctx.logger?.warn?.(
          `dsh-qq: 凭据缺失（${config.appIdEnv} / ${config.appSecretEnv}）。请在 dsh 凭据/环境中配置后重载。`,
        );
      }
    } catch (error) {
      ctx.logger?.warn?.(`dsh-qq: 凭据解析失败: ${String(error)}`);
    }
  })();

  // ── push session events back to QQ ───────────────────────────────────────
  // `session/event` is a global broadcast; filter to the sessions we own.
  ctx.on("session/event", (session, event) => {
    const openid = openidForSession(session.id);
    if (openid === undefined) return;
    if (event.type === "assistant/message") {
      const content = event.data?.message?.content ?? [];
      const text = content.filter((b) => b?.type === "text").map((b) => b.text ?? "").join("");
      if (text) client.sendC2c(openid, text).catch((error) => ctx.logger?.warn?.(String(error)));
    } else if (event.type === "turn/end") {
      const reason = event.data?.reason;
      if (reason?.kind === "error") {
        client.sendC2c(openid, `❌ 任务失败: ${reason.error?.code ?? ""} ${reason.error?.message ?? ""}`).catch(() => {});
      } else if (reason?.kind === "aborted") {
        client.sendC2c(openid, "⏹️ 任务已中断。").catch(() => {});
      }
    }
  });

  // ── command: one-click connectivity test ─────────────────────────────────
  const commands = ctx.get("commands");
  commands?.register({
    name: "qq-test",
    description: "Test the QQ official bridge (send a message to your own openid)",
    handler: async () => {
      try {
        const appId = await resolve("appIdEnv");
        const appSecret = await resolve("appSecretEnv");
        const client2 = new QqClient({ appId: appId ?? "", appSecret: appSecret ?? "", sandbox: Boolean(config.sandbox) });
        const token = await client2.accessToken();
        return {
          kind: "success",
          text: `✅ dsh-qq 凭据有效（AppID 已配置，token 获取成功）。启动桥接后私聊机器人即可驱动 dsh。`,
        };
      } catch (error) {
        const code = error?.code ?? "UNKNOWN";
        return { kind: "error", text: `❌ dsh-qq 测试失败 (${code}): ${error?.message ?? String(error)}` };
      }
    },
  });

  // ── lifecycle ────────────────────────────────────────────────────────────
  const start = async () => {
    try {
      await client.connect();
    } catch (error) {
      ctx.logger?.error?.(`dsh-qq: ${String(error)}`);
    }
  };
  start();
  // Cordis: register a disposer so the long connection is torn down when the
  // plugin fiber unloads (HMR / profile shutdown / plugin disable).
  ctx.effect(() => {
    return () => client.stop().catch(() => {});
  });
}

/** Map a dsh session id back to its QQ openid (ours only). */
function openidForSession(sessionId) {
  const id = String(sessionId);
  const prefix = "qq-official-";
  return id.startsWith(prefix) ? id.slice(prefix.length) : undefined;
}

export { QqClient };

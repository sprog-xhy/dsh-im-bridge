/**
 * @deepseek-ai/dsh-feishu — bridge the Feishu (飞书) open-platform app bot into
 * dsh agents as a Cordis plugin.
 *
 * @module @deepseek-ai/dsh-feishu
 */
import type { Context } from '@deepseek-ai/cordis';
import z from '@deepseek-ai/schemastery';
/** Stable Cordis plugin name. */
export declare const name = 'dsh-feishu';
/** Core services required before the Feishu bridge can start. */
export declare const inject: string[];
/** Plugin config: Feishu credentials and behavior knobs. */
export interface Config {
    /** Env-var name whose value holds the Feishu App ID (default FEISHU_APP_ID). */
    appIdEnv: string;
    /** Env-var name whose value holds the Feishu App Secret (default FEISHU_APP_SECRET). */
    appSecretEnv: string;
    /** Optional custom-bot webhook URL (send-only), overrides app bot for send. */
    webhookUrl: string;
    /** Optional event-encryption key. */
    encryptKey: string;
    /** Which chat types to accept (default ["p2p", "group"]). */
    receiveChatTypes: string[];
    /** Optional allow-list of sender openids; empty = allow all. */
    allowUsers: string[];
    /** Optional cwd for created agents. */
    cwd: string;
}
export declare const Config: z<Config>;
/**
 * Mount the Feishu bridge: connect the long connection, route inbound messages
 * into per-chat dsh agents, and push assistant replies/questions back.
 * @param ctx - plugin context carrying dsh core services.
 * @param config - validated Feishu bridge config.
 */
export declare function apply(ctx: Context, config: Config): void;
export {};

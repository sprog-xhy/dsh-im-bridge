/**
 * @deepseek-ai/dsh-qq — bridge the QQ official open-platform bot (C2C private
 * chats) into dsh agents as a Cordis plugin.
 *
 * @module @deepseek-ai/dsh-qq
 */
import type { Context } from '@deepseek-ai/cordis';
import z from '@deepseek-ai/schemastery';
/** Stable Cordis plugin name. */
export declare const name = 'dsh-qq';
/** Core services required before the QQ bridge can start. */
export declare const inject: string[];
/** Plugin config: QQ credentials and behavior knobs. */
export interface Config {
    /** Env-var name whose value holds the QQ AppID (default QQ_OFFICIAL_APP_ID). */
    appIdEnv: string;
    /** Env-var name whose value holds the QQ AppSecret (default QQ_OFFICIAL_APP_SECRET). */
    appSecretEnv: string;
    /** QQ sandbox environment (default false = production). */
    sandbox: boolean;
    /** Optional allow-list of user openids; empty = allow all. */
    allowUsers: string[];
}
export declare const Config: z<Config>;
/**
 * Mount the QQ bridge: connect the official long connection, route inbound C2C
 * messages into per-user dsh agents, and push assistant replies/questions back
 * to QQ.
 * @param ctx - plugin context carrying dsh core services.
 * @param config - validated QQ bridge config.
 */
export declare function apply(ctx: Context, config: Config): void;
export {};

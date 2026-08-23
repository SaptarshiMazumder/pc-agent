/**
 * Wire protocol types — the TS mirror of agent_runtime/presentation/protocol.py and the payloads in
 * docs/PROTOCOL.md. Additive server fields are always allowed; clients ignore what they don't know.
 */
/** The protocol generation this SDK speaks (mirrors gateway.PROTOCOL_VERSION). */
declare const PROTOCOL_VERSION = 1;
interface RequestFrame {
    type: 'req';
    id: string;
    method: string;
    params: Record<string, unknown>;
}
interface ResponseFrame {
    type: 'res';
    id: string;
    ok: boolean;
    payload: Record<string, any>;
}
interface EventFrame {
    type: 'event';
    event: string;
    payload: Record<string, any>;
}
type Frame = ResponseFrame | EventFrame;
/** An agent's app surface (present only for app agents — agents shipping their own UI). */
interface AgentApp {
    title: string;
    /** absolute path portion (e.g. "/apps/<id>/") — the opener appends its own token/scope */
    url: string;
}
interface AgentInfo {
    id: string;
    name: string;
    version?: string;
    tagline?: string;
    suggestions?: string[];
    color?: string;
    /** null/absent for plain chat agents */
    app?: AgentApp | null;
}
interface Hello {
    agentName: string;
    agentId: string;
    model: string;
    version: string;
    protocol: number;
    /** advisory in v1: false when this client declared a NEWER protocol than the server speaks */
    compatible?: boolean;
    product: string;
    productId: string;
    storeEnabled: boolean;
    registryConfigured: boolean;
    registryUrl: string;
    localRegistryDir: string;
    workspace: string;
    agents: AgentInfo[];
}
interface SessionRow {
    sessionId: string;
    title: string;
    titleManual?: boolean;
    snippet?: string;
    projectId: string;
    messages: number;
    modified: number;
    agentId: string;
}
/** The inner event of a chat.event push: {type: "message_update" | "tool_execution_end" | ...}. */
interface AgentEvent {
    type: string;
    [key: string]: any;
}
/** chat.event payload as broadcast by the daemon. */
interface ChatEventPayload {
    sessionKey: string;
    runId: string;
    /** which agent the run belongs to (protocol v1 additive field) */
    agentId?: string;
    ts: number;
    event: AgentEvent;
}
interface CapabilityDescriptor {
    kind: 'tool' | 'plugin' | 'skill' | 'agent';
    id: string;
    name: string;
    description: string;
    source: string;
    extra: Record<string, unknown>;
}
interface InvokeResult {
    text: string;
    artifacts: Array<Record<string, any>>;
}
interface SendResult {
    runId: string;
    deduplicated?: boolean;
    attachments?: Array<Record<string, any>>;
}
interface Attachment {
    name: string;
    mimeType?: string;
    dataBase64: string;
}
/** The full text of a tool result (a message dict with content blocks). */
declare function resultText(result: any): string;

/**
 * AgentdClient — one WebSocket to an agentd daemon: promise-based requests (id-matched res
 * frames) + fan-out of broadcast events, with auto-reconnect. Extracted from the desktop
 * client's proven GatewayClient and published as the ONE way to speak the protocol.
 *
 * Transport-agnostic by contract: you hand it a URL + token (local ws:// today, hosted wss://
 * tomorrow) — the SDK itself never discovers, assumes, or prefers any location.
 *
 * Runs anywhere a WHATWG WebSocket exists: browsers (agent apps), Electron, Node >= 21.
 */

type ConnectionStatus = 'connecting' | 'open' | 'closed';
type EventHandler = (payload: Record<string, any>) => void;
type StatusHandler = (status: ConnectionStatus) => void;
/** Where and how to connect. `url` may be ws(s):// or http(s):// (auto-upgraded to ws). */
interface ConnectTarget {
    url: string;
    /** The MACHINE token — may this client connect at all. */
    token?: string;
    /** The SESSION token — WHO is connecting. Two credentials, two jobs: `token` is a machine
     *  secret, `session` identifies a person. A hosted daemon has no machine token and the session
     *  does both, which is why the server falls back to `token` when this is absent. */
    session?: string;
    /** WHICH KEYS pay for this connection's model calls: 'local' or 'cloud'. A preference, never a
     *  credential — the daemon pays with the session above, so a client can only bill itself. */
    mode?: string;
    /** app connections: restrict this connection to one agent (stable tier only) */
    scope?: string;
}
/** Static target, or a resolver called on EVERY (re)connect — so a host that can re-discover
 *  a restarted daemon (new port/token) hands a function; a browser app hands its fixed URL. */
type ConnectInput = ConnectTarget | (() => Promise<ConnectTarget>);
interface AgentdClientOptions {
    /** identifies this client in hello (e.g. "my-app/1.0"); helps server-side observability */
    clientName?: string;
}
declare class AgentdClient {
    private ws;
    private input;
    private nextId;
    private pending;
    private eventHandlers;
    private statusHandlers;
    /** The last status announced — see `onStatus` for why this is remembered. */
    private status;
    private reconnectDelay;
    /** When the current socket opened, so "did this connection actually work?" can be answered. */
    private openedAt;
    private static readonly UNAUTHORIZED_DELAY;
    private static readonly HEALTHY_MS;
    private closedByUs;
    private lastTarget;
    private readonly clientName;
    constructor(options?: AgentdClientOptions);
    /** Connect (or switch) to a daemon. Reconnects automatically with backoff until close(). */
    connect(input: ConnectInput): void;
    close(): void;
    /** Re-open the socket, re-reading the target.
     *
     *  Identity and run mode are read by the daemon when a connection OPENS, so changing either
     *  has to bring up a new one — otherwise the daemon goes on answering as whoever this client
     *  was before. Called by authLogin / authLogout / setRunMode. */
    reconnect(): void;
    get connected(): boolean;
    private scheduleReconnect;
    private open;
    /** Detach + close the current socket without triggering its reconnect. */
    private teardownSocket;
    request<T = Record<string, any>>(method: string, params?: Record<string, unknown>): Promise<T>;
    /** Subscribe to a broadcast event by name. Returns the unsubscribe. */
    on(event: string, handler: EventHandler): () => void;
    /**
     * Subscribe to connection status. Returns the unsubscribe.
     *
     * THE CURRENT STATUS ARRIVES IMMEDIATELY, before this returns. Status was transitions-only, and
     * a subscriber that mounted after the socket opened — which is most of them, since connecting
     * starts at construction and React mounts a frame later — heard nothing until the next
     * reconnect. The symptom is a composer that says "connecting…" and refuses to send over a
     * perfectly open socket.
     */
    onStatus(handler: StatusHandler): () => void;
    private handleFrame;
    /** Handshake — introduces this client + its protocol so the server can flag compatibility. */
    hello(): Promise<Hello>;
    agents(): Promise<{
        agents: AgentInfo[];
        default: string;
    }>;
    agentDetail(agentId: string): Promise<Record<string, any>>;
    sessions(agentId?: string): Promise<{
        sessions: SessionRow[];
    }>;
    history(sessionKey: string, agentId?: string): Promise<{
        messages: any[];
    }>;
    send(opts: {
        message: string;
        sessionKey?: string;
        agentId?: string;
        projectId?: string;
        attachments?: Attachment[];
        idempotencyKey?: string;
    }): Promise<SendResult>;
    abort(sessionKey: string): Promise<{
        aborted: boolean;
        runId?: string;
    }>;
    invokeTool(name: string, params?: Record<string, unknown>): Promise<InvokeResult>;
    capabilities(agentId?: string): Promise<{
        capabilities: CapabilityDescriptor[];
    }>;
    catalog(): Promise<Record<string, any>>;
    notifications(): Promise<Record<string, any>>;
    /**
     * Follow ONE session's run events (the daemon broadcasts every session's events to every
     * authorized socket — this does the filtering bookkeeping for you). Returns the unsubscribe.
     */
    onRun(sessionKey: string, handler: (payload: ChatEventPayload) => void): () => void;
    /** Follow every run of ONE agent (uses the protocol-v1 agentId event field). */
    onAgent(agentId: string, handler: (payload: ChatEventPayload) => void): () => void;
    /** Build the authenticated GET /file URL for a server-side artifact path. */
    fileUrl(path: string): string;
}
/**
 * Convenience for agent apps served by the daemon at /apps/<id>/: the opener put token+scope
 * in the page URL and the WS shares the page's own origin — so an app connects with one line:
 *   const client = agentd.fromPage()
 */
declare function fromPage(options?: AgentdClientOptions): AgentdClient;

/**
 * The contract between this package and whoever hosts it. A leaf: imports nothing.
 *
 * Everything host-specific is a parameter. The agentd client resolves its accounts URL from
 * discovery and keeps secrets in the OS keychain; an agent window asks the daemon and has only
 * localStorage. Neither fact belongs in the renewal logic, and hard-coding either is what forced
 * a second implementation last time.
 */
/** The credential pair, plus who it belongs to. */
interface TokenPair {
    /** Short-lived (~10 min). The ONLY half that ever travels on a connection. */
    accessToken: string;
    /**
     * Long-lived (30 days), single-use, and rotating. Exchanged ONLY at `<accounts>/auth/refresh`.
     *
     * Empty is a legitimate state, not a broken one: a window opened by the desktop app is handed
     * an access token on its launch URL and deliberately never receives this one — it runs
     * third-party code, and this is a 30-day credential for the whole account. Such a window cannot
     * renew itself and is fed instead (`adopt`).
     */
    refreshToken: string;
    /** Absolute epoch ms when `accessToken` dies. */
    expiresAt: number;
    accountId: string;
    email: string;
}
/**
 * Where the refresh token is kept.
 *
 * Async because the desktop's answer is an IPC call to the OS keychain. The access token is NOT
 * stored here — see `SessionStore`.
 */
interface SecretStore {
    read(): Promise<string | null>;
    write(token: string | null): Promise<void>;
}
/**
 * Where the non-secret half of the session is kept, synchronously.
 *
 * SYNCHRONOUS ON PURPOSE. An agent window is handed its credential on the launch URL, holds no
 * refresh token, and must survive a reload — so the access token has to be readable before the
 * first await. localStorage is the only store that shape works with.
 */
interface SessionStore {
    read(): string | null;
    write(value: string | null): void;
}
interface AuthConfig {
    /**
     * The accounts service base URL, no trailing slash. A RESOLVER, never a snapshot.
     *
     * It held a copied string once, set by whichever caller ran first — and one of them did not:
     * signing in fresh never configured it, so every later refresh returned null before making a
     * request. The symptom would have been a user signed out ten minutes after logging in, only if
     * they had signed in rather than resumed. A function cannot go stale and cannot be read too
     * early, and it picks up discovery resolving later for free.
     */
    accountsUrl: () => string | Promise<string>;
    /** Sync store for the session. Required — every host has one. */
    session: SessionStore;
    /** OS-encrypted store for the refresh token. Omit and it rides in `session` instead. */
    secrets?: SecretStore;
    /** Names this client to the server, so `/me/devices` can tell them apart. */
    clientId: string;
    /** A human-readable device name for the same list. Best-effort; never blocks sign-in. */
    deviceLabel?: () => string;
    /**
     * Called on every change to the pair, including renewal and sign-out.
     *
     * This is how a host applies a new credential without this package knowing what a socket is.
     * The agentd client reconnects its gateway; an agent window swaps the token on the live socket
     * with `auth.update`, which is what lets a renewal happen mid-run without dropping it.
     */
    onChange?: (pair: TokenPair | null) => void;
    /** Injected for tests. Defaults to global fetch. */
    fetchImpl?: typeof fetch;
    timeoutMs?: number;
}

/** When an access token dies, in epoch ms, from its own `exp`. 0 when unreadable. */
declare function accessTokenExpiry(token: string): number;
/** Which account an access token speaks for, from its `sub`. '' when unreadable. */
declare function accessTokenAccount(token: string): string;

/**
 * localStorage-backed stores, and the KEY each host reads.
 *
 * The key is a parameter rather than a constant for one reason: two agent windows served from the
 * same origin that shared a key would silently become one session — one agent's credential
 * quietly becoming another's.
 */

/** A `SessionStore` over localStorage, inert where storage is unavailable. */
declare function localSessionStore(key: string): SessionStore;
/** An in-memory store — for tests, and for a host that must not persist at all. */
declare function memorySessionStore(): SessionStore;

/**
 * TokenManager — the ONE place that mints, keeps and renews a credential.
 *
 * There were two of these and they disagreed. This is the one that was right, generalised so the
 * agent SDK can use it too, with the three defects the other copy had fixed rather than carried:
 *
 *  1. RENEW A TOKEN THAT HAS ALREADY EXPIRED. The old SDK guarded renewal with
 *     `life > 0 && life < 10min`, so the moment a token actually died — a sleeping laptop, a
 *     throttled background tab, a long agent run — renewal declined to act, and never acted
 *     again. Expiry is the reason to refresh, not a reason to stop.
 *
 *  2. SINGLE-FLIGHT. Refresh tokens are single-use and rotating, and the server treats a second
 *     use as theft: it revokes the whole family, which signs the user out EVERYWHERE. Two windows
 *     waking together, or one firing two ticks, was enough to trigger it. Every caller here shares
 *     one promise.
 *
 *  3. A REFUSED REFRESH IS TERMINAL; A FAILED ONE IS NOT. 401/403 means the family is gone — clear
 *     it and let the host show a form. Anything else is a network or a bad afternoon, and must NOT
 *     sign anyone out.
 *
 * WHAT IT DELIBERATELY DOES NOT KNOW: what a socket is, where the accounts service lives, or how
 * this host keeps a secret. All three arrive through `AuthConfig` — which is what lets one
 * implementation serve a desktop app with an OS keychain and an agent window with localStorage.
 */

declare class TokenManager {
    private readonly config;
    private pair;
    private inflight;
    private timer;
    private readonly listeners;
    private wake;
    constructor(config: AuthConfig);
    /** What is held right now, WITHOUT renewing. Synchronous, for a socket URL or a rendered email. */
    current(): TokenPair | null;
    /** Is there a credential this client can still use, or still renew? */
    signedIn(): boolean;
    /**
     * A USABLE access token, renewing first when the one we hold is spent.
     *
     * The only way anything should ever obtain a credential, so that no caller anywhere has to
     * reason about expiry — which is exactly the reasoning every caller previously got wrong.
     */
    accessToken(): Promise<string>;
    subscribe(cb: (p: TokenPair | null) => void): () => void;
    /**
     * Sign in, creating the account first when `signup`.
     *
     * THROWS on a rejected credential, carrying the service's own message ("incorrect password") so
     * a form has something to show. A failed attempt must never resolve to a signed-out state: the
     * caller cannot tell that apart from having signed out, and the user is left looking at a form
     * that cleared itself.
     */
    login(args: {
        email: string;
        password: string;
        signup?: boolean;
    }): Promise<TokenPair>;
    /**
     * Re-establish a session at start-up.
     *
     * This is what makes "stay signed in" work with a ten-minute access token: nothing durable is
     * kept but the refresh token, and one exchange at boot turns it into a usable pair. A window
     * holding no refresh token (opened by the desktop app, and fed rather than renewing) keeps
     * whatever it was handed — unless that has died, in which case it is dropped, because a page
     * presenting a dead token is not refused, it is accepted ANONYMOUSLY.
     */
    restore(): Promise<TokenPair | null>;
    /**
     * Trade a live access token for a session of this client's own. Returns null when there is
     * nothing live to trade, or the server declined.
     *
     * NEVER THROWS. It runs on a boot path beside things that matter more; a window that cannot
     * derive is no worse off than it was a moment ago — it still holds a working access token, and
     * it degrades to exactly the old behaviour rather than failing to start.
     */
    derive(): Promise<TokenPair | null>;
    /**
     * Trade the refresh token for a new pair. SINGLE-FLIGHT — see the header.
     *
     * Returns null when the session is over, having cleared it; and null WITHOUT clearing when the
     * attempt merely failed. The difference is the whole point.
     */
    refresh(): Promise<TokenPair | null>;
    private exchange;
    /**
     * Write a credential directly, with NO account check. The unguarded door.
     *
     * There is exactly one honest use: a host adopting a credential an opener handed it, such as the
     * `?session=` on an agent window's launch URL. Everything else — sign-in, renewal, a token
     * pushed by the desktop app — has a guarded path above, and using this instead skips the check
     * that path exists for.
     *
     * Synchronous in effect: the pair is live the moment this returns, because a caller that writes
     * a session and immediately builds a socket URL from it cannot wait for a keychain round trip.
     */
    replace(pair: TokenPair | null): void;
    /**
     * Adopt an access token minted elsewhere — the desktop app pushing one into an agent window.
     *
     * WHOSE TOKEN IS THIS? The push reaches EVERY open window at once and cannot know that one of
     * them signed in as somebody else. Adopting it there would leave this account's email and
     * refresh token stored beside another account's access token, and land the window on the wrong
     * account while still displaying this one's address. An unreadable token fails CLOSED.
     *
     * Holding no accountId is the ordinary case, not an exception: a window opened BY the desktop
     * app took its credential from the launch URL and recorded no account, so it has nothing to
     * disagree with and accepts every push.
     */
    adopt(accessToken: string): Promise<boolean>;
    /**
     * Forget this client's session, and tell the server so.
     *
     * A sign-out that only forgets locally leaves a 30-day credential alive on a machine the user
     * may have just decided they do not trust. Best-effort: being offline must not block signing out.
     */
    logout(): Promise<void>;
    /**
     * Keep the credential fresh for as long as the host lives. Returns a stop function.
     *
     * TWO TRIGGERS, because a timer alone is provably not enough. Timers do not fire while a machine
     * sleeps and are throttled in background tabs, so a window that was away comes back holding a
     * token that died hours ago — the single most common way this used to break, and the one a
     * schedule can never cover. Coming back is therefore its own trigger.
     */
    start(): () => void;
    stop(): void;
    private tick;
    private schedule;
    private expired;
    /** Close enough to the end to be worth renewing now — or already past it. */
    private expiringSoon;
    private set;
    private writeStored;
    private readStored;
    private readSecret;
    private toPair;
    private base;
    private deviceLabel;
    private send;
    private post;
}

/**
 * What THIS client knows about itself: who is signed in, and which keys it wants to pay with.
 *
 * THE CLIENT HOLDS BOTH FACTS. The daemon stores neither. That is not a stylistic choice: a
 * daemon-side session is ONE slot, and one slot cannot serve two people — the second to sign in
 * overwrites the first, signing out signs out everybody, and one window's Cloud switch moves every
 * other window's billing. Held per client and presented per connection, a hundred users on one
 * daemon is a hundred sockets with a hundred answers.
 *
 * THE CREDENTIAL HALF NOW LIVES IN `@agentd/auth`, not here. This module used to own the storage
 * format, the expiry rules and (next door, in auth.ts) a renewal loop — a second implementation of
 * what the agentd client already did, which drifted from it and lost. What is left here is the
 * part that genuinely is this client's own: WHICH KEYS PAY, which is not an identity question and
 * has no server-side equivalent.
 */

/**
 * A session as the rest of the SDK reads it.
 *
 * A projection of `@agentd/auth`'s `TokenPair`, kept in this shape because agent apps already
 * destructure it. The manager is the source of truth; this is a view of it.
 */
interface StoredSession {
    /** The ACCESS token — short-lived and the only one that travels on a connection. */
    token: string;
    email: string;
    accountId: string;
    /** Absent in a window opened BY the desktop app: it is fed tokens instead of renewing. */
    refreshToken?: string;
    /** Epoch ms when `token` expires. */
    expiresAt?: number;
}
/** 'local' = my own provider keys. 'cloud' = platform keys, metered to my account. */
type RunMode = 'local' | 'cloud';
/**
 * The session this client can still use, or null.
 *
 * SYNCHRONOUS, because the socket URL is built from it and a page must be able to answer "who am
 * I" before its first await. Renewal happens on its own schedule; this reports what is held now.
 * Anything that needs a credential it can RELY on should await `identity().accessToken()`, which
 * renews first.
 */
declare function loadSession(storageKey?: string): StoredSession | null;
/**
 * Write a session directly.
 *
 * The ONE legitimate caller is `fromPage` in client.ts, adopting the access token an opener put on
 * the launch URL. Everything else should go through sign-in or renewal — writing a credential by
 * hand is how a page ends up holding one nothing can renew.
 */
declare function saveSession(value: StoredSession | null, storageKey?: string): void;
declare function loadMode(storageKey?: string): RunMode | null;
/** null clears the choice, returning this client to the default (cloud when it has a session). */
declare function saveMode(value: RunMode | null, storageKey?: string): void;
/**
 * The mode this client should run in: what it CHOSE, else the default.
 *
 * ONE PLACE, because two readers need the same answer and a disagreement between them is
 * invisible: the settings page renders it, and the socket sends it. If the page defaulted to cloud
 * while the connect URL sent nothing, the UI would promise platform keys while the calls went out
 * on the user's own.
 *
 * Default is CLOUD once signed in — and only where there is a proxy to reach.
 */
declare function effectiveMode(storageKey?: string, signedIn?: boolean, canUseCloud?: boolean): RunMode;

/**
 * What the DAEMON says about the platform it is part of: where people sign in, and whether a
 * model proxy exists to switch to.
 *
 * Its own module because two things need it and they must not import each other: the sign-in flow
 * (auth.ts) and the token manager that renews what sign-in produced (identity.ts). A leaf.
 */
/** Options shared by everything that talks to the daemon over plain HTTP. */
interface DaemonOptions {
    /** Daemon HTTP origin. Defaults to the page's own — an agent app is served BY the daemon. */
    origin?: string;
    /** The daemon's bearer token. Defaults to `?token=` on the page URL. */
    token?: string;
    timeoutMs?: number;
}
declare const DEFAULT_TIMEOUT = 45000;
declare function daemonOrigin(opts: DaemonOptions): string;
declare function daemonToken(opts: DaemonOptions): string;
declare function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T>;
/** The daemon's own view: where sign-in lives, and whether a proxy exists to switch to. */
declare function platformStatus(opts: DaemonOptions): Promise<Record<string, any>>;
/** Just the accounts service address, or '' when this daemon has none. */
declare function accountsUrl(opts: DaemonOptions): Promise<string>;

/**
 * Sign-in for an agent window — ORDINARY HTTP, from the client, exactly like any web app.
 *
 *   GET  <daemon>/platform/status   → where the accounts service is
 *   POST <accounts>/signup          (only when creating)
 *   POST <accounts>/auth/login      → an access token and a refresh token
 *
 * The daemon is not in the middle of this. It answers one question — "where do people sign in?" —
 * and is then told the answer on the next connection.
 *
 * WHY NOT THROUGH THE DAEMON. It was, briefly: three socket methods, with the daemon performing
 * the exchange and keeping the token. That put ONE session on the machine, and one session cannot
 * serve two people — the second to sign in overwrote the first, signing out signed out everybody,
 * and any way to read the token back handed one user another's credential.
 *
 * EVERY LINE OF CREDENTIAL HANDLING BELOW IS A DELEGATION. This file used to implement sign-in,
 * refresh and a renewal timer itself — a second copy of what `clients/ui` already had, which
 * drifted from it and lost. It would not renew a token that had ALREADY expired (the one case that
 * matters), it had no single-flight guard, so two windows waking together could trip the server's
 * refresh-reuse detector and get the whole family revoked, and it posted to `/login` where the
 * other client posted to `/auth/login`. One implementation now lives in `@agentd/auth` and both
 * clients call it. Adding credential logic here means writing the third copy, so do not.
 */

interface AuthState {
    /** Is an accounts service configured on this daemon? false => no sign-in to offer. */
    available: boolean;
    /** Is THIS client signed in? */
    signedIn: boolean;
    email: string;
    accountId: string;
    /** Which keys this client's model calls run on. */
    mode: RunMode;
    /** Is there a Cloud to switch to on this build? */
    canUseCloud: boolean;
    /**
     * Does this daemon DEMAND an account, or merely offer one?
     *
     * `available` says an accounts service exists. That is not the same question, and conflating
     * them is why a desktop daemon — which accepts the machine token and requires no account at
     * all — still put a sign-in form in front of every window. Only the daemon knows: it is an
     * explicit hosted opt-in, not something a client can infer from a configured URL.
     */
    required: boolean;
}
interface AuthOptions extends DaemonOptions {
    /** The connected client, so a change can reach the daemon at once. */
    client?: AgentdClient;
    /** Storage key override; defaults to one derived from the agent id in the page URL. */
    storageKey?: string;
}
/** What this client is, right now: its own state, plus what the daemon offers. */
declare function authStatus(opts?: AuthOptions): Promise<AuthState>;
/**
 * Sign in, or create the account first when `signup`.
 *
 * REJECTS on a rejected credential, carrying the accounts service's own message ("incorrect
 * password") so a form has something to show. A failed attempt must never resolve to
 * `signedIn: false`: the caller cannot tell that apart from "signed out", and the user is left
 * looking at a form that cleared itself.
 */
declare function authLogin(args: {
    email: string;
    password: string;
    signup?: boolean;
}, opts?: AuthOptions): Promise<AuthState>;
/**
 * Renew the access token now. Returns the new one, or '' when there was nothing to renew with.
 *
 * Rarely wanted directly: renewal runs on its own from the moment this window has a session, and
 * anything that needs a credential should ASK for one (`identity().accessToken()`) rather than
 * renew first and hope.
 */
declare function authRefresh(opts?: AuthOptions): Promise<string>;
/**
 * Keep this window's access token fresh for as long as the page is open. Returns a stop function.
 *
 * Renewal is ALREADY RUNNING by the time anything can call this — it starts with the manager, so a
 * window cannot end up holding a credential nothing is looking after just because its author did
 * not know to ask. Kept because agent apps call it, and because saying so explicitly is
 * reasonable. Idempotent.
 */
declare function startAuthRenewal(opts?: AuthOptions): () => void;
/**
 * Accept access tokens pushed down by the desktop app. Returns an unsubscribe.
 *
 * A window opened from the desktop app gets its credential on the launch URL and holds NO refresh
 * token — deliberately, because an agent app is third-party code and a refresh token is a 30-day
 * credential for the user's whole account. So it cannot renew itself; the desktop app, which does
 * hold the refresh token, mints short-lived access tokens and hands them down (see the desktop's
 * src/preload/app.ts). A no-op in a browser tab, where there is no bridge to listen to.
 *
 * WHETHER a pushed token is taken is the manager's decision (`adopt`): the push reaches every open
 * window at once, and a window signed in as somebody else must not silently become the pusher's
 * account.
 */
declare function acceptHostTokens(opts?: AuthOptions): () => void;
/** Forget this client's session. Other windows keep theirs — each holds its own. */
declare function authLogout(opts?: AuthOptions): Promise<AuthState>;
/** Choose which keys pay for THIS client's model calls. Other clients are unaffected. */
declare function setRunMode(mode: RunMode, opts?: AuthOptions): Promise<AuthState>;

/**
 * The shapes money arrives in. Field-for-field what the accounts service returns, renamed to
 * camelCase once, here — so no consumer parses `credits_remaining` a second time and no consumer
 * gets to disagree about what a pack is.
 */
type Credits = {
    creditsRemaining: number;
    fundingSource: string;
    creditClass: string;
    modelTierMax: string;
    entitlementRequired: boolean;
    entitled: boolean;
    expiresAt: number;
};
type CreditPack = {
    id: string;
    kind: string;
    title: string;
    priceUsd: number;
    credits: number;
    modelTierMax: string;
    periodDays: number;
};
type Catalog = {
    packs: CreditPack[];
    /** Which payment rail is configured. For display only — never branch behaviour on it. */
    provider: string;
    /** The rail's own sentence about what confirming will do ("no card is charged", or later the
     *  real thing). Rendered verbatim so swapping the rail rewrites the disclosure itself. */
    paymentNote: string;
};
type Purchase = {
    ok: boolean;
    replayed: boolean;
    credits: number;
    priceUsd: number;
    creditsRemaining: number;
    /** The rail's own account of what it did — shown as-is on the receipt line. */
    paymentDetail: string;
    /**
     * Set ONLY when the rail could not finish in one request and the customer must go and pay.
     * Empty means the purchase is already done and the credits are already granted.
     *
     * A caller that follows this when present and shows the balance otherwise is correct on every
     * rail, without ever asking which one is configured — which is the rule the whole payments
     * module is built on.
     */
    checkoutUrl: string;
};
/** What the host has to answer before any of this can run. */
type BillingHost = {
    /** Base URL of the accounts service, no trailing slash. */
    accountsUrl(): Promise<string> | string;
    /** A CURRENT access token. Implementations refresh as needed; this must not return a stale one. */
    accessToken(): Promise<string> | string;
    /** Idempotency keys. Injected because `crypto.randomUUID` is unavailable on some hosts. */
    newKey(): string;
};

/**
 * "The balance probably changed" — one tiny bus, so nothing polls a money endpoint on a timer.
 *
 * A balance moves without the thing showing it doing anything that would re-render: a purchase on
 * the credits panel has to move the chip in the composer, and a message that spends credits has to
 * move both. The alternative is every consumer polling, which is a cost paid forever for an event
 * that is rare.
 *
 * Module-level on purpose. There is one balance per signed-in account per window, so a per-client
 * bus would just be the same set with more wiring.
 */
/** Subscribe to "the balance probably changed"; returns an unsubscribe. */
declare function onCreditsChanged(cb: () => void): () => void;
/** Announce a balance change. Called after any purchase; safe to call after any known debit. */
declare function notifyCreditsChanged(): void;

/**
 * BillingClient — read a balance, read the shelf, buy from it. The only code that talks money to
 * the accounts service.
 *
 * WHY IT IS A CLASS TAKING A HOST rather than four free functions. Three very different callers
 * need this: the agentd renderer (its own TokenManager, its own configured accounts URL), an agent
 * window (the SDK's `identity()` and a URL discovered from the daemon), and Agent Builder (both,
 * via the SDK). Every one of them answers "where is accounts" and "what is my token" differently,
 * and NONE of them differs in what a purchase is. Injecting those two answers is what lets the
 * third caller be free rather than a third implementation — the same argument that produced
 * `@agentd/auth`.
 *
 * IT BUYS THROUGH /me/checkout, NOT /me/purchase. `/me/checkout` is a strict superset: on a rail
 * that settles in place it returns the completed purchase, and on a card rail it returns a link to
 * go and pay. Building on it means an agent shipped today keeps working the day a real rail is
 * switched on, with no change to the agent.
 *
 * THE ONLY THING A CLIENT MAY SEND IS A product_id. Price and credit count are read server-side
 * from the products row — otherwise a user posts their own numbers and mints a fortune. That rule
 * is enforced by the server; it is repeated here so nobody "helpfully" adds an amount parameter.
 *
 * READS FAIL SOFT, THE PURCHASE FAILS LOUD. A balance that cannot be fetched renders as "unknown",
 * which is honest and harmless. A purchase that fails must reach the user with the server's own
 * words — silently resolving it would leave someone believing they had bought credits.
 */

declare class BillingClient {
    private readonly host;
    constructor(host: BillingHost);
    private base;
    private authed;
    /**
     * The balance, or null when there is nothing to show — not signed in, no accounts service, or
     * the request failed. Null is rendered as "unavailable" rather than as zero, because showing a
     * confident 0 to someone with credits is worse than admitting we do not know.
     */
    credits(agentId?: string): Promise<Credits | null>;
    /**
     * What is for sale. NOT signed-in-only and NOT hardcoded: the packs come from the `products`
     * table, whose prices derive from the markup dial, so changing what is on sale is a row in a
     * database and never a release of a client — and the price shown cannot drift from the price
     * charged, because there is only one of them.
     */
    catalog(kind?: string): Promise<Catalog | null>;
    /**
     * Buy a pack. THROWS with the server's own message on refusal.
     *
     * `returnUrl` is only consulted by a rail that sends the customer away; on one that settles in
     * place it is ignored, and the returned `checkoutUrl` is empty. Callers pass their own page so a
     * card payment comes back where it started.
     */
    buy(productId: string, returnUrl?: string): Promise<Purchase>;
}

/**
 * Credits, for an agent window — the data half. The UI half is `wallet.ts`.
 *
 * NOTHING NEW IS PLUMBED HERE. An agent window already knows who the user is (`identity()`, an
 * auto-refreshing TokenManager) and where the accounts service lives (`accountsUrl()`, answered by
 * the daemon it is served from). Those are exactly the two questions `BillingClient` asks its host,
 * so this file is the wiring and not an implementation — the implementation is `@agentd/billing`,
 * shared byte-for-byte with the agentd client so an agent and the desktop app cannot disagree
 * about what a purchase is.
 */

interface CreditsOptions extends DaemonOptions {
    client?: AgentdClient;
    storageKey?: string;
}
/** The host answers, for a page served by a daemon. */
declare function creditsHost(opts?: CreditsOptions): BillingHost;
/** A ready-to-use billing client for this window. */
declare function billing(opts?: CreditsOptions): BillingClient;

/**
 * The agent window's TokenManager — ONE per storage key, shared by everything in the page.
 *
 * The SDK used to carry its own sign-in and its own renewal loop, a second implementation of what
 * `clients/ui` already did. They drifted, as two copies of one job do: this one refused to renew a
 * token that had already expired, had no single-flight guard, and posted to a different endpoint.
 * A user signed into an agent window was quietly signed out ten minutes later.
 *
 * So there is no implementation here at all — only the three facts `@agentd/auth` cannot know
 * about an agent window:
 *
 *   * WHERE THE ACCOUNTS SERVICE IS. The window asks its own daemon (`/platform/status`) rather
 *     than reading a build-time env, because an agent is served by whichever daemon happens to be
 *     running it.
 *   * WHERE THE SESSION LIVES. Keyed per agent, so two agent windows on one origin never share or
 *     clobber each other's — see `sessionKey`.
 *   * WHAT TO DO WHEN THE CREDENTIAL CHANGES. Swapped onto the OPEN socket with `auth.update`, so
 *     a renewal never interrupts a run in flight. Reconnecting instead would drop it, which is the
 *     one thing a silent background renewal must never do.
 *
 * NO REFRESH TOKEN IS A NORMAL STATE. A window opened by the desktop app is handed an access token
 * on its launch URL and never receives a refresh token — deliberately, since an agent is
 * third-party code and that is a 30-day credential for the whole account. Such a window cannot
 * renew and is fed instead (`adopt`, driven by `acceptHostTokens`).
 */

interface IdentityOptions extends DaemonOptions {
    /** The connected client, so a new credential can reach the daemon at once. */
    client?: AgentdClient;
    /** Storage key override; defaults to one derived from the agent id in the page URL. */
    storageKey?: string;
}
/**
 * The storage key for THIS window.
 *
 * `?scope=` is present only when an OPENER built the url (the desktop app, a launch link). A page
 * reached from a marketplace card is just `/apps/<id>/`, so the path is the only thing that says
 * which agent this is — and without that fallback every such app on one origin shares the key
 * `agentd.session.app`, i.e. one agent's session silently becomes another's.
 */
declare function sessionKey(explicit?: string): string;
declare function identity(opts?: IdentityOptions): TokenManager;
/** Drop the memoised managers. Tests only — a page has exactly one lifetime. */
declare function resetIdentity(): void;

export { type AgentApp, type AgentEvent, type AgentInfo, AgentdClient, type AgentdClientOptions, type Attachment, type AuthConfig, type AuthOptions, type AuthState, BillingClient, type BillingHost, type CapabilityDescriptor, type Catalog, type ChatEventPayload, type ConnectInput, type ConnectTarget, type ConnectionStatus, type CreditPack, type Credits, type CreditsOptions, DEFAULT_TIMEOUT, type DaemonOptions, type EventFrame, type Frame, type Hello, type IdentityOptions, type InvokeResult, PROTOCOL_VERSION, type Purchase, type RequestFrame, type ResponseFrame, type RunMode, type SecretStore, type SendResult, type SessionRow, type SessionStore, type StoredSession, TokenManager, type TokenPair, acceptHostTokens, accessTokenAccount, accessTokenExpiry, accountsUrl, authLogin, authLogout, authRefresh, authStatus, billing, creditsHost, daemonOrigin, daemonToken, effectiveMode, fromPage, identity, loadMode, loadSession, localSessionStore, memorySessionStore, notifyCreditsChanged, onCreditsChanged, platformStatus, resetIdentity, resultText, saveMode, saveSession, sessionKey, setRunMode, startAuthRenewal, withTimeout };

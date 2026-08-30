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
    private rawRequest;
    private authRepair;
    /** Fetch a fresh access token and push it onto the open socket. True when the daemon took it.
     *  SINGLE-FLIGHT: ten rejected requests during one dead-token moment ride one repair. */
    private repairAuth;
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

/** When an access token dies, in epoch ms, from its own `exp`. 0 when unreadable. */
declare function accessTokenExpiry(token: string): number;
/** Which account an access token speaks for, from its `sub`. '' when unreadable. */
declare function accessTokenAccount(token: string): string;

/**
 * What THIS client knows about itself: which keys it wants to pay with, and (hosted only) the
 * borrowed token an opener handed it on the launch URL.
 *
 * THE CREDENTIAL STORY LEFT THIS FILE. On desktop the runtime is the only session holder
 * (platform_session.py); a window asks `GET /auth/token` and stores NOTHING — see identity.ts.
 * What remains here is genuinely per-window:
 *
 *   * RUN MODE — which keys pay for this client's model calls. A preference, not a credential,
 *     so localStorage is exactly right for it.
 *   * THE PAGE SESSION — on a HOSTED daemon there is no machine session to inherit, so a window
 *     opened by another app still adopts the access token from its launch URL. It now lives in
 *     module memory for the life of the page, never in storage: this window runs third-party
 *     code and must not persist a credential, and there is nothing that could renew it anyway.
 */

/**
 * A session as the rest of the SDK reads it.
 *
 * Kept in this shape because agent apps already destructure it. Only the hosted launch-URL path
 * produces one now; on desktop `loadSession` answers null and identity comes from the runtime.
 */
interface StoredSession {
    /** The ACCESS token — short-lived and the only one that travels on a connection. */
    token: string;
    email: string;
    accountId: string;
    /** Never present any more — windows do not hold refresh tokens. Kept for destructurers. */
    refreshToken?: string;
    /** Epoch ms when `token` expires. */
    expiresAt?: number;
}
/** 'local' = my own provider keys. 'cloud' = platform keys, metered to my account. */
type RunMode = 'local' | 'cloud';
/**
 * The session this page was HANDED, or null.
 *
 * SYNCHRONOUS, because the socket URL is built from it. Null on every desktop window — there
 * the daemon inherits the machine's identity for the connection and nothing travels at all.
 * Anything that needs a credential it can RELY on should await `identity().accessToken()`.
 */
declare function loadSession(_storageKey?: string): StoredSession | null;
/**
 * Adopt a launch-URL session (or clear it with null).
 *
 * The ONE legitimate caller is `fromPage` in client.ts. Everything else signs in through the
 * runtime (`authLogin`) and stores nothing here.
 */
declare function saveSession(value: StoredSession | null, _storageKey?: string): void;
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

interface AuthState {
    /** Does this daemon have an accounts service at all? (BYOK installs: no.) */
    available: boolean;
    signedIn: boolean;
    email: string;
    accountId: string;
    /** Which keys pay for THIS connection's model calls. */
    mode: RunMode;
    canUseCloud: boolean;
    /** Must somebody sign in before this app may run? The daemon's answer; `<Gate>` reads it. */
    required: boolean;
}
interface AuthOptions extends DaemonOptions {
    client?: AgentdClient;
    /** Accepted for compatibility; per-window sessions are gone. */
    storageKey?: string;
}
declare function authStatus(opts?: AuthOptions): Promise<AuthState>;
/**
 * Sign in, or create the account first when `signup`.
 *
 * REJECTS on a rejected credential, carrying the server's own message ("incorrect password") so
 * a form has something to show. A failed attempt must never resolve to `signedIn: false`: the
 * caller cannot tell that apart from "signed out", and the user is left looking at a form that
 * cleared itself.
 */
declare function authLogin(args: {
    email: string;
    password: string;
    signup?: boolean;
}, opts?: AuthOptions): Promise<AuthState>;
/** Forget the MACHINE's session. Every window on this daemon signs out together — identity is a
 *  fact about the machine now, not about a window. */
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
    /** The organization whose pool this balance IS, when fundingSource is 'org_pool'. An account
     *  in an org has no personal wallet — the server decides the pocket from membership. */
    orgId: string;
    /** This member's seat allowance is spent for the month. The pool may hold plenty. */
    memberCapped: boolean;
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
    /** Seats one purchase adds — set on `seat_subscription` products, 0 on credit packs. */
    seats: number;
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
    buy(productId: string, returnUrl?: string, orgId?: string): Promise<Purchase>;
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
 * Organizations and seats, for an agent window.
 *
 * COPIED FROM clients/ui/src/lib/orgs.ts — the assistant's own client, function for function. An
 * enterprise buys seats once; an agent must not have a second, subtly different idea of what a
 * seat is, who may mint an invite, or what "joinable" means.
 *
 * NOTHING NEW IS PLUMBED HERE, exactly as in `credits.ts`. A window already knows who the user is
 * (`identity()`, an auto-refreshing TokenManager) and where the accounts service lives
 * (`accountsUrl()`, answered by the daemon that served the page). Those are the only two things
 * these calls need.
 *
 * EVERY ANSWER IS ALREADY SCOPED SERVER-SIDE by membership — the API fails closed with a 404 for
 * an org you are not in — so nothing here filters. It renders what the server says the caller may
 * see. A client that decided for itself which orgs to show would be a second, weaker copy of an
 * access rule that is already enforced where it matters.
 *
 * THE DAEMON IS NOT INVOLVED. Org membership reaches it through the access token's own `orgs`
 * claim, not through anything this module does.
 */

interface OrgOptions extends DaemonOptions {
    client?: AgentdClient;
    storageKey?: string;
}
type OrgMembership = {
    id: string;
    name: string;
    role: string;
};
type JoinableOrg = {
    id: string;
    name: string;
};
type MyOrgs = {
    orgs: OrgMembership[];
    joinable: JoinableOrg[];
};
type OrgMember = {
    accountId: string;
    email: string;
    role: string;
    monthlyCreditCap: number;
    addedAt: number;
};
type OrgDetail = {
    id: string;
    name: string;
    role: string;
    seatsTotal: number;
    seatsUsed: number;
    createdAt: number;
    /** admin-view extras — absent for a plain member, exactly as the server withholds them */
    members?: OrgMember[];
    domains?: string[];
    primaryOwner?: string;
    poolCreditsRemaining?: number;
};
type OrgUsageRow = {
    accountId: string;
    email: string;
    credits: number;
    costUsd: number;
    calls: number;
    monthlyCreditCap: number;
};
type OrgInvite = {
    inviteToken: string;
    orgId: string;
    orgName: string;
    email: string;
    role: string;
    expiresAt: number;
};
/** My orgs + my role, and the ones my email domain would let me join. */
declare function fetchMyOrgs(opts?: OrgOptions): Promise<MyOrgs>;
declare function createOrg(name: string, seatsTotal?: number, opts?: OrgOptions): Promise<OrgDetail>;
/** Join by invite token OR by the domain offer (an org id from `joinable`). */
declare function joinOrg(input: {
    inviteToken?: string;
    orgId?: string;
}, opts?: OrgOptions): Promise<OrgDetail>;
declare function fetchOrgDetail(orgId: string, opts?: OrgOptions): Promise<OrgDetail>;
declare function mintInvite(orgId: string, input?: {
    email?: string;
    role?: string;
}, opts?: OrgOptions): Promise<OrgInvite>;
/** Role change / monthly cap / remove (`active: false`) — org admin and up, server-enforced. */
declare function updateMember(orgId: string, accountId: string, patch: {
    role?: string;
    monthlyCreditCap?: number;
    active?: boolean;
}, opts?: OrgOptions): Promise<OrgDetail>;
declare function updateDomain(orgId: string, domain: string, remove?: boolean, opts?: OrgOptions): Promise<OrgDetail>;
declare function fetchOrgUsage(orgId: string, opts?: OrgOptions): Promise<{
    month: string;
    members: OrgUsageRow[];
}>;

interface IdentityOptions extends DaemonOptions {
    /** BINDS the client for token pushes: whenever this fetcher obtains a FRESH cookie token
     *  (hosted), it fires `auth.update` onto every bound client's open socket — the handoff that
     *  keeps a long-lived connection, and the run already in flight on it, paying with a live
     *  token. No timers: the push rides whatever ask fetched the token (status polls, credits). */
    client?: AgentdClient;
    /** Accepted for compatibility; windows no longer have per-window sessions to key. */
    storageKey?: string;
}
/** The typed answer from the runtime — see the module note for the four states. */
interface TokenAnswer {
    state: 'ok' | 'signed_out' | 'session_expired' | 'accounts_unreachable';
    accessToken?: string;
    expiresAt?: number;
    email?: string;
    accountId?: string;
    retryAfterSec?: number;
    /** Who answered: the runtime (desktop) or the accounts cookie session (hosted). A window
     *  uses this to know whether it must PRESENT the token on its socket — on desktop the daemon
     *  inherits the machine identity and nothing travels. */
    via?: 'runtime' | 'cookie';
}
/** Build a runtime /auth/* URL. The MACHINE TOKEN rides along (`?token=`, same slot every
 *  other daemon HTTP call uses) because the runtime requires it where one is configured — it is
 *  what keeps a hostile web page from driving these endpoints blind. Every window has it on its
 *  own launch URL. */
declare function authUrl(path: string, opts?: DaemonOptions): URL;
/** Ask the runtime for the machine's token state. The ONE identity read everything builds on.
 *  On a hosted daemon the runtime answers 404 — no machine session exists there — and the ask
 *  falls through to the accounts cookie (see fetchCookieToken). */
declare function fetchToken(opts?: DaemonOptions): Promise<TokenAnswer>;
/** The thin per-window fetcher behind `identity()`. Caches the token in memory only, and only
 *  until near expiry — the runtime does all real work, so "cache" here just saves HTTP chatter
 *  between keystrokes. */
declare class TokenFetcher {
    private readonly opts;
    private answer;
    private inflight;
    private readonly clients;
    constructor(opts: DaemonOptions);
    /** Register a client to receive `auth.update` pushes. Idempotent. */
    bind(client: AgentdClient): void;
    /** A current access token, or '' when the machine is signed out / unreachable. Callers that
     *  need to know WHY ask `state()`. */
    accessToken(): Promise<string>;
    state(): Promise<TokenAnswer>;
    /** THE HANDOFF. A hosted connection's identity is the token it presented — a snapshot the
     *  daemon cannot renew (it holds no refresh token for this user; the browser's cookie does).
     *  So when a genuinely NEW cookie token arrives, every bound open socket gets it via
     *  `auth.update`, which the daemon applies to the connection AND to the turn already running
     *  on it. Desktop answers come via the runtime, which renews its own connections — no push.
     *  Fire-and-forget: a socket that is closed or an older daemon just ignores it. */
    private push;
    signedIn(): boolean;
    /** Compatibility shape for callers that read `current()?.email`. */
    current(): {
        email: string;
        accountId: string;
    } | null;
}
/** The window's identity handle. One per daemon origin; all state lives in the runtime. */
declare function identity(opts?: IdentityOptions): TokenFetcher;
/** TEST SEAM: forget cached answers (a signed-out test must not see the last test's token). */
declare function resetIdentity(): void;
/** DEAD: windows have no per-window sessions to key any more. Returns a stable string for any
 *  caller still using it as a cache key. */
declare function sessionKey(explicit?: string): string;
/** DEAD: openers no longer push tokens down — every window asks the runtime itself. */
declare function acceptHostTokens(): () => void;
/** DEAD: there is nothing to renew in a window. The runtime renews, lazily, when asked. */
declare function startAuthRenewal(): () => void;

export { type AgentApp, type AgentEvent, type AgentInfo, AgentdClient, type AgentdClientOptions, type Attachment, type AuthOptions, type AuthState, BillingClient, type BillingHost, type CapabilityDescriptor, type Catalog, type ChatEventPayload, type ConnectInput, type ConnectTarget, type ConnectionStatus, type CreditPack, type Credits, type CreditsOptions, DEFAULT_TIMEOUT, type DaemonOptions, type EventFrame, type Frame, type Hello, type IdentityOptions, type InvokeResult, type JoinableOrg, type MyOrgs, type OrgDetail, type OrgInvite, type OrgMember, type OrgMembership, type OrgOptions, type OrgUsageRow, PROTOCOL_VERSION, type Purchase, type RequestFrame, type ResponseFrame, type RunMode, type SendResult, type SessionRow, type StoredSession, type TokenAnswer, acceptHostTokens, accessTokenAccount, accessTokenExpiry, accountsUrl, authLogin, authLogout, authStatus, authUrl, billing, createOrg, creditsHost, daemonOrigin, daemonToken, effectiveMode, fetchMyOrgs, fetchOrgDetail, fetchOrgUsage, fetchToken, fromPage, identity, joinOrg, loadMode, loadSession, mintInvite, notifyCreditsChanged, onCreditsChanged, platformStatus, resetIdentity, resultText, saveMode, saveSession, sessionKey, setRunMode, startAuthRenewal, updateDomain, updateMember, withTimeout };

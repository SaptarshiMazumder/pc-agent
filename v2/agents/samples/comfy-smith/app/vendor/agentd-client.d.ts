import { BillingClient, BillingHost } from '@agentd/billing';
export { BillingClient, BillingHost, Catalog, CreditPack, Credits, Purchase, notifyCreditsChanged, onCreditsChanged } from '@agentd/billing';
import { TokenManager } from '@agentd/auth';
export { AuthConfig, SecretStore, SessionStore, TokenManager, TokenPair, accessTokenAccount, accessTokenExpiry, localSessionStore, memorySessionStore } from '@agentd/auth';

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
 * The sign-in GATE — the UI half of sign-in (mechanism lives in auth.ts).
 *
 *   await agentd.mountSignInGate()
 *   // past this line somebody is signed in, or this install has no accounts to sign in to
 *
 * No arguments needed: the heading comes from the page's own <title>, which is already this
 * agent's name. Naming a product here would be a second copy of it to keep in sync.
 *
 * ONE LINE, and it is deliberately a blocking await: an app that renders its composer first and
 * signs in later has to handle "signed in yet?" at every send site.
 *
 * IT RENDERS NOTHING in exactly two cases:
 *   - this daemon has no accounts service configured, so there is nothing to sign in to
 *   - somebody is already signed in
 *
 * `require: true` removes the FIRST of those. A product that cannot run as nobody says so, and
 * then a daemon with no accounts service gets an explanation in the window instead of an app
 * running signed out. Its caller reads `signedIn` on the result rather than assuming one.
 *
 * THAT LIST USED TO BE LONGER, AND WRONG. It also skipped the gate whenever the platform's keys
 * were already paying for model calls — because this was never a login, it was a checkout screen
 * ("Runs on our servers — no API keys to set up"). On a BYOK install nobody is paying, so the
 * gate concluded there was nothing to ask and rendered nothing, forever. Signing in and paying
 * are now separate questions; this one only asks who you are.
 *
 * ELEMENT IDS ARE PART OF THE CONTRACT. `gate`, `gateForm`, `gateEmail`, `gatePass` match what
 * figure-creator's hand-written gate used, because the desktop shell's AGENTD_E2E_LOGIN hook
 * drives those ids to test a packaged build with no human at the keyboard. Renaming them would
 * silently disable that test — it would fill nothing and still report success.
 *
 * STYLING is injected once and driven by CSS custom properties, so an agent themes it from its
 * own stylesheet (`--gate-accent`, `--gate-bg`, …) instead of forking the markup. Values fall
 * back to the surrounding page's, so an unthemed agent still looks like itself.
 */

interface SignInGateOptions extends AuthOptions {
    /** Product name in the heading. Defaults to the document title, then the agent id. */
    product?: string;
    /** One line under the heading explaining WHY a sign-in is being asked for. */
    blurb?: string;
    /** Where to attach. Defaults to document.body. */
    mount?: HTMLElement;
    /** Allow account creation from the gate (default true). Set false for invite-only products. */
    allowSignup?: boolean;
    /**
     * THIS PRODUCT demands an identity, whatever the deployment would settle for.
     *
     * `AuthState.required` is the DAEMON's answer to "must anyone sign in here", and it is false on
     * every desktop install — the machine token already authorises that window, so the gate steps
     * aside. An agent whose every run costs somebody money, or writes into somebody's workspace,
     * cannot let the deployment decide that: the same package has to behave identically on a laptop
     * and on the hosted daemon, and "who is this?" is the agent's question, not the host's.
     *
     * Set here rather than inferred from `[app] mode` or from being hosted, because an agent that
     * needs an account needs it for reasons only the agent knows.
     *
     * Callers must then read `signedIn` on the result: with this set, a resolved promise means the
     * gate is finished, NOT that somebody is behind it (see the no-accounts-service case below).
     */
    require?: boolean;
}
interface GateResult extends AuthState {
    /** true when a gate was actually displayed and the user completed it. */
    signedInHere: boolean;
}
declare function mountSignInGate(options?: SignInGateOptions): Promise<GateResult>;
/** Sign out and show the gate again. Convenience for an app with a Sign-out control. */
declare function signOutAndGate(options?: SignInGateOptions): Promise<GateResult>;

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
 * Credits & billing, as a panel any agent window can mount.
 *
 *   await agentd.mountCreditsPanel({ client, mount: someElement })
 *
 * THE SAME SCREEN EVERY AGENT SHOWS, because it is the same code every agent runs. It ships inside
 * the SDK — like the sign-in gate in `gate.ts` — and `npm run build` re-vendors it into every
 * agent's `ui/vendor/agentd-client.js`. A copy under templates/ would put a second version of the
 * shop in one product, and the copy could then disagree with the accounts service it buys from.
 * That is the whole reason this is not a snippet the model writes per agent.
 *
 * LAID OUT TO MATCH the agentd renderer's Credits & billing page (SubscriptionView.tsx): balance
 * card, buy-credits grid, the rail's own disclosure, then the receipt. Same information in the
 * same order, so a user who tops up in the desktop app and then inside an agent sees one product.
 *
 * WHAT IT DOES NOT DECIDE. Not the packs (GET /products — a database row, so what is on sale
 * changes without releasing a client), not the prices (same), and not the disclosure sentence
 * (`paymentNote`, the rail's own words — so wiring up a real rail rewrites this panel's promises
 * instead of leaving a stale "no card is charged" note on a screen that now charges).
 *
 * IT RENDERS NOTHING WHEN THERE IS NOTHING TO SELL: no accounts service (a BYOK build), or nobody
 * signed in. Safe to call unconditionally, which is what makes it a component and not a decision.
 */

interface CreditsPanelOptions extends CreditsOptions {
    /** Where to render. Defaults to `#agentd-credits` if present, else appended to <body>. */
    mount?: HTMLElement;
    /** Scope the balance to one agent's subscription pocket. Defaults to the platform balance. */
    agentId?: string;
    /** Where a card rail should return the customer. Defaults to this page. */
    returnUrl?: string;
}
interface CreditsPanelHandle {
    /** Re-read the balance from the server. */
    refresh(): Promise<void>;
    /** Remove the panel and stop listening. */
    destroy(): void;
    /** Did it render anything? False on a BYOK build or when nobody is signed in. */
    shown: boolean;
}
/**
 * Mount the panel. Resolves once it has drawn, or decided not to.
 *
 * Never rejects for an ordinary refusal — a failed purchase is reported INSIDE the panel, where
 * the user can read it and try again. A daemon that cannot be reached resolves to a panel that
 * drew nothing, because the app's own status chip already reports that and a second alarm for one
 * fault is noise.
 */
declare function mountCreditsPanel(options?: CreditsPanelOptions): Promise<CreditsPanelHandle>;

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

export { type AgentApp, type AgentEvent, type AgentInfo, AgentdClient, type AgentdClientOptions, type Attachment, type AuthOptions, type AuthState, type CapabilityDescriptor, type ChatEventPayload, type ConnectInput, type ConnectTarget, type ConnectionStatus, type CreditsOptions, type CreditsPanelHandle, type CreditsPanelOptions, DEFAULT_TIMEOUT, type DaemonOptions, type EventFrame, type Frame, type GateResult, type Hello, type IdentityOptions, type InvokeResult, PROTOCOL_VERSION, type RequestFrame, type ResponseFrame, type RunMode, type SendResult, type SessionRow, type SignInGateOptions, type StoredSession, acceptHostTokens, accountsUrl, authLogin, authLogout, authRefresh, authStatus, billing, creditsHost, daemonOrigin, daemonToken, effectiveMode, fromPage, identity, loadMode, loadSession, mountCreditsPanel, mountSignInGate, platformStatus, resetIdentity, resultText, saveMode, saveSession, sessionKey, setRunMode, signOutAndGate, startAuthRenewal, withTimeout };

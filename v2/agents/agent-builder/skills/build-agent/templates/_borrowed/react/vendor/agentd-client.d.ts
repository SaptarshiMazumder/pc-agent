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
    private reconnectDelay;
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
 * A leaf — it imports nothing, so both the socket (client.ts) and the sign-in flow (auth.ts) can
 * read it without forming a cycle.
 *
 * THE CLIENT HOLDS BOTH FACTS. The daemon stores neither. That is not a stylistic choice: a
 * daemon-side session is ONE slot, and one slot cannot serve two people — the second to sign in
 * overwrites the first, signing out signs out everybody, and one window's Cloud switch moves
 * every other window's billing. Held per client and presented per connection, a hundred users on
 * one daemon is a hundred sockets with a hundred answers.
 *
 * Keyed per agent, so two agent apps on one machine never share or clobber each other's.
 */
interface StoredSession {
    token: string;
    email: string;
    accountId: string;
}
/** 'local' = my own provider keys. 'cloud' = platform keys, metered to my account. */
type RunMode = 'local' | 'cloud';
declare function loadSession(storageKey?: string): StoredSession | null;
declare function saveSession(value: StoredSession | null, storageKey?: string): void;
declare function loadMode(storageKey?: string): RunMode | null;
/** null clears the choice, returning this client to the default (cloud when it has a session). */
declare function saveMode(value: RunMode | null, storageKey?: string): void;
/**
 * The mode this client should run in: what it CHOSE, else the default.
 *
 * ONE PLACE, because two readers need the same answer and a disagreement between them is
 * invisible: the settings page renders it, and the socket sends it. If the page defaulted to
 * cloud while the connect URL sent nothing, the UI would promise platform keys while the calls
 * went out on the user's own.
 *
 * Default is CLOUD once signed in — and only where there is a proxy to reach.
 */
declare function effectiveMode(storageKey?: string, signedIn?: boolean, canUseCloud?: boolean): RunMode;

/**
 * Sign-in — ORDINARY HTTP, from the client, exactly like any web app.
 *
 *   GET  <daemon>/platform/status     → where the accounts service is
 *   POST <accountsUrl>/signup         (only when creating)
 *   POST <accountsUrl>/login          → a session token
 *   store it, reconnect
 *
 * The daemon is not in the middle of this. It answers one question — "where do people sign in?" —
 * and is then told the answer on the next connection.
 *
 * WHY NOT THROUGH THE DAEMON. It was, briefly: three socket methods, with the daemon performing
 * the exchange and keeping the token. That put ONE session on the machine, and one session cannot
 * serve two people — the second to sign in overwrote the first, signing out signed out everybody,
 * and any way to read the token back handed one user another's credential. Routing it through a
 * socket bought nothing this does not, and cost that.
 *
 * SO THE CLIENT DECIDES BOTH FACTS: who it is, and which keys pay. Both travel on the connection
 * (`?session=`, `?mode=`), which is why a hundred users on one daemon is a hundred sockets each
 * answering for itself.
 *
 * CHANGING EITHER RECONNECTS. The daemon reads them when the socket opens, so a sign-in that did
 * not reconnect would leave it still seeing the old answer.
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
}
interface AuthOptions {
    /** The connected client, so a change can reconnect it and take effect at once. */
    client?: AgentdClient;
    /** Daemon HTTP origin. Defaults to the page's own — an agent app is served BY the daemon. */
    origin?: string;
    /** The daemon's bearer token. Defaults to `?token=` on the page URL. */
    token?: string;
    timeoutMs?: number;
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
}
interface GateResult extends AuthState {
    /** true when a gate was actually displayed and the user completed it. */
    signedInHere: boolean;
}
/**
 * Sign the user in, showing a form only if one is needed.
 *
 * Resolves once the app may proceed. Rejects only if the daemon itself cannot be reached — a
 * wrong password does not reject, it is reported in the form and the user tries again.
 */
declare function mountSignInGate(options?: SignInGateOptions): Promise<GateResult>;
/** Sign out and show the gate again. Convenience for an app with a Sign-out control. */
declare function signOutAndGate(options?: SignInGateOptions): Promise<GateResult>;

export { type AgentApp, type AgentEvent, type AgentInfo, AgentdClient, type AgentdClientOptions, type Attachment, type AuthOptions, type AuthState, type CapabilityDescriptor, type ChatEventPayload, type ConnectInput, type ConnectTarget, type ConnectionStatus, type EventFrame, type Frame, type GateResult, type Hello, type InvokeResult, PROTOCOL_VERSION, type RequestFrame, type ResponseFrame, type RunMode, type SendResult, type SessionRow, type SignInGateOptions, type StoredSession, authLogin, authLogout, authStatus, effectiveMode, fromPage, loadMode, loadSession, mountSignInGate, resultText, saveMode, saveSession, setRunMode, signOutAndGate };

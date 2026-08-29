// src/protocol.ts
var PROTOCOL_VERSION = 1;
function resultText(result) {
  if (result && typeof result === "object") {
    const content = result.content;
    if (Array.isArray(content)) {
      return content.map((block) => block && typeof block === "object" ? block.text || "" : "").join("").trim();
    }
    return String(result.text || "").trim();
  }
  return String(result ?? "").trim();
}

// ../auth/src/claims.ts
function claims(token) {
  try {
    const body = (token || "").split(".")[1];
    if (!body) return null;
    return JSON.parse(atob(body.replace(/-/g, "+").replace(/_/g, "/")));
  } catch {
    return null;
  }
}
function accessTokenExpiry(token) {
  const exp = Number(claims(token)?.exp || 0);
  return exp > 0 ? exp * 1e3 : 0;
}
function accessTokenAccount(token) {
  return String(claims(token)?.sub || "");
}
function accessTokenEmail(token) {
  return String(claims(token)?.email || "");
}

// src/platform-status.ts
var DEFAULT_TIMEOUT = 45e3;
function daemonOrigin(opts) {
  if (opts.origin) return opts.origin.replace(/\/$/, "");
  if (typeof location === "undefined") throw new Error("no origin: pass options.origin");
  return location.origin;
}
function daemonToken(opts) {
  if (typeof opts.token === "string") return opts.token;
  if (typeof location === "undefined") return "";
  try {
    return new URL(location.href).searchParams.get("token") || "";
  } catch {
    return "";
  }
}
async function withTimeout(p, ms, what) {
  let timer;
  const guard = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} timed out after ${ms}ms`)), ms);
  });
  try {
    return await Promise.race([p, guard]);
  } finally {
    clearTimeout(timer);
  }
}
async function platformStatus(opts) {
  const u = new URL("/platform/status", `${daemonOrigin(opts)}/`);
  const token = daemonToken(opts);
  if (token) u.searchParams.set("token", token);
  const r = await withTimeout(
    fetch(u.toString(), { cache: "no-store" }),
    opts.timeoutMs ?? DEFAULT_TIMEOUT,
    "platform status"
  );
  if (!r.ok) throw new Error(`platform status failed (HTTP ${r.status})`);
  return await r.json();
}
async function accountsUrl(opts) {
  const status = await platformStatus(opts);
  return String(status.accountsUrl || "").replace(/\/$/, "");
}

// src/identity.ts
function authUrl(path, opts = {}) {
  const u = new URL(path, `${daemonOrigin(opts)}/`);
  const token = daemonToken(opts);
  if (token) u.searchParams.set("token", token);
  return u;
}
async function fetchCookieToken(opts) {
  let base = "";
  try {
    base = String((await platformStatus(opts)).accountsUrl || "").replace(/\/$/, "");
  } catch {
    return { state: "accounts_unreachable", retryAfterSec: 15, via: "cookie" };
  }
  if (!base) return { state: "signed_out", via: "cookie" };
  let r;
  try {
    r = await fetch(`${base}/auth/refresh`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      credentials: "include",
      cache: "no-store",
      body: JSON.stringify({ cookie: true, client_id: "agent-window" })
    });
  } catch {
    return { state: "accounts_unreachable", retryAfterSec: 15, via: "cookie" };
  }
  if (r.status === 401 || r.status === 403) return { state: "signed_out", via: "cookie" };
  if (!r.ok) return { state: "accounts_unreachable", retryAfterSec: 30, via: "cookie" };
  const d = await r.json().catch(() => ({}));
  if (!d.access_token) return { state: "signed_out", via: "cookie" };
  return {
    state: "ok",
    accessToken: d.access_token,
    expiresAt: Date.now() / 1e3 + Number(d.expires_in || 0),
    accountId: String(d.account_id || ""),
    email: String(d.email || ""),
    via: "cookie"
  };
}
async function fetchToken(opts = {}) {
  try {
    const r = await fetch(authUrl("/auth/token", opts), {
      cache: "no-store"
    });
    if (r.status === 404) return fetchCookieToken(opts);
    const d = await r.json().catch(() => ({}));
    if (d && typeof d.state === "string") return { ...d, via: "runtime" };
    return { state: r.ok ? "ok" : "signed_out", via: "runtime" };
  } catch {
    return { state: "accounts_unreachable", retryAfterSec: 15 };
  }
}
var TokenFetcher = class {
  constructor(opts) {
    this.opts = opts;
    this.answer = null;
    this.inflight = null;
    this.clients = /* @__PURE__ */ new Set();
  }
  /** Register a client to receive `auth.update` pushes. Idempotent. */
  bind(client) {
    this.clients.add(client);
  }
  /** A current access token, or '' when the machine is signed out / unreachable. Callers that
   *  need to know WHY ask `state()`. */
  async accessToken() {
    const a = await this.state();
    return a.state === "ok" ? a.accessToken || "" : "";
  }
  async state() {
    const held = this.answer;
    if (held?.state === "ok" && (held.expiresAt || 0) * 1e3 - Date.now() > 15e4) return held;
    if (held?.state === "ok" && (held.expiresAt || 0) - Date.now() / 1e3 > 150) return held;
    if (!this.inflight) {
      this.inflight = fetchToken(this.opts).finally(() => {
        this.inflight = null;
      });
      this.inflight.then((a) => {
        const prev = this.answer;
        this.answer = a;
        this.push(a, prev);
      });
    }
    return this.inflight;
  }
  /** THE HANDOFF. A hosted connection's identity is the token it presented — a snapshot the
   *  daemon cannot renew (it holds no refresh token for this user; the browser's cookie does).
   *  So when a genuinely NEW cookie token arrives, every bound open socket gets it via
   *  `auth.update`, which the daemon applies to the connection AND to the turn already running
   *  on it. Desktop answers come via the runtime, which renews its own connections — no push.
   *  Fire-and-forget: a socket that is closed or an older daemon just ignores it. */
  push(a, prev) {
    if (a.state !== "ok" || a.via !== "cookie" || !a.accessToken) return;
    if (prev?.state === "ok" && prev.accessToken === a.accessToken) return;
    for (const c of this.clients) {
      void c.request("auth.update", { accessToken: a.accessToken }).catch(() => {
      });
    }
  }
  signedIn() {
    return this.answer?.state === "ok";
  }
  /** Compatibility shape for callers that read `current()?.email`. */
  current() {
    const a = this.answer;
    return a?.state === "ok" ? { email: a.email || "", accountId: a.accountId || "" } : null;
  }
};
var fetchers = /* @__PURE__ */ new Map();
function identity(opts = {}) {
  const key = daemonOrigin(opts);
  let f = fetchers.get(key);
  if (!f) {
    f = new TokenFetcher(opts);
    fetchers.set(key, f);
  }
  if (opts.client) f.bind(opts.client);
  return f;
}
function resetIdentity() {
  fetchers.clear();
}
function sessionKey(explicit = "") {
  return explicit || "agentd.session.machine";
}
function acceptHostTokens() {
  return () => void 0;
}
function startAuthRenewal() {
  return () => void 0;
}

// src/session.ts
var pageSession = null;
function loadSession(_storageKey = "") {
  const s = pageSession;
  if (!s) return null;
  if (s.expiresAt && s.expiresAt <= Date.now()) return null;
  return s;
}
function saveSession(value, _storageKey = "") {
  if (!value) {
    pageSession = null;
    return;
  }
  pageSession = {
    token: value.token,
    // The token's own claims fill what the opener did not say — a launch URL carries the token
    // and nothing else, and these blanks are what made opened windows render "Account" unnamed.
    email: value.email || accessTokenEmail(value.token),
    accountId: value.accountId || accessTokenAccount(value.token),
    expiresAt: value.expiresAt || accessTokenExpiry(value.token) || void 0
  };
}
function loadMode(storageKey = "") {
  try {
    const v = localStorage.getItem(sessionKey(storageKey) + ".mode");
    return v === "local" || v === "cloud" ? v : null;
  } catch {
    return null;
  }
}
function saveMode(value, storageKey = "") {
  try {
    if (value) localStorage.setItem(sessionKey(storageKey) + ".mode", value);
    else localStorage.removeItem(sessionKey(storageKey) + ".mode");
  } catch {
  }
}
function effectiveMode(storageKey = "", signedIn = false, canUseCloud = true) {
  const chosen = loadMode(storageKey);
  if (chosen) return chosen;
  return signedIn && canUseCloud ? "cloud" : "local";
}

// src/client.ts
function toWsUrl(target) {
  const u = new URL(target.url);
  if (u.protocol === "http:") u.protocol = "ws:";
  if (u.protocol === "https:") u.protocol = "wss:";
  if (target.token) u.searchParams.set("token", target.token);
  if (target.session) u.searchParams.set("session", target.session);
  if (target.mode) u.searchParams.set("mode", target.mode);
  if (target.scope) u.searchParams.set("scope", target.scope);
  return u.toString();
}
function toHttpOrigin(wsUrl) {
  const u = new URL(wsUrl);
  u.protocol = u.protocol === "wss:" ? "https:" : "http:";
  u.search = "";
  u.pathname = "";
  return u.origin;
}
var _AgentdClient = class _AgentdClient {
  constructor(options = {}) {
    this.ws = null;
    this.input = null;
    this.nextId = 1;
    this.pending = /* @__PURE__ */ new Map();
    this.eventHandlers = /* @__PURE__ */ new Map();
    this.statusHandlers = /* @__PURE__ */ new Set();
    /** The last status announced — see `onStatus` for why this is remembered. */
    this.status = "connecting";
    this.reconnectDelay = 1e3;
    /** When the current socket opened, so "did this connection actually work?" can be answered. */
    this.openedAt = 0;
    this.closedByUs = false;
    this.lastTarget = null;
    this.authRepair = null;
    this.clientName = options.clientName || `@agentd/client/${PROTOCOL_VERSION}`;
  }
  /** Connect (or switch) to a daemon. Reconnects automatically with backoff until close(). */
  connect(input) {
    this.input = input;
    this.closedByUs = false;
    void this.open();
  }
  close() {
    this.closedByUs = true;
    this.teardownSocket();
  }
  /** Re-open the socket, re-reading the target.
   *
   *  Identity and run mode are read by the daemon when a connection OPENS, so changing either
   *  has to bring up a new one — otherwise the daemon goes on answering as whoever this client
   *  was before. Called by authLogin / authLogout / setRunMode. */
  reconnect() {
    if (!this.input) return;
    this.closedByUs = false;
    void this.open();
  }
  get connected() {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }
  scheduleReconnect() {
    if (this.closedByUs) return;
    setTimeout(() => void this.open(), this.reconnectDelay);
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, 15e3);
  }
  async open() {
    if (!this.input) return;
    this.teardownSocket();
    this.status = "connecting";
    for (const handler of this.statusHandlers) handler("connecting");
    let target;
    try {
      target = typeof this.input === "function" ? await this.input() : this.input;
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.lastTarget = target;
    const ws = new WebSocket(toWsUrl(target));
    this.teardownSocket();
    this.ws = ws;
    ws.onopen = () => {
      this.openedAt = Date.now();
      this.status = "open";
      for (const handler of this.statusHandlers) handler("open");
    };
    ws.onmessage = (message) => this.handleFrame(JSON.parse(message.data));
    ws.onclose = (event) => {
      for (const [, pending] of this.pending) pending.reject(new Error("connection closed"));
      this.pending.clear();
      this.status = "closed";
      for (const handler of this.statusHandlers) handler("closed");
      const lived = this.openedAt ? Date.now() - this.openedAt : 0;
      this.openedAt = 0;
      if (event && event.code === 4401) {
        this.reconnectDelay = _AgentdClient.UNAUTHORIZED_DELAY;
      } else if (lived >= _AgentdClient.HEALTHY_MS) {
        this.reconnectDelay = 1e3;
      }
      this.scheduleReconnect();
    };
  }
  /** Detach + close the current socket without triggering its reconnect. */
  teardownSocket() {
    const old = this.ws;
    if (!old) return;
    this.ws = null;
    old.onopen = null;
    old.onmessage = null;
    old.onclose = null;
    old.onerror = null;
    try {
      old.close();
    } catch {
    }
  }
  // ------------------------------------------------------------------ raw protocol
  request(method, params = {}) {
    return this.rawRequest(method, params).catch(async (e) => {
      const code = e?.code;
      if (code !== "auth_expired" || method === "auth.update") throw e;
      if (!await this.repairAuth()) throw e;
      return this.rawRequest(method, params);
    });
  }
  rawRequest(method, params = {}) {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("not connected"));
    }
    const id = String(this.nextId++);
    const frame = { type: "req", id, method, params };
    ws.send(JSON.stringify(frame));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
  }
  /** Fetch a fresh access token and push it onto the open socket. True when the daemon took it.
   *  SINGLE-FLIGHT: ten rejected requests during one dead-token moment ride one repair. */
  repairAuth() {
    if (!this.authRepair) {
      this.authRepair = (async () => {
        try {
          const a = await identity().state();
          if (a.state !== "ok" || !a.accessToken) return false;
          await this.rawRequest("auth.update", { accessToken: a.accessToken });
          return true;
        } catch {
          return false;
        } finally {
          this.authRepair = null;
        }
      })();
    }
    return this.authRepair;
  }
  /** Subscribe to a broadcast event by name. Returns the unsubscribe. */
  on(event, handler) {
    if (!this.eventHandlers.has(event)) this.eventHandlers.set(event, /* @__PURE__ */ new Set());
    this.eventHandlers.get(event).add(handler);
    return () => this.eventHandlers.get(event)?.delete(handler);
  }
  /**
   * Subscribe to connection status. Returns the unsubscribe.
   *
   * THE CURRENT STATUS ARRIVES IMMEDIATELY, before this returns. Status was transitions-only, and
   * a subscriber that mounted after the socket opened — which is most of them, since connecting
   * starts at construction and React mounts a frame later — heard nothing until the next
   * reconnect. The symptom is a composer that says "connecting…" and refuses to send over a
   * perfectly open socket.
   */
  onStatus(handler) {
    this.statusHandlers.add(handler);
    handler(this.status);
    return () => this.statusHandlers.delete(handler);
  }
  handleFrame(frame) {
    if (frame.type === "res") {
      const pending = this.pending.get(frame.id);
      if (!pending) return;
      this.pending.delete(frame.id);
      if (frame.ok) pending.resolve(frame.payload || {});
      else {
        const err = new Error(String(frame.payload?.error || "gateway error"));
        if (typeof frame.payload?.code === "string") err.code = frame.payload.code;
        pending.reject(err);
      }
    } else if (frame.type === "event") {
      for (const handler of this.eventHandlers.get(frame.event) || []) {
        handler(frame.payload || {});
      }
    }
  }
  // ------------------------------------------------------------------ typed helpers
  /** Handshake — introduces this client + its protocol so the server can flag compatibility. */
  hello() {
    return this.request("hello", { protocol: PROTOCOL_VERSION, client: this.clientName });
  }
  async agents() {
    return this.request("agents.list");
  }
  agentDetail(agentId) {
    return this.request("agents.detail", { agentId });
  }
  sessions(agentId) {
    return this.request("sessions.list", agentId ? { agentId } : {});
  }
  history(sessionKey2, agentId) {
    return this.request("sessions.history", { sessionKey: sessionKey2, ...agentId ? { agentId } : {} });
  }
  send(opts) {
    return this.request("chat.send", { sessionKey: "default", ...opts });
  }
  abort(sessionKey2) {
    return this.request("chat.abort", { sessionKey: sessionKey2 });
  }
  invokeTool(name, params = {}) {
    return this.request("tools.invoke", { name, params });
  }
  capabilities(agentId) {
    return this.request("capabilities.list", agentId ? { agentId } : {});
  }
  catalog() {
    return this.request("plugins.catalog");
  }
  notifications() {
    return this.request("notifications.list");
  }
  /**
   * Follow ONE session's run events (the daemon broadcasts every session's events to every
   * authorized socket — this does the filtering bookkeeping for you). Returns the unsubscribe.
   */
  onRun(sessionKey2, handler) {
    return this.on("chat.event", (payload) => {
      if (payload.sessionKey === sessionKey2) {
        handler(payload);
      }
    });
  }
  /** Follow every run of ONE agent (uses the protocol-v1 agentId event field). */
  onAgent(agentId, handler) {
    return this.on("chat.event", (payload) => {
      if (payload.agentId === agentId) {
        handler(payload);
      }
    });
  }
  /** Build the authenticated GET /file URL for a server-side artifact path. */
  fileUrl(path) {
    if (!this.lastTarget) throw new Error("not connected");
    const origin = toHttpOrigin(new URL(this.lastTarget.url).toString());
    const u = new URL("/file", origin);
    u.searchParams.set("path", path);
    if (this.lastTarget.token) u.searchParams.set("token", this.lastTarget.token);
    if (this.lastTarget.session) u.searchParams.set("session", this.lastTarget.session);
    return u.toString();
  }
};
//: Backoff ceiling for a credential the server REFUSED. Retrying a dead token fast is not
//: resilience, it is a flood — and the server is the thing being flooded.
_AgentdClient.UNAUTHORIZED_DELAY = 6e4;
//: A socket must survive this long before it counts as a working connection.
_AgentdClient.HEALTHY_MS = 1e4;
var AgentdClient = _AgentdClient;
function fromPage(options = {}) {
  const here = new URL(window.location.href);
  const token = here.searchParams.get("token") || "";
  const pathAgent = /\/apps\/([^/]+)/.exec(here.pathname);
  const scope = here.searchParams.get("scope") || (pathAgent ? `agent:${decodeURIComponent(pathAgent[1])}` : "");
  const urlSession = here.searchParams.get("session") || "";
  const urlMode = here.searchParams.get("mode") || "";
  if (urlSession) {
    saveSession({
      token: urlSession,
      email: "",
      accountId: "",
      expiresAt: accessTokenExpiry(urlSession) || void 0
    });
  }
  if (urlMode === "local" || urlMode === "cloud") saveMode(urlMode);
  if ((urlSession || urlMode) && typeof history !== "undefined") {
    here.searchParams.delete("session");
    here.searchParams.delete("mode");
    history.replaceState(null, "", here.toString());
  }
  const client = new AgentdClient(options);
  identity({ origin: here.origin, client });
  client.connect(async () => {
    const stored = loadSession()?.token;
    const a = await identity({ origin: here.origin }).state();
    const cookieToken = a.via === "cookie" && a.state === "ok" ? a.accessToken || "" : "";
    const signedIn = !!stored || a.state === "ok";
    return {
      url: here.origin,
      token: token || void 0,
      session: cookieToken || stored || void 0,
      mode: effectiveMode("", signedIn),
      scope: scope || void 0
    };
  });
  return client;
}

// src/auth.ts
async function authStatus(opts = {}) {
  const status = await platformStatus(opts);
  const canUseCloud = !!status.canUseCloud;
  const tok = await fetchToken(opts);
  const signedIn = tok.state === "ok";
  return {
    available: !!String(status.accountsUrl || ""),
    signedIn,
    email: signedIn && tok.email || "",
    accountId: signedIn && tok.accountId || "",
    mode: effectiveMode(opts.storageKey, signedIn, canUseCloud),
    canUseCloud,
    // Absent on an older daemon. Defaulting to TRUE keeps the gate exactly as it was there — a
    // client that guessed "not required" against a daemon that requires it would show no login
    // and then fail every call with no explanation.
    required: status.signInRequired !== false
  };
}
async function authLogin(args, opts = {}) {
  const r = await fetch(authUrl("/auth/login", opts), {
    cache: "no-store",
    headers: {
      "X-Auth-Email": args.email,
      "X-Auth-Password": args.password,
      ...args.signup ? { "X-Auth-Signup": "1" } : {}
    }
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok || d.state !== "ok") {
    throw new Error(String(d.error || `sign-in failed (HTTP ${r.status})`));
  }
  return authStatus(opts);
}
async function authLogout(opts = {}) {
  await fetch(authUrl("/auth/logout", opts), { cache: "no-store" }).catch(() => {
  });
  saveMode(null, opts.storageKey);
  return authStatus(opts);
}
async function setRunMode(mode, opts = {}) {
  if (mode === "cloud" && !await identity(opts).accessToken()) {
    throw new Error("sign in first \u2014 Cloud mode meters model calls to your account");
  }
  saveMode(mode, opts.storageKey);
  opts.client?.reconnect();
  return authStatus(opts);
}

// ../billing/src/credits-bus.ts
var listeners = /* @__PURE__ */ new Set();
function onCreditsChanged(cb) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}
function notifyCreditsChanged() {
  listeners.forEach((l) => l());
}

// ../billing/src/billing-client.ts
function toPack(d) {
  return {
    id: String(d.id || ""),
    kind: String(d.kind || ""),
    title: String(d.title || ""),
    priceUsd: Number(d.price_usd || 0),
    credits: Number(d.credits || 0),
    seats: Number(d.seats || 0),
    modelTierMax: String(d.model_tier_max || ""),
    periodDays: Number(d.period_days || 0)
  };
}
var BillingClient = class {
  constructor(host) {
    this.host = host;
  }
  async base() {
    const url = String(await this.host.accountsUrl() || "").replace(/\/$/, "");
    if (!url) throw new Error("this daemon has no accounts service configured");
    return url;
  }
  async authed() {
    const token = String(await this.host.accessToken() || "");
    if (!token) throw new Error("sign in first");
    return { Authorization: `Bearer ${token}` };
  }
  /**
   * The balance, or null when there is nothing to show — not signed in, no accounts service, or
   * the request failed. Null is rendered as "unavailable" rather than as zero, because showing a
   * confident 0 to someone with credits is worse than admitting we do not know.
   */
  async credits(agentId = "") {
    try {
      const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
      const r = await fetch(`${await this.base()}/me/credits${q}`, { headers: await this.authed() });
      if (!r.ok) return null;
      const d = await r.json();
      return {
        creditsRemaining: Number(d.credits_remaining || 0),
        fundingSource: String(d.funding_source || ""),
        orgId: String(d.org_id || ""),
        memberCapped: Boolean(d.member_capped),
        creditClass: String(d.credit_class || ""),
        modelTierMax: String(d.model_tier_max || ""),
        entitlementRequired: Boolean(d.entitlement_required),
        entitled: d.entitled !== false,
        expiresAt: Number(d.expires_at || 0)
      };
    } catch {
      return null;
    }
  }
  /**
   * What is for sale. NOT signed-in-only and NOT hardcoded: the packs come from the `products`
   * table, whose prices derive from the markup dial, so changing what is on sale is a row in a
   * database and never a release of a client — and the price shown cannot drift from the price
   * charged, because there is only one of them.
   */
  async catalog(kind = "credit_pack") {
    try {
      const r = await fetch(`${await this.base()}/products?kind=${encodeURIComponent(kind)}`);
      if (!r.ok) return null;
      const d = await r.json();
      return {
        packs: (d.products || []).map(toPack),
        provider: String(d.provider || ""),
        paymentNote: String(d.payment_note || "")
      };
    } catch {
      return null;
    }
  }
  /**
   * Buy a pack. THROWS with the server's own message on refusal.
   *
   * `returnUrl` is only consulted by a rail that sends the customer away; on one that settles in
   * place it is ignored, and the returned `checkoutUrl` is empty. Callers pass their own page so a
   * card payment comes back where it started.
   */
  async buy(productId, returnUrl = "", orgId = "") {
    const body = {
      product_id: productId,
      idempotency_key: this.host.newKey()
    };
    if (orgId) body.org_id = orgId;
    if (returnUrl) {
      body.success_url = returnUrl;
      body.cancel_url = returnUrl;
    }
    const r = await fetch(`${await this.base()}/me/checkout`, {
      method: "POST",
      headers: { ...await this.authed(), "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(String(d.detail || `purchase failed (HTTP ${r.status})`));
    const checkoutUrl = String(d.checkout_url || "");
    if (!checkoutUrl) notifyCreditsChanged();
    const payment = d.payment || {};
    return {
      ok: true,
      replayed: d.replayed === true,
      credits: Number(d.credits || 0),
      priceUsd: Number(d.price_usd || 0),
      creditsRemaining: Number(d.credits_remaining || 0),
      paymentDetail: String(payment.detail || ""),
      checkoutUrl
    };
  }
};

// src/credits.ts
function newKey() {
  const c = typeof crypto === "undefined" ? null : crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  if (c && typeof c.getRandomValues === "function") {
    const b = c.getRandomValues(new Uint8Array(16));
    return Array.from(b, (n) => n.toString(16).padStart(2, "0")).join("");
  }
  return `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}
function creditsHost(opts = {}) {
  return {
    accountsUrl: () => accountsUrl(opts),
    accessToken: () => identity(opts).accessToken(),
    newKey
  };
}
function billing(opts = {}) {
  return new BillingClient(creditsHost(opts));
}

// src/orgs.ts
async function call(opts, method, path, body) {
  const base = await accountsUrl(opts);
  if (!base) throw new Error("this daemon has no accounts service, so organizations are unavailable");
  const token = await identity(opts).accessToken();
  if (!token) throw new Error("sign in first");
  const r = await fetch(base + path, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...body !== void 0 ? { "Content-Type": "application/json" } : {}
    },
    ...body !== void 0 ? { body: JSON.stringify(body) } : {}
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(String(d.detail || `request failed (HTTP ${r.status})`));
  return d;
}
function toDetail(d) {
  const out = {
    id: String(d.id || ""),
    name: String(d.name || ""),
    role: String(d.role || "member"),
    seatsTotal: Number(d.seats_total || 0),
    seatsUsed: Number(d.seats_used || 0),
    createdAt: Number(d.created_at || 0)
  };
  if (Array.isArray(d.members)) {
    out.members = d.members.map((m) => ({
      accountId: String(m.account_id || ""),
      email: String(m.email || ""),
      role: String(m.role || "member"),
      monthlyCreditCap: Number(m.monthly_credit_cap || 0),
      addedAt: Number(m.added_at || 0)
    }));
    out.domains = d.domains || [];
    out.primaryOwner = String(d.primary_owner || "");
    out.poolCreditsRemaining = Number(d.pool_credits_remaining || 0);
  }
  return out;
}
async function fetchMyOrgs(opts = {}) {
  const d = await call(
    opts,
    "GET",
    "/me/orgs"
  );
  return {
    orgs: (d.orgs || []).map((o) => ({
      id: String(o.id || ""),
      name: String(o.name || o.id || ""),
      role: String(o.role || "member")
    })),
    joinable: (d.joinable || []).map((o) => ({
      id: String(o.id || ""),
      name: String(o.name || o.id || "")
    }))
  };
}
async function createOrg(name, seatsTotal, opts = {}) {
  return toDetail(
    await call(opts, "POST", "/orgs", { name, ...seatsTotal ? { seats_total: seatsTotal } : {} })
  );
}
async function joinOrg(input, opts = {}) {
  return toDetail(
    await call(opts, "POST", "/orgs/join", {
      ...input.inviteToken ? { invite_token: input.inviteToken } : {},
      ...input.orgId ? { org_id: input.orgId } : {}
    })
  );
}
async function fetchOrgDetail(orgId, opts = {}) {
  return toDetail(await call(opts, "GET", `/orgs/${encodeURIComponent(orgId)}`));
}
async function mintInvite(orgId, input = {}, opts = {}) {
  const d = await call(
    opts,
    "POST",
    `/orgs/${encodeURIComponent(orgId)}/invites`,
    { ...input.email ? { email: input.email } : {}, ...input.role ? { role: input.role } : {} }
  );
  return {
    inviteToken: String(d.invite_token || ""),
    orgId: String(d.org_id || orgId),
    orgName: String(d.org_name || ""),
    email: String(d.email || ""),
    role: String(d.role || "member"),
    expiresAt: Number(d.expires_at || 0)
  };
}
async function updateMember(orgId, accountId, patch, opts = {}) {
  return toDetail(
    await call(
      opts,
      "POST",
      `/orgs/${encodeURIComponent(orgId)}/members/${encodeURIComponent(accountId)}`,
      {
        ...patch.role !== void 0 ? { role: patch.role } : {},
        ...patch.monthlyCreditCap !== void 0 ? { monthly_credit_cap: patch.monthlyCreditCap } : {},
        ...patch.active !== void 0 ? { active: patch.active } : {}
      }
    )
  );
}
async function updateDomain(orgId, domain, remove = false, opts = {}) {
  return toDetail(
    await call(opts, "POST", `/orgs/${encodeURIComponent(orgId)}/domains`, { domain, remove })
  );
}
async function fetchOrgUsage(orgId, opts = {}) {
  const d = await call(
    opts,
    "GET",
    `/orgs/${encodeURIComponent(orgId)}/usage`
  );
  return {
    month: String(d.month || ""),
    members: (d.members || []).map((m) => ({
      accountId: String(m.account_id || ""),
      email: String(m.email || ""),
      credits: Number(m.credits || 0),
      costUsd: Number(m.cost_usd || 0),
      calls: Number(m.calls || 0),
      monthlyCreditCap: Number(m.monthly_credit_cap || 0)
    }))
  };
}
export {
  AgentdClient,
  BillingClient,
  DEFAULT_TIMEOUT,
  PROTOCOL_VERSION,
  acceptHostTokens,
  accessTokenAccount,
  accessTokenExpiry,
  accountsUrl,
  authLogin,
  authLogout,
  authStatus,
  authUrl,
  billing,
  createOrg,
  creditsHost,
  daemonOrigin,
  daemonToken,
  effectiveMode,
  fetchMyOrgs,
  fetchOrgDetail,
  fetchOrgUsage,
  fetchToken,
  fromPage,
  identity,
  joinOrg,
  loadMode,
  loadSession,
  mintInvite,
  notifyCreditsChanged,
  onCreditsChanged,
  platformStatus,
  resetIdentity,
  resultText,
  saveMode,
  saveSession,
  sessionKey,
  setRunMode,
  startAuthRenewal,
  updateDomain,
  updateMember,
  withTimeout
};
//# sourceMappingURL=index.js.map
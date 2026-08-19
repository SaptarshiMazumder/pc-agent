"use strict";
var agentd = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // src/index.ts
  var src_exports = {};
  __export(src_exports, {
    AgentdClient: () => AgentdClient,
    DEFAULT_TIMEOUT: () => DEFAULT_TIMEOUT,
    PROTOCOL_VERSION: () => PROTOCOL_VERSION,
    TokenManager: () => TokenManager,
    acceptHostTokens: () => acceptHostTokens,
    accessTokenAccount: () => accessTokenAccount,
    accessTokenExpiry: () => accessTokenExpiry,
    accountsUrl: () => accountsUrl,
    authLogin: () => authLogin,
    authLogout: () => authLogout,
    authRefresh: () => authRefresh,
    authStatus: () => authStatus,
    daemonOrigin: () => daemonOrigin,
    daemonToken: () => daemonToken,
    effectiveMode: () => effectiveMode,
    fromPage: () => fromPage,
    identity: () => identity,
    loadMode: () => loadMode,
    loadSession: () => loadSession,
    localSessionStore: () => localSessionStore,
    memorySessionStore: () => memorySessionStore,
    mountSignInGate: () => mountSignInGate,
    platformStatus: () => platformStatus,
    resetIdentity: () => resetIdentity,
    resultText: () => resultText,
    saveMode: () => saveMode,
    saveSession: () => saveSession,
    sessionKey: () => sessionKey,
    setRunMode: () => setRunMode,
    signOutAndGate: () => signOutAndGate,
    startAuthRenewal: () => startAuthRenewal,
    withTimeout: () => withTimeout
  });

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
  function usable(token) {
    return !!token && !token.startsWith("sess_") && token.split(".").length === 3;
  }
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

  // ../auth/src/storage.ts
  function localSessionStore(key) {
    return {
      read() {
        try {
          return localStorage.getItem(key);
        } catch {
          return null;
        }
      },
      write(value) {
        try {
          if (value === null) localStorage.removeItem(key);
          else localStorage.setItem(key, value);
        } catch {
        }
      }
    };
  }
  function memorySessionStore() {
    let held = null;
    return {
      read: () => held,
      write: (value) => {
        held = value;
      }
    };
  }

  // ../auth/src/token-manager.ts
  var EXPIRY_SKEW_MS = 3e4;
  var MIN_DELAY_MS = 5e3;
  var BLIND_POLL_MS = 3e5;
  var DEFAULT_TIMEOUT_MS = 45e3;
  var TokenManager = class {
    constructor(config) {
      this.config = config;
      this.pair = null;
      this.inflight = null;
      this.timer = null;
      this.listeners = /* @__PURE__ */ new Set();
      this.wake = null;
      this.pair = this.readStored();
    }
    // ------------------------------------------------------------------- reading
    /** What is held right now, WITHOUT renewing. Synchronous, for a socket URL or a rendered email. */
    current() {
      return this.pair;
    }
    /** Is there a credential this client can still use, or still renew? */
    signedIn() {
      const p = this.pair;
      if (!p || !usable(p.accessToken)) return false;
      if (p.refreshToken || !this.expired(p)) return true;
      this.replace(null);
      return false;
    }
    /**
     * A USABLE access token, renewing first when the one we hold is spent.
     *
     * The only way anything should ever obtain a credential, so that no caller anywhere has to
     * reason about expiry — which is exactly the reasoning every caller previously got wrong.
     */
    async accessToken() {
      const p = this.pair;
      if (p && !this.expired(p)) return p.accessToken;
      const next = await this.refresh();
      return next?.accessToken || "";
    }
    subscribe(cb) {
      this.listeners.add(cb);
      return () => this.listeners.delete(cb);
    }
    // ------------------------------------------------------------------- writing
    /**
     * Sign in, creating the account first when `signup`.
     *
     * THROWS on a rejected credential, carrying the service's own message ("incorrect password") so
     * a form has something to show. A failed attempt must never resolve to a signed-out state: the
     * caller cannot tell that apart from having signed out, and the user is left looking at a form
     * that cleared itself.
     */
    async login(args) {
      const base = await this.base();
      const email = args.email.trim().toLowerCase();
      if (args.signup) {
        await this.post(`${base}/signup`, { email, password: args.password }, "signup");
      }
      const data = await this.post(
        `${base}/auth/login`,
        {
          email,
          password: args.password,
          client_id: this.config.clientId,
          device_label: this.deviceLabel()
        },
        "login"
      );
      const next = this.toPair(data, email);
      if (!next.accessToken) throw new Error("the accounts server returned no access token");
      await this.set(next);
      return next;
    }
    /**
     * Re-establish a session at start-up.
     *
     * This is what makes "stay signed in" work with a ten-minute access token: nothing durable is
     * kept but the refresh token, and one exchange at boot turns it into a usable pair. A window
     * holding no refresh token (opened by the desktop app, and fed rather than renewing) keeps
     * whatever it was handed — unless that has died, in which case it is dropped, because a page
     * presenting a dead token is not refused, it is accepted ANONYMOUSLY.
     */
    async restore() {
      const stored = this.pair || this.readStored();
      if (!stored?.refreshToken) {
        if (stored && this.expired(stored)) await this.set(null);
        return this.pair;
      }
      return this.refresh();
    }
    /**
     * Trade the refresh token for a new pair. SINGLE-FLIGHT — see the header.
     *
     * Returns null when the session is over, having cleared it; and null WITHOUT clearing when the
     * attempt merely failed. The difference is the whole point.
     */
    refresh() {
      if (this.inflight) return this.inflight;
      this.inflight = this.exchange().finally(() => {
        this.inflight = null;
      });
      return this.inflight;
    }
    async exchange() {
      const token = this.pair?.refreshToken || await this.readSecret();
      if (!token) return null;
      let base = "";
      try {
        base = await this.base();
      } catch {
        return null;
      }
      let res;
      try {
        res = await this.send(`${base}/auth/refresh`, {
          refresh_token: token,
          client_id: this.config.clientId
        });
      } catch {
        return null;
      }
      if (!res.ok) {
        if (res.status === 401 || res.status === 403) await this.set(null);
        return null;
      }
      const data = await res.json().catch(() => ({}));
      const next = this.toPair(data, this.pair?.email || "");
      if (!next.accessToken) return null;
      if (!next.refreshToken) next.refreshToken = token;
      if (!next.accountId && this.pair?.accountId) next.accountId = this.pair.accountId;
      await this.set(next);
      return next;
    }
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
    replace(pair) {
      void this.set(pair);
    }
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
    async adopt(accessToken) {
      if (!usable(accessToken)) return false;
      const held = this.pair;
      if (held?.accountId && accessTokenAccount(accessToken) !== held.accountId) return false;
      await this.set({
        accessToken,
        refreshToken: held?.refreshToken || "",
        expiresAt: accessTokenExpiry(accessToken),
        accountId: held?.accountId || "",
        email: held?.email || ""
      });
      return true;
    }
    /**
     * Forget this client's session, and tell the server so.
     *
     * A sign-out that only forgets locally leaves a 30-day credential alive on a machine the user
     * may have just decided they do not trust. Best-effort: being offline must not block signing out.
     */
    async logout() {
      const token = this.pair?.refreshToken || await this.readSecret();
      await this.set(null);
      if (!token) return;
      try {
        const base = await this.base();
        await this.send(`${base}/auth/logout`, { refresh_token: token });
      } catch {
      }
    }
    // ------------------------------------------------------------------- renewal
    /**
     * Keep the credential fresh for as long as the host lives. Returns a stop function.
     *
     * TWO TRIGGERS, because a timer alone is provably not enough. Timers do not fire while a machine
     * sleeps and are throttled in background tabs, so a window that was away comes back holding a
     * token that died hours ago — the single most common way this used to break, and the one a
     * schedule can never cover. Coming back is therefore its own trigger.
     */
    start() {
      this.schedule();
      if (typeof document !== "undefined" && !this.wake) {
        this.wake = () => {
          if (document.visibilityState === "visible") void this.tick();
        };
        document.addEventListener("visibilitychange", this.wake);
        if (typeof addEventListener === "function") addEventListener("focus", this.wake);
      }
      return () => this.stop();
    }
    stop() {
      if (this.timer) clearTimeout(this.timer);
      this.timer = null;
      if (!this.wake) return;
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", this.wake);
      }
      if (typeof removeEventListener === "function") removeEventListener("focus", this.wake);
      this.wake = null;
    }
    async tick() {
      const p = this.pair;
      if (p?.refreshToken && this.expiringSoon(p)) await this.refresh();
      this.schedule();
    }
    schedule() {
      if (this.timer) clearTimeout(this.timer);
      this.timer = null;
      const p = this.pair;
      if (!p?.refreshToken) return;
      if (!p.expiresAt) {
        this.timer = setTimeout(() => void this.tick(), BLIND_POLL_MS);
        return;
      }
      const life = p.expiresAt - Date.now();
      this.timer = setTimeout(() => void this.tick(), Math.max(MIN_DELAY_MS, Math.floor(life * 0.8)));
    }
    expired(p) {
      return p.expiresAt > 0 && Date.now() > p.expiresAt - EXPIRY_SKEW_MS;
    }
    /** Close enough to the end to be worth renewing now — or already past it. */
    expiringSoon(p) {
      if (!p.expiresAt) return true;
      return Date.now() > p.expiresAt - Math.max(EXPIRY_SKEW_MS, 12e4);
    }
    // ------------------------------------------------------------------- storage
    async set(next) {
      this.pair = next;
      try {
        this.writeStored(next);
      } catch (e) {
        console.warn("[auth] could not persist the session; signed in for this run only", e);
      }
      this.schedule();
      this.listeners.forEach((l) => l(next));
      this.config.onChange?.(next);
    }
    writeStored(next) {
      if (!next) {
        this.config.session.write(null);
        void this.config.secrets?.write(null);
        return;
      }
      const encrypted = !!this.config.secrets;
      this.config.session.write(
        JSON.stringify({
          accessToken: next.accessToken,
          // Kept here ONLY when there is no encrypted store to put it in. On the desktop it goes to
          // the keychain instead, so the plain store never holds a 30-day credential.
          refreshToken: encrypted ? "" : next.refreshToken,
          expiresAt: next.expiresAt,
          accountId: next.accountId,
          email: next.email
        })
      );
      if (encrypted) void this.config.secrets?.write(next.refreshToken || null);
    }
    readStored() {
      try {
        const raw = this.config.session.read();
        if (!raw) return null;
        const p = JSON.parse(raw);
        const held = {
          accessToken: p.accessToken || "",
          refreshToken: p.refreshToken || "",
          expiresAt: p.expiresAt || accessTokenExpiry(p.accessToken || ""),
          accountId: p.accountId || "",
          email: p.email || ""
        };
        if (!usable(held.accessToken) || !held.refreshToken && this.expired(held)) {
          this.config.session.write(null);
          return null;
        }
        return held;
      } catch {
        return null;
      }
    }
    async readSecret() {
      try {
        return await this.config.secrets?.read() || "";
      } catch {
        return "";
      }
    }
    // ------------------------------------------------------------------ plumbing
    toPair(d, fallbackEmail) {
      const accessToken = String(d.access_token || d.token || d.session || "");
      return {
        accessToken,
        refreshToken: String(d.refresh_token || ""),
        // `expires_in` is RELATIVE on purpose (identity/domain/token.py): our clock and the server's
        // may disagree, and a relative lifetime is correct under skew where an absolute deadline is
        // not. The token's own `exp` covers a server that sends neither.
        expiresAt: d.expires_in ? Date.now() + Number(d.expires_in) * 1e3 : accessTokenExpiry(accessToken),
        accountId: String(d.account_id || ""),
        email: String(d.email || fallbackEmail)
      };
    }
    async base() {
      const clean = (await this.config.accountsUrl() || "").replace(/\/$/, "");
      if (!clean) throw new Error("no accounts service is configured");
      return clean;
    }
    deviceLabel() {
      try {
        return this.config.deviceLabel?.() || this.config.clientId;
      } catch {
        return this.config.clientId;
      }
    }
    async send(url, body) {
      const call = this.config.fetchImpl || fetch;
      const ms = this.config.timeoutMs ?? DEFAULT_TIMEOUT_MS;
      const ctl = typeof AbortController === "function" ? new AbortController() : null;
      const timer = setTimeout(() => ctl?.abort(), ms);
      try {
        return await call(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
          signal: ctl?.signal
        });
      } finally {
        clearTimeout(timer);
      }
    }
    async post(url, body, what) {
      const r = await this.send(url, body);
      const text = await r.text();
      let data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
      }
      if (!r.ok) {
        throw new Error(String(data?.detail || data?.error || `${what} failed (HTTP ${r.status})`));
      }
      return data;
    }
  };

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
  function sessionKey(explicit = "") {
    if (explicit) return explicit;
    const here = typeof location === "undefined" ? null : new URL(location.href);
    const scope = here?.searchParams.get("scope") || "";
    const fromPath = /\/apps\/([^/]+)/.exec(here?.pathname || "");
    const id = /^agent:(.+)$/.exec(scope)?.[1] || (fromPath ? decodeURIComponent(fromPath[1]) : "");
    return `agentd.session.${id || "app"}`;
  }
  var managers = /* @__PURE__ */ new Map();
  function identity(opts = {}) {
    const key = sessionKey(opts.storageKey);
    const held = managers.get(key);
    if (held) {
      if (opts.client) bindClient(held, key, opts.client);
      return held;
    }
    const manager = new TokenManager({
      accountsUrl: () => accountsUrl(opts),
      session: localSessionStore(key),
      // No `secrets`: a browser page has no OS keychain, so the refresh token — when this window
      // has one at all — rides in the same store. The desktop's answer to that is not to encrypt it
      // here but to never send one (see the header).
      clientId: "app",
      deviceLabel: () => documentTitle() || "Agent app",
      timeoutMs: opts.timeoutMs
    });
    managers.set(key, manager);
    if (opts.client) bindClient(manager, key, opts.client);
    manager.start();
    return manager;
  }
  var bound = /* @__PURE__ */ new Map();
  function bindClient(manager, key, client) {
    const already = bound.get(key);
    bound.set(key, client);
    if (already === client) return;
    if (already) return;
    manager.subscribe((pair) => {
      const target = bound.get(key);
      if (!target) return;
      if (!pair) {
        target.reconnect();
        return;
      }
      void target.request("auth.update", { accessToken: pair.accessToken }).catch(() => target.reconnect());
    });
  }
  function documentTitle() {
    try {
      return typeof document === "undefined" ? "" : document.title;
    } catch {
      return "";
    }
  }
  function resetIdentity() {
    managers.forEach((m) => m.stop());
    managers.clear();
    bound.clear();
  }

  // src/session.ts
  function loadSession(storageKey = "") {
    const manager = identity({ storageKey });
    if (!manager.signedIn()) return null;
    const p = manager.current();
    if (!p) return null;
    return {
      token: p.accessToken,
      email: p.email,
      accountId: p.accountId,
      refreshToken: p.refreshToken || void 0,
      expiresAt: p.expiresAt || void 0
    };
  }
  function saveSession(value, storageKey = "") {
    const manager = identity({ storageKey });
    if (!value) {
      manager.replace(null);
      return;
    }
    manager.replace({
      accessToken: value.token,
      refreshToken: value.refreshToken || "",
      expiresAt: value.expiresAt || accessTokenExpiry(value.token),
      accountId: value.accountId || "",
      email: value.email || ""
    });
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
      this.reconnectDelay = 1e3;
      /** When the current socket opened, so "did this connection actually work?" can be answered. */
      this.openedAt = 0;
      this.closedByUs = false;
      this.lastTarget = null;
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
        for (const handler of this.statusHandlers) handler("open");
      };
      ws.onmessage = (message) => this.handleFrame(JSON.parse(message.data));
      ws.onclose = (event) => {
        for (const [, pending] of this.pending) pending.reject(new Error("connection closed"));
        this.pending.clear();
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
    /** Subscribe to a broadcast event by name. Returns the unsubscribe. */
    on(event, handler) {
      if (!this.eventHandlers.has(event)) this.eventHandlers.set(event, /* @__PURE__ */ new Set());
      this.eventHandlers.get(event).add(handler);
      return () => this.eventHandlers.get(event)?.delete(handler);
    }
    onStatus(handler) {
      this.statusHandlers.add(handler);
      return () => this.statusHandlers.delete(handler);
    }
    handleFrame(frame) {
      if (frame.type === "res") {
        const pending = this.pending.get(frame.id);
        if (!pending) return;
        this.pending.delete(frame.id);
        if (frame.ok) pending.resolve(frame.payload || {});
        else pending.reject(new Error(String(frame.payload?.error || "gateway error")));
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
    client.connect(async () => {
      const stored = loadSession()?.token;
      return {
        url: here.origin,
        token: token || void 0,
        session: stored || void 0,
        mode: effectiveMode("", !!stored),
        scope: scope || void 0
      };
    });
    return client;
  }

  // src/auth.ts
  async function authStatus(opts = {}) {
    const status = await platformStatus(opts);
    const manager = identity(opts);
    const signedIn = manager.signedIn();
    const held = manager.current();
    const canUseCloud = !!status.canUseCloud;
    return {
      available: !!String(status.accountsUrl || ""),
      signedIn,
      email: signedIn && held?.email || "",
      accountId: signedIn && held?.accountId || "",
      mode: effectiveMode(opts.storageKey, signedIn, canUseCloud),
      canUseCloud,
      // Absent on an older daemon. Defaulting to TRUE keeps the gate exactly as it was there — a
      // client that guessed "not required" against a daemon that requires it would show no login and
      // then fail every call with no explanation.
      required: status.signInRequired !== false
    };
  }
  async function authLogin(args, opts = {}) {
    await identity(opts).login(args);
    return authStatus(opts);
  }
  async function authRefresh(opts = {}) {
    const next = await identity(opts).refresh();
    return next?.accessToken || "";
  }
  function startAuthRenewal(opts = {}) {
    return identity(opts).start();
  }
  function acceptHostTokens(opts = {}) {
    const host = globalThis.agentdHost;
    if (!host?.onAccessToken) return () => void 0;
    const manager = identity(opts);
    return host.onAccessToken((token) => {
      if (token) void manager.adopt(token);
    });
  }
  async function authLogout(opts = {}) {
    await identity(opts).logout();
    saveMode(null, opts.storageKey);
    return authStatus(opts);
  }
  async function setRunMode(mode, opts = {}) {
    if (mode === "cloud" && !identity(opts).signedIn()) {
      throw new Error("sign in first \u2014 Cloud mode meters model calls to your account");
    }
    saveMode(mode, opts.storageKey);
    opts.client?.reconnect();
    return authStatus(opts);
  }

  // src/gate.ts
  var STYLE_ID = "agentd-gate-style";
  var CSS = `
.agentd-gate{position:fixed;inset:0;z-index:9999;display:grid;place-items:center;
  background:var(--gate-bg,rgba(20,20,22,.72));backdrop-filter:blur(6px);
  font-family:var(--gate-font,system-ui,-apple-system,Segoe UI,sans-serif)}
.agentd-gate[hidden]{display:none}
.agentd-gate-card{width:min(92vw,360px);padding:26px 24px;border-radius:14px;
  background:var(--gate-card,#fff);color:var(--gate-fg,#16161a);
  box-shadow:0 18px 50px rgba(0,0,0,.35);display:flex;flex-direction:column;gap:10px}
.agentd-gate-mark{font-size:26px;line-height:1;text-align:center;color:var(--gate-accent,#4f46e5)}
.agentd-gate-title{margin:2px 0 0;font-size:19px;font-weight:650;text-align:center}
.agentd-gate-sub{margin:0 0 6px;font-size:12.5px;line-height:1.45;text-align:center;
  color:var(--gate-muted,#6b6b76)}
.agentd-gate-label{font-size:11.5px;font-weight:600;color:var(--gate-muted,#6b6b76)}
.agentd-gate-input{padding:9px 11px;border-radius:8px;font-size:13.5px;
  border:1px solid var(--gate-border,#d8d8e0);background:var(--gate-input,#fff);
  color:var(--gate-fg,#16161a)}
.agentd-gate-input:focus{outline:2px solid var(--gate-accent,#4f46e5);outline-offset:1px}
.agentd-gate-btn{margin-top:6px;padding:10px 12px;border:0;border-radius:8px;cursor:pointer;
  font-size:13.5px;font-weight:600;color:var(--gate-on-accent,#fff);
  background:var(--gate-accent,#4f46e5)}
.agentd-gate-btn[disabled]{opacity:.6;cursor:default}
.agentd-gate-toggle{padding:4px;border:0;background:none;cursor:pointer;font-size:12px;
  color:var(--gate-muted,#6b6b76);text-decoration:underline}
.agentd-gate-error{padding:7px 9px;border-radius:7px;font-size:12px;
  background:var(--gate-error-bg,#fdeaea);color:var(--gate-error-fg,#a3232b)}
.agentd-gate-error[hidden]{display:none}
`;
  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const el = document.createElement("style");
    el.id = STYLE_ID;
    el.textContent = CSS;
    document.head.appendChild(el);
  }
  function build(product, blurb, allowSignup) {
    const wrap = document.createElement("div");
    wrap.className = "agentd-gate";
    wrap.id = "gate";
    wrap.innerHTML = `
    <form class="agentd-gate-card" id="gateForm" autocomplete="on">
      <div class="agentd-gate-mark" aria-hidden="true">&#9681;</div>
      <h1 class="agentd-gate-title" id="gateTitle">Sign in</h1>
      <p class="agentd-gate-sub" id="gateSub"></p>
      <label class="agentd-gate-label" for="gateEmail">Email</label>
      <input class="agentd-gate-input" id="gateEmail" type="email" autocomplete="email"
             required placeholder="you@example.com" />
      <label class="agentd-gate-label" for="gatePass">Password</label>
      <input class="agentd-gate-input" id="gatePass" type="password"
             autocomplete="current-password" required minlength="8" placeholder="\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022" />
      <div class="agentd-gate-error" id="gateError" hidden></div>
      <button class="agentd-gate-btn" id="gateBtn" type="submit">Sign in</button>
      ${allowSignup ? '<button class="agentd-gate-toggle" id="gateToggle" type="button">New here? Create an account</button>' : ""}
    </form>`;
    const title = wrap.querySelector("#gateTitle");
    const sub = wrap.querySelector("#gateSub");
    title.textContent = `Sign in to ${product}`;
    sub.textContent = blurb;
    return wrap;
  }
  function wantsVerifyBypass() {
    if (typeof location === "undefined") return false;
    try {
      return new URL(location.href).searchParams.get("verify") === "1";
    } catch {
      return false;
    }
  }
  async function mountSignInGate(options = {}) {
    const allowSignup = options.allowSignup !== false;
    const product = options.product || typeof document !== "undefined" && document.title || "this app";
    const blurb = options.blurb || "Sign in to continue.";
    const state = await authStatus(options);
    if (!state.available || state.signedIn) {
      if (state.signedIn) {
        startAuthRenewal(options);
        acceptHostTokens(options);
      }
      return { ...state, signedInHere: false };
    }
    if (!state.required && wantsVerifyBypass()) {
      return { ...state, signedInHere: false };
    }
    injectStyle();
    const gate = build(product, blurb, allowSignup);
    (options.mount || document.body).appendChild(gate);
    const $ = (id) => gate.querySelector(`#${id}`);
    const emailEl = $("gateEmail");
    const passEl = $("gatePass");
    const btn = $("gateBtn");
    const errorEl = $("gateError");
    const subEl = $("gateSub");
    const titleEl = $("gateTitle");
    const toggle = allowSignup ? $("gateToggle") : null;
    let signup = false;
    const say = (text) => subEl.textContent = text;
    const fail = (text) => {
      errorEl.textContent = text;
      errorEl.hidden = !text;
    };
    if (state.email) emailEl.value = state.email;
    setTimeout(() => emailEl.focus(), 0);
    toggle?.addEventListener("click", () => {
      signup = !signup;
      titleEl.textContent = signup ? "Create your account" : `Sign in to ${product}`;
      btn.textContent = signup ? "Create account" : "Sign in";
      toggle.textContent = signup ? "Have an account? Sign in" : "New here? Create an account";
      passEl.setAttribute("autocomplete", signup ? "new-password" : "current-password");
      say(blurb);
      fail("");
    });
    return new Promise((resolve) => {
      const form = $("gateForm");
      say(blurb);
      form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const email = emailEl.value.trim().toLowerCase();
        const password = passEl.value;
        if (!email || !password) return;
        btn.disabled = true;
        fail("");
        say(signup ? "Creating your account\u2026" : "Signing in\u2026");
        try {
          const result = await authLogin({ email, password, signup }, options);
          startAuthRenewal(options);
          acceptHostTokens(options);
          gate.remove();
          resolve({ ...result, signedInHere: true });
        } catch (e) {
          btn.disabled = false;
          say(blurb);
          fail(String(e?.message || e));
        }
      });
    });
  }
  async function signOutAndGate(options = {}) {
    const state = await authLogout(options);
    if (!state.available) return { ...state, signedInHere: false };
    return mountSignInGate(options);
  }
  return __toCommonJS(src_exports);
})();

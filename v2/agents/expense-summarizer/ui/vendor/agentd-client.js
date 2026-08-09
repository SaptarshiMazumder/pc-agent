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
    PROTOCOL_VERSION: () => PROTOCOL_VERSION,
    agentIdFromPage: () => agentIdFromPage,
    authLogin: () => authLogin,
    authLogout: () => authLogout,
    authStatus: () => authStatus,
    fromPage: () => fromPage,
    loadSession: () => loadSession,
    mountSignInGate: () => mountSignInGate,
    platformConnect: () => platformConnect,
    platformStatus: () => platformStatus,
    resolveAuth: () => resolveAuth,
    resultText: () => resultText,
    saveSession: () => saveSession,
    signIn: () => signIn,
    signOut: () => signOut,
    signOutAndGate: () => signOutAndGate
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

  // src/client.ts
  function toWsUrl(target) {
    const u = new URL(target.url);
    if (u.protocol === "http:") u.protocol = "ws:";
    if (u.protocol === "https:") u.protocol = "wss:";
    if (target.token) u.searchParams.set("token", target.token);
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
  var AgentdClient = class {
    constructor(options = {}) {
      this.ws = null;
      this.input = null;
      this.nextId = 1;
      this.pending = /* @__PURE__ */ new Map();
      this.eventHandlers = /* @__PURE__ */ new Map();
      this.statusHandlers = /* @__PURE__ */ new Set();
      this.reconnectDelay = 1e3;
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
        this.reconnectDelay = 1e3;
        for (const handler of this.statusHandlers) handler("open");
      };
      ws.onmessage = (message) => this.handleFrame(JSON.parse(message.data));
      ws.onclose = () => {
        for (const [, pending] of this.pending) pending.reject(new Error("connection closed"));
        this.pending.clear();
        for (const handler of this.statusHandlers) handler("closed");
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
    history(sessionKey, agentId) {
      return this.request("sessions.history", { sessionKey, ...agentId ? { agentId } : {} });
    }
    send(opts) {
      return this.request("chat.send", { sessionKey: "default", ...opts });
    }
    abort(sessionKey) {
      return this.request("chat.abort", { sessionKey });
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
    onRun(sessionKey, handler) {
      return this.on("chat.event", (payload) => {
        if (payload.sessionKey === sessionKey) {
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
      const origin2 = toHttpOrigin(new URL(this.lastTarget.url).toString());
      const u = new URL("/file", origin2);
      u.searchParams.set("path", path);
      if (this.lastTarget.token) u.searchParams.set("token", this.lastTarget.token);
      return u.toString();
    }
  };
  function fromPage(options = {}) {
    const here = new URL(window.location.href);
    const token = here.searchParams.get("token") || "";
    const scope = here.searchParams.get("scope") || "";
    const client = new AgentdClient(options);
    client.connect({ url: here.origin, token: token || void 0, scope: scope || void 0 });
    return client;
  }

  // src/auth.ts
  var CONNECT_TIMEOUT_MS = 1e4;
  async function ask(method, params, opts) {
    if (opts.client) return opts.client.request(method, params);
    const client = fromPage({ clientName: "agentd-sdk-auth" });
    try {
      await opened(client, opts.timeoutMs ?? CONNECT_TIMEOUT_MS);
      return await client.request(method, params);
    } finally {
      client.close();
    }
  }
  function opened(client, timeoutMs) {
    if (client.connected) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        stop();
        reject(new Error(`the daemon did not answer within ${timeoutMs}ms`));
      }, timeoutMs);
      const stop = client.onStatus((status) => {
        if (status !== "open") return;
        clearTimeout(timer);
        stop();
        resolve();
      });
    });
  }
  function shape(raw) {
    return {
      available: !!raw?.available,
      signedIn: !!raw?.signedIn,
      email: String(raw?.email || ""),
      accountId: String(raw?.accountId || "")
    };
  }
  async function authStatus(opts = {}) {
    return shape(await ask("auth.status", {}, opts));
  }
  async function authLogin(args, opts = {}) {
    const raw = await ask(
      "auth.login",
      { email: args.email, password: args.password, signup: !!args.signup },
      opts
    );
    return shape({ available: true, ...raw });
  }
  async function authLogout(opts = {}) {
    const raw = await ask("auth.logout", {}, opts);
    return shape({ available: true, ...raw });
  }

  // src/platform.ts
  var DEFAULTS = {
    timeoutMs: 15e3,
    confirmAttempts: 6,
    confirmDelayMs: 1e3
  };
  var SERVER_SIDE_KEYS = "server-side";
  function origin(opts = {}) {
    if (opts.origin) return opts.origin.replace(/\/$/, "");
    if (typeof location === "undefined") throw new Error("no origin: pass options.origin");
    return location.origin;
  }
  function agentIdFromPage() {
    if (typeof location === "undefined") return "";
    const here = new URL(location.href);
    const scope = here.searchParams.get("scope") || "";
    const scoped = /^agent:(.+)$/.exec(scope);
    if (scoped) return scoped[1];
    const path = /\/apps\/([^/]+)/.exec(here.pathname);
    return path ? decodeURIComponent(path[1]) : "";
  }
  function daemonToken(opts = {}) {
    if (typeof opts.token === "string") return opts.token;
    if (typeof location === "undefined") return "";
    try {
      return new URL(location.href).searchParams.get("token") || "";
    } catch {
      return "";
    }
  }
  function daemonUrl(path, params, opts = {}) {
    const u = new URL(path, `${origin(opts)}/`);
    const token = daemonToken(opts);
    if (token) u.searchParams.set("token", token);
    for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
    return u.toString();
  }
  function storageKey(opts = {}) {
    if (opts.storageKey) return opts.storageKey;
    const id = agentIdFromPage();
    return `agentd.session.${id || "app"}`;
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
  async function getJson(url, timeoutMs, what) {
    const r = await withTimeout(fetch(url, { cache: "no-store" }), timeoutMs, what);
    const text = await r.text();
    let body = {};
    try {
      body = text ? JSON.parse(text) : {};
    } catch {
    }
    if (!r.ok) throw new Error(String(body?.error || `HTTP ${r.status}`));
    return body;
  }
  async function postJson(url, payload, timeoutMs, what) {
    const r = await withTimeout(
      fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload)
      }),
      timeoutMs,
      what
    );
    const text = await r.text();
    let body = {};
    try {
      body = text ? JSON.parse(text) : {};
    } catch {
    }
    if (!r.ok) throw new Error(String(body?.error || body?.detail || `HTTP ${r.status}`));
    return body;
  }
  function proxyOf(raw) {
    return raw && (raw.modelProxy || raw.modelGateway) || null;
  }
  function shape2(raw) {
    const accountsUrl = String(raw?.accountsUrl || "").replace(/\/$/, "");
    return {
      accountsUrl,
      hosted: !!accountsUrl,
      keysLive: !!proxyOf(raw)?.enabled,
      raw: raw || {}
    };
  }
  async function platformStatus(opts = {}) {
    const timeoutMs = opts.timeoutMs ?? DEFAULTS.timeoutMs;
    return shape2(await getJson(daemonUrl("/platform/status", {}, opts), timeoutMs, "platform status"));
  }
  async function platformConnect(session, opts = {}) {
    const timeoutMs = opts.timeoutMs ?? DEFAULTS.timeoutMs;
    const attempts = opts.confirmAttempts ?? DEFAULTS.confirmAttempts;
    const delay = opts.confirmDelayMs ?? DEFAULTS.confirmDelayMs;
    const url = daemonUrl("/platform/connect", { session }, opts);
    try {
      const status = shape2(await getJson(url, timeoutMs, "platform connect"));
      if (status.keysLive) return status;
    } catch (e) {
      const message = String(e?.message || e).toLowerCase();
      if (message.includes(SERVER_SIDE_KEYS)) {
        const status = await platformStatus(opts);
        return { ...status, keysLive: true };
      }
    }
    for (let i = 0; i < attempts; i++) {
      await new Promise((r) => setTimeout(r, delay));
      try {
        const status = await platformStatus(opts);
        if (status.keysLive) return status;
      } catch {
      }
    }
    throw new Error("signed in, but this device did not activate \u2014 try again");
  }
  function loadSession(opts = {}) {
    try {
      const raw = localStorage.getItem(storageKey(opts));
      const parsed = raw ? JSON.parse(raw) : null;
      return parsed && parsed.token ? parsed : null;
    } catch {
      return null;
    }
  }
  function saveSession(value, opts = {}) {
    try {
      if (value) localStorage.setItem(storageKey(opts), JSON.stringify(value));
      else localStorage.removeItem(storageKey(opts));
    } catch {
    }
  }
  async function signIn(args, opts = {}) {
    const timeoutMs = opts.timeoutMs ?? DEFAULTS.timeoutMs;
    const status = await platformStatus(opts);
    if (!status.hosted) throw new Error("this build has no sign-in server configured");
    const email = args.email.trim().toLowerCase();
    if (args.signup) {
      await postJson(`${status.accountsUrl}/signup`, { email, password: args.password }, timeoutMs, "signup");
    }
    const login = await postJson(
      `${status.accountsUrl}/login`,
      { email, password: args.password },
      timeoutMs,
      "login"
    );
    const token = String(login?.token || login?.session || "");
    if (!token) throw new Error("the accounts server returned no session token");
    const connected = await platformConnect(token, opts);
    saveSession({ token, email }, opts);
    return { ...connected, token };
  }
  function signOut(opts = {}) {
    saveSession(null, opts);
  }
  async function resolveAuth(opts = {}) {
    const status = await platformStatus(opts);
    if (!status.hosted || status.keysLive) {
      return { needsSignIn: false, status, token: loadSession(opts)?.token || "" };
    }
    const stored = loadSession(opts);
    if (stored?.token) {
      try {
        const reconnected = await platformConnect(stored.token, opts);
        if (reconnected.keysLive) {
          return { needsSignIn: false, status: reconnected, token: stored.token };
        }
      } catch {
      }
      saveSession(null, opts);
    }
    return { needsSignIn: true, status, token: "" };
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
  async function mountSignInGate(options = {}) {
    const allowSignup = options.allowSignup !== false;
    const product = options.product || typeof document !== "undefined" && document.title || "this app";
    const blurb = options.blurb || "Sign in to continue.";
    const state = await authStatus(options);
    if (!state.available || state.signedIn) {
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

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
    fromPage: () => fromPage,
    resultText: () => resultText
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
      const origin = toHttpOrigin(new URL(this.lastTarget.url).toString());
      const u = new URL("/file", origin);
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
  return __toCommonJS(src_exports);
})();

/* The settings view — the whole agentd surface, plus this agent's own overrides.

   TWO LAYERS, ONE PAGE.

     daemon    agentd.config.json + .env, shared by every agent on the machine
     agent     config.agents["agent-builder"], this agent alone

   `config.agents` is an ordinary config key (same nested shape as `plugins`), so both layers
   arrive in ONE config.get and leave in ONE config.set. There is no second store.

   The override flag decides which layer wins AT RUN TIME, key by key: for each knob this agent
   has set, its value; for the rest, the daemon's. Off, and the agent's block is ignored
   entirely — kept on disk, just dormant.

   EVERY OVERRIDABLE ROW SAYS WHICH LAYER IT CAME FROM. That is not decoration. This page once
   showed "Model: GPT-5" while every turn was answered by gemini, because cost-efficiency was
   silently overriding it — a value with no provenance is how that stayed invisible.

   Edits are held in `draft`, a copy of the daemon's values that dotted-key paths write into
   (`cost_efficiency.enabled`, `agents.agent-builder.model`). The patch sent to the daemon is
   the set of TOP-LEVEL keys that differ, which is exactly what config.set accepts.

   Provider keys are the reason the page exists at all — BYOK. `env` says which are set;
   `envValues` carries the actual strings and is ABSENT for an installed agent (the daemon
   strips it), so the field renders as "•••• saved" with no way to read it back. Intended, not
   a failure: a page that shipped inside someone else's package must never lift the user's key.
   Keys are DAEMON-WIDE and deliberately not overridable — one .env, one source. */

window.Settings = (function () {
  const $ = (id) => document.getElementById(id)
  let client = null
  let data = null
  let draft = {}     // pending config edits, nested; seeded from data.values on every load
  const keys = {}    // pending .env edits
  let platform = null      // platform.status — run mode, live from the daemon
  let auth = null          // auth.status — who is signed in, live from the daemon
  let platformError = ''

  // Whose page this is. The connect URL carries `scope=agent:<id>` — the daemon strips that
  // prefix before it forces the agent onto our requests, so anything we key BY agent id has to
  // strip it too, or it writes a block under "agent:<id>" that the resolver never looks for.
  const AGENT_ID =
    (new URL(location.href).searchParams.get('scope') || '').replace(/^agent:/, '') ||
    'agent-builder'

  // ── nested values, addressed by dotted path ───────────────────────────────
  // Agent ids are kebab-case slugs, so `agents.<id>.model` splits unambiguously.
  function getPath(obj, path) {
    return path.split('.').reduce((acc, k) => (acc == null ? undefined : acc[k]), obj)
  }
  function setPath(obj, path, value) {
    const parts = path.split('.')
    const root = { ...obj }
    let cur = root
    for (let i = 0; i < parts.length - 1; i++) {
      const k = parts[i]
      cur[k] = cur[k] && typeof cur[k] === 'object' ? { ...cur[k] } : {}
      cur = cur[k]
    }
    cur[parts[parts.length - 1]] = value
    return root
  }

  const OVERRIDE_PATH = `agents.${AGENT_ID}.override_default`
  /** Default TRUE — the flag exists to be turned OFF, and must read the same way the resolver
   *  does or the page would describe behaviour the daemon does not have. */
  const overriding = () => getPath(draft, OVERRIDE_PATH) !== false

  /** Where a field's value lives. An agent-scoped field writes into this agent's own block
   *  while the override is on; with it off the agent's block does nothing, so the row shows
   *  the daemon's value and says so. */
  const pathFor = (f) => (f.agent && overriding() ? `agents.${AGENT_ID}.${f.key}` : f.key)

  /** The value to SHOW: this agent's if it set one, else the daemon's. Mirrors resolve(). */
  function valueOf(f) {
    if (f.agent && overriding()) {
      const own = getPath(draft, `agents.${AGENT_ID}.${f.key}`)
      if (own !== undefined) return own
    }
    return getPath(draft, f.key)
  }

  /** "this agent" or "daemon" — the badge. */
  const sourceOf = (f) =>
    f.agent && overriding() && getPath(draft, `agents.${AGENT_ID}.${f.key}`) !== undefined
      ? 'this agent'
      : 'daemon'

  const GROUPS = [
    {
      title: 'This agent',
      help: `Settings for ${AGENT_ID} alone. With the override on, these win over the ` +
            'daemon-wide values below — one knob at a time: anything left unset here keeps ' +
            'using the daemon\'s. Turning the override off does not erase them.',
      agentToggle: true,
      agent: true,
      fields: [
        { key: 'model', label: 'Model', type: 'select', catalog: 'models',
          help: 'The brain for this agent\'s runs.' },
        { key: 'reasoning_effort', label: 'Reasoning effort', type: 'select',
          options: ['minimal', 'low', 'medium', 'high'] },
        { key: 'max_turns', label: 'Max turns per run', type: 'number',
          help: 'Tool-call rounds before the run stops on its own.' },
        { key: 'verify_tool', label: 'Self-verify', type: 'toggle',
          help: 'Review its own draft before replying.' },
        { key: 'memory_enabled', label: 'Long-term memory', type: 'toggle' },
      ],
      costEfficiency: true,
    },
    {
      title: 'API keys',
      help: 'Your own provider keys. Stored in the .env beside the config, never in the ' +
            'config file itself, and read straight from the environment by the model layer. ' +
            'Shared by every agent on this machine — keys are not per-agent.',
      secrets: true,
    },
    {
      title: 'Daemon defaults',
      help: 'What every agent uses unless it overrides it. This is the same surface the ' +
            'JARVIS settings window edits.',
      fields: [
        { key: 'agent_name', label: 'Assistant name', type: 'text' },
        { key: 'model', label: 'Model', type: 'select', catalog: 'models' },
        { key: 'reasoning_effort', label: 'Reasoning effort', type: 'select',
          options: ['minimal', 'low', 'medium', 'high'] },
        { key: 'max_turns', label: 'Max turns per run', type: 'number' },
      ],
      costEfficiency: true,
    },
    {
      title: 'Behaviour',
      fields: [
        { key: 'completeness_check', label: 'Completeness check', type: 'toggle' },
        { key: 'execution_contract', label: 'Execution contract', type: 'toggle' },
        { key: 'skill_workshop', label: 'Skill workshop', type: 'toggle',
          help: 'Agents may author reusable SKILL.md playbooks at runtime.' },
        { key: 'mcp_workshop', label: 'MCP workshop', type: 'toggle' },
      ],
    },
    {
      title: 'Memory & context',
      fields: [
        { key: 'memory_auto_recall', label: 'Auto-recall', type: 'toggle' },
        { key: 'context_max_messages', label: 'Context window (messages)', type: 'number' },
        { key: 'workspace_index_enabled', label: 'Workspace index', type: 'toggle' },
      ],
    },
    {
      title: 'Delegation',
      fields: [
        { key: 'subagents_enabled', label: 'Sub-agents', type: 'toggle' },
        { key: 'subagent_max', label: 'Max concurrent', type: 'number' },
        { key: 'subagent_max_depth', label: 'Max depth', type: 'number' },
        { key: 'agent_messaging_enabled', label: 'Agent-to-agent messaging', type: 'toggle' },
      ],
    },
    {
      title: 'Autonomy',
      help: 'Scheduled and self-woken runs.',
      fields: [
        { key: 'autonomy_enabled', label: 'Autonomy', type: 'toggle' },
        { key: 'heartbeat_default_interval', label: 'Heartbeat interval', type: 'text' },
        { key: 'heartbeat_active_hours', label: 'Active hours', type: 'text' },
        { key: 'notify_enabled', label: 'Notifications', type: 'toggle' },
      ],
    },
    {
      title: 'Tools',
      fields: [
        { key: 'computer_enabled', label: 'Computer use', type: 'toggle' },
        { key: 'tool_timeout_default', label: 'Default timeout (s)', type: 'number' },
        { key: 'tool_retries_default', label: 'Default retries', type: 'number' },
        { key: 'parallel_search_enabled', label: 'Parallel search', type: 'toggle' },
      ],
    },
    {
      title: 'Paths',
      help: 'Where this daemon keeps things. Changing these takes effect on restart.',
      fields: [
        { key: 'workspace', label: 'Workspace', type: 'text' },
        { key: 'state_dir', label: 'State directory', type: 'text' },
        { key: 'agents_dir', label: 'Agents directory', type: 'text' },
      ],
    },
  ]

  function init(c) {
    client = c
    // The daemon pushes this whenever identity or run mode changes ANYWHERE — this window, the
    // agentd window, another agent. Both facts are machine-wide, so a page holding its own stale
    // copy would keep offering to sign out of an account that is already gone.
    client.on('auth.changed', (state) => {
      auth = state
      if (platform) platform = { ...platform, mode: state.mode, canUseCloud: state.canUseCloud }
      if (data) render()
    })
  }

  async function load() {
    const body = $('settingsBody')
    try {
      data = await client.request('config.get')
    } catch (e) {
      body.textContent = ''
      body.append(el('div', 'loading', `could not load settings: ${(e && e.message) || e}`))
      return
    }
    // a fresh copy every load, so Save -> reload leaves nothing stale behind
    draft = JSON.parse(JSON.stringify(data.values || {}))
    await loadPlatform()
    render()
  }

  /** Run mode comes from the DAEMON, not from this page's storage — see modeSection. A failure
   *  is remembered rather than swallowed: the section renders the reason, because a Run mode
   *  control that quietly vanishes looks identical to a build that has no Cloud. */
  async function loadPlatform() {
    platformError = ''
    try {
      // ONE call, and it is the SDK's: identity and run mode are this client's own state, so
      // there is nothing to ask the daemon for beyond "is there an accounts service, and is there
      // a proxy to switch to". Both used to be daemon methods, which made them machine-wide.
      auth = await window.agentd.authStatus({ client })
      platform = auth
    } catch (e) {
      platform = null
      auth = null
      platformError = (e && e.message) || String(e)
    }
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag)
    if (cls) n.className = cls
    if (text != null) n.textContent = text
    return n
  }

  function render() {
    const body = $('settingsBody')
    body.textContent = ''
    const values = data.values || {}
    const pinned = data.envOverrides || {}

    // Account first, Run mode second — that is the order they depend on. Cloud needs somebody to
    // bill, so the second control greys out until the first one is filled in.
    body.append(accountSection())
    body.append(modeSection())

    for (const group of GROUPS) {
      const sec = el('section', 'set-group')
      sec.append(el('h2', null, group.title))
      if (group.help) sec.append(el('p', 'ghelp', group.help))

      if (group.agentToggle) sec.append(overrideRow())

      if (group.secrets) {
        for (const name of data.providerKeys || []) sec.append(secretRow(name))
      } else {
        for (const f of group.fields || []) {
          // the daemon does not expose it -> do not invent a control for it
          if (!(f.key in values)) continue
          // With cost efficiency ON, `model` is NOT what runs — the router picks per turn.
          // Leaving the dropdown on screen would display a value with no effect, which is the
          // exact confusion this page exists to end.
          if (group.costEfficiency && f.key === 'model' && ceEnabled(!!group.agent)) continue
          sec.append(row({ ...f, agent: !!group.agent }, pinned[f.key]))
        }
        // Cost efficiency last in its group: it CHANGES what the Model row above means, so it
        // reads as a modifier of the section rather than a knob of its own.
        if (group.costEfficiency) costEfficiencyRows(sec, !!group.agent)
      }
      body.append(sec)
    }

    const bar = el('div', 'set-bar')
    const msg = el('span', 'set-msg', data.keysLocked
      ? 'Provider keys are managed by the platform on this install.'
      : `agentd ${data.version || ''} · ${data.effectiveModel || ''}`)
    msg.id = 'setMsg'
    const save = el('button', 'prime-btn', 'Save changes')
    save.id = 'setSave'
    save.disabled = true
    save.addEventListener('click', () => void commit())
    bar.append(msg, save)
    body.append(bar)
  }

  /** Account — WHO you are. Separate from Run mode below, which is who PAYS.
   *
   *  Both live here because they are the two facts a user has to be able to see and change from
   *  inside the agent they are using. Before this, signing in meant leaving the agent and opening
   *  agentd, and there was no way to sign out from here at all.
   *
   *  SIGNING OUT IS THE DAEMON'S SIGN-OUT, not a local flag. It drops the identity token AND
   *  re-applies the run mode, so platform billing stops in the same step — the state "signed out
   *  but still metering your account" is not reachable from here. */
  function accountSection() {
    const sec = el('section', 'set-group')
    sec.append(el('h2', null, 'Account'))

    if (platformError) {
      sec.append(el('div', 'loading', `could not read the account: ${platformError}`))
      return sec
    }
    if (!(auth && auth.available)) {
      sec.append(el('p', 'ghelp', 'This build has no accounts service, so there is nobody to sign ' +
        'in as. It runs on the API keys set below.'))
      return sec
    }

    const signedIn = !!(auth && auth.signedIn)
    sec.append(el('p', 'ghelp', signedIn
      ? 'Signing out also stops platform billing — Run mode falls back to your own API keys.'
      : 'Sign in to use Cloud mode. Your own API keys keep working either way.'))

    const wrap = el('div', 'field')
    const left = el('div')
    left.append(el('label', null, signedIn ? (auth.email || 'Signed in') : 'Not signed in'))
    left.append(el('span', 'fhelp', signedIn
      ? 'This machine is signed in to the platform.'
      : 'No account on this machine.'))
    wrap.append(left)

    const btn = el('button', 'prime-btn', signedIn ? 'Sign out' : 'Sign in')
    btn.addEventListener('click', () => void (signedIn ? doSignOut() : doSignIn()))
    wrap.append(btn)
    sec.append(wrap)
    return sec
  }

  async function doSignIn() {
    // The SDK's gate: the daemon performs the exchange and keeps the token, so nothing here ever
    // holds a credential. Resolves once somebody is signed in, or immediately if they already are.
    await window.agentd.mountSignInGate({ client })
    await loadPlatform()
    render()
  }

  async function doSignOut() {
    await window.agentd.authLogout({ client })
    await loadPlatform()
    render()
  }

  /** Run mode — Local (your own API keys) vs Cloud (platform keys, metered to your account).
   *
   *  MACHINE-WIDE, AND IT SAYS SO. The model proxy is one piece of daemon state shared by every
   *  agent, so this control flips the others too. A toggle that silently changed every other
   *  agent on the machine would be the worst kind of surprise.
   *
   *  The daemon is the source of truth. This used to live in the agentd desktop app's own
   *  localStorage, which no other page could read — which is exactly why changing mode meant
   *  leaving the agent, opening agentd, and switching there.
   *
   *  Default is Cloud: signing in puts a daemon with no stated preference onto platform keys with
   *  nothing pressed. Choosing Local is remembered, so the next sign-in does not undo it. */
  function modeSection() {
    const sec = el('section', 'set-group')
    sec.append(el('h2', null, 'Run mode'))
    sec.append(el('p', 'ghelp',
      'Who pays for model calls. This applies to EVERY agent on this machine, not just this one.'))

    if (platformError) {
      sec.append(el('div', 'loading', `could not read the run mode: ${platformError}`))
      return sec
    }

    const mode = (platform && platform.mode) || 'local'
    const cloud = mode === 'cloud'
    const canCloud = !!(platform && platform.canUseCloud)
    const chosen = (platform && platform.modePreference) || ''

    const wrap = el('div', 'field')
    const left = el('div')
    left.append(el('label', null, cloud ? 'Cloud — platform keys' : 'Local — your own API keys'))
    left.append(el('span', 'fhelp', cloud
      ? 'Model calls route through the hosted proxy and are metered to your account.'
      : 'Model calls go straight to the providers, with the keys set below.'))
    // "Cloud" and "Cloud because nobody said otherwise" are different states, and only the
    // second one changes by itself when someone signs in.
    if (!chosen) left.append(el('span', 'fhelp', 'default — you have not chosen yet'))
    if (!cloud && !canCloud) {
      left.append(el('span', 'fhelp', platform && platform.accountsUrl
        ? 'Sign in to use Cloud.'
        : 'This build has no model proxy, so Cloud is unavailable.'))
    }
    wrap.append(left)

    const t = el('button', `toggle ${cloud ? 'on' : ''}`)
    t.disabled = !cloud && !canCloud
    t.addEventListener('click', () => void switchMode(cloud ? 'local' : 'cloud'))
    wrap.append(t)
    sec.append(wrap)
    return sec
  }

  async function switchMode(next) {
    const say = (cls, text) => {
      const m = $('setMsg')
      if (!m) return
      m.className = `set-msg${cls ? ' ' + cls : ''}`
      m.textContent = text
    }
    say('', next === 'cloud' ? 'switching to Cloud…' : 'switching to Local…')
    try {
      // No token is passed. The daemon signed the user in and kept the session token; a page
      // that never receives a credential cannot leak one.
      await window.agentd.setRunMode(next, { client })
      await loadPlatform()
      render()
      say('ok', `Now running in ${next === 'cloud' ? 'Cloud' : 'Local'} mode.`)
    } catch (e) {
      say('bad', `could not switch: ${(e && e.message) || e}`)
    }
  }

  /** The override flag. Re-renders on change, because it moves every row in its group between
   *  the two layers — showing the old layer's values afterwards would be a lie. */
  function overrideRow() {
    const wrap = el('div', 'field override')
    const left = el('div')
    left.append(el('label', null, 'Override JARVIS settings'))
    left.append(el('span', 'fhelp', overriding()
      ? 'On — this agent\'s own values win, one knob at a time.'
      : 'Off — the daemon\'s values are in force. Anything set here is kept, just dormant.'))
    wrap.append(left)

    const t = el('button', `toggle ${overriding() ? 'on' : ''}`)
    t.addEventListener('click', () => {
      draft = setPath(draft, OVERRIDE_PATH, !overriding())
      render()   // the whole group changes layer
      dirty()
    })
    wrap.append(t)
    return wrap
  }

  /** Cost efficiency, and the Model row it overrules.
   *
   *  When it is ON the daemon's `model` is NOT what runs — the router picks per turn. Showing
   *  a Model dropdown beside it would be showing a value that has no effect, which is exactly
   *  the confusion this whole change came from. So: on, and the two brains replace it. */
  /** Is cost efficiency on for this layer? Decides whether a `model` row means anything. */
  function ceEnabled(agentScoped) {
    return !!(valueOf({ agent: agentScoped, key: 'cost_efficiency' }) || {}).enabled
  }

  function costEfficiencyRows(sec, agentScoped) {
    const base = { agent: agentScoped, type: 'select', catalog: 'models' }
    const on = ceEnabled(agentScoped)

    const toggle = row({
      agent: agentScoped, key: 'cost_efficiency.enabled', label: 'Cost efficiency', type: 'toggle',
      help: 'Run a cheap model on ordinary turns and only switch to a stronger one when the ' +
            'turn actually involves an image.',
      onChange: () => render(),   // the rows below it appear/disappear
    })
    sec.append(toggle)

    if (!on) return
    sec.append(row({ ...base, key: 'cost_efficiency.text_model', label: 'Text model',
      help: 'Ordinary turns — the one you talk to most.' }))
    sec.append(row({ ...base, key: 'cost_efficiency.vision_model', label: 'Vision model',
      help: 'Turns carrying an image, and every turn after one enters the chat.' }))
  }

  function dirty() {
    const save = $('setSave')
    if (save) save.disabled = !(Object.keys(patch()).length || Object.keys(keys).length)
  }

  /** What actually goes to the daemon: the TOP-LEVEL keys whose value differs from what was
   *  loaded. Nested edits ride inside their own top-level key, which is why config.set needs
   *  no notion of paths. */
  function patch() {
    const out = {}
    const values = data.values || {}
    for (const k of Object.keys(draft)) {
      if (JSON.stringify(draft[k]) !== JSON.stringify(values[k])) out[k] = draft[k]
    }
    return out
  }

  function row(f, pinnedBy) {
    const path = pathFor(f)
    const value = valueOf(f)
    const wrap = el('div', 'field')
    const left = el('div')
    left.append(el('label', null, f.label))
    if (f.help) left.append(el('span', 'fhelp', f.help))
    // A value fixed by an environment variable cannot be changed from here. Say WHY it is
    // disabled — a greyed-out control with no explanation reads as a bug.
    if (pinnedBy) left.append(el('span', 'fhelp pinned', `pinned by ${pinnedBy} in .env`))
    else if (f.agent) {
      const src = sourceOf(f)
      left.append(el('span', `fhelp src ${src === 'daemon' ? 'from-daemon' : 'from-agent'}`,
        src === 'daemon' ? 'using the daemon value' : 'set for this agent'))
    }
    wrap.append(left)

    const change = (v) => {
      draft = setPath(draft, path, v)
      if (f.onChange) f.onChange()
      dirty()
    }

    if (f.type === 'toggle') {
      const t = el('button', `toggle ${value ? 'on' : ''}`)
      t.disabled = !!pinnedBy
      t.addEventListener('click', () => {
        const next = !t.classList.contains('on')
        t.classList.toggle('on', next)
        change(next)
      })
      wrap.append(t)
      return wrap
    }

    let input
    const opts = f.catalog ? (data.catalogs || {})[f.catalog] : f.options
    if (f.type === 'select' && opts && opts.length) {
      input = el('select')
      for (const o of opts) {
        const val = typeof o === 'string' ? o : (o.value ?? o.id ?? '')
        const lab = typeof o === 'string' ? o : (o.label ?? o.name ?? val)
        const opt = el('option', null, lab)
        opt.value = val
        input.append(opt)
      }
      // a value the catalog does not list must still be shown, not silently swapped for the
      // first option — that would change the setting just by rendering the page
      if (value != null && ![...input.options].some((o) => o.value === String(value))) {
        const opt = el('option', null, `${value} (current)`)
        opt.value = String(value)
        input.prepend(opt)
      }
      input.value = String(value ?? '')
    } else {
      input = el('input')
      input.type = f.type === 'number' ? 'number' : 'text'
      input.value = value == null ? '' : String(value)
    }
    input.disabled = !!pinnedBy
    input.addEventListener('change', () => {
      change(f.type === 'number' ? Number(input.value) : input.value)
    })
    wrap.append(input)
    return wrap
  }

  function secretRow(name) {
    const wrap = el('div', 'field')
    const left = el('div')
    left.append(el('label', null, name.replace(/_API_KEY$|_KEY$|_API_TOKEN$/, '')))
    const isSet = (data.env || {})[name]
    // envValues is absent for an installed agent — say "saved", never show the value
    const revealable = Object.prototype.hasOwnProperty.call(data, 'envValues')
    left.append(el('span', 'fhelp', isSet
      ? (revealable ? name : `${name} · saved (hidden for installed agents)`)
      : name))
    wrap.append(left)

    const input = el('input')
    input.type = 'password'
    input.placeholder = isSet ? '•••••••• saved' : 'not set'
    if (revealable && isSet) input.value = (data.envValues || {})[name] || ''
    input.disabled = !!data.keysLocked
    input.addEventListener('change', () => { keys[name] = input.value; dirty() })
    wrap.append(input)
    return wrap
  }

  async function commit() {
    const msg = $('setMsg')
    const save = $('setSave')
    save.disabled = true
    msg.className = 'set-msg'
    msg.textContent = 'saving…'
    try {
      const params = {}
      const p = patch()
      if (Object.keys(p).length) params.patch = p
      if (Object.keys(keys).length) params.keys = keys
      const res = await client.request('config.set', params)
      for (const k of Object.keys(keys)) delete keys[k]
      msg.className = 'set-msg ok'
      msg.textContent = res && res.restartRequired
        ? 'Saved — restart the daemon for some of these to take effect.'
        : 'Saved.'
      await load()   // reseeds draft from what the daemon actually stored
    } catch (e) {
      // Leave the edits in place and re-enable Save. Clearing the form on failure would throw
      // away what the user typed and tell them nothing.
      msg.className = 'set-msg bad'
      msg.textContent = `could not save: ${(e && e.message) || e}`
      save.disabled = false
    }
  }

  return { init, load }
})()

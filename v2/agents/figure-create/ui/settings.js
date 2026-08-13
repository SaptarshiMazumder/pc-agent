/* Settings — this agent's own model, and the user's API key (BYOK).

   ─────────────────────────────────────────────────────────────────────────────────────────
   TWO LAYERS. UNDERSTAND THIS BEFORE ADDING A KNOB.

     daemon   agentd.config.json + .env — SHARED by every agent on the machine
     agent    config.agents["<this-agent>"] — this agent alone

   `config.agents` is an ordinary config key (same nested shape as `plugins`), so both layers
   arrive in ONE config.get and leave in ONE config.set. There is no second file.

   The "Override JARVIS settings" flag decides which wins at run time, KEY BY KEY: for each
   knob this agent has set, its value; for the rest, the daemon's. Off, and this agent's block
   is ignored entirely — kept on disk, just dormant.

   ─────────────────────────────────────────────────────────────────────────────────────────
   WHY THE DAEMON LIST IS SHORT

   Anything you add to the daemon groups is a knob THIS agent's window offers over the user's
   whole install. The daemon exposes ~50 keys; three kinds belong on an agent's page:

     • the provider key   — without it nothing works. This is the point of the page.
     • the model          — the one choice that visibly changes how the agent behaves.
     • a few behaviours   — self-verify, memory.

   `port`, `state_dir`, `workspace`, `agents_dir`, `subagent_max` and the tool timeouts are
   machine plumbing. An agent offering to change the daemon's port is offering to break the
   user's install from inside a package they trusted for one job. Add a key only when THIS
   agent needs it — `config.get` returns everything the daemon accepts, so log `data.values`
   to see the list.

   API KEYS ARE NEVER PER-AGENT. One shared .env, one source. `env` says which are set;
   `envValues` carries the strings and is ABSENT once this agent has been installed from a
   package — the daemon strips it — so the field shows "•••• saved" with no way to read it
   back. Deliberate: a page that shipped inside someone else's download must never be able to
   lift the user's key. */

window.Settings = (function () {
  const $ = (id) => document.getElementById(id)
  let client = null
  let data = null
  let draft = {}     // pending config edits, nested; reseeded from data.values on every load
  const keys = {}    // pending .env edits
  let platform = null      // platform.status — run mode, live from the daemon
  let auth = null          // auth.status — who is signed in, live from the daemon
  let platformError = ''

  // Whose page this is. The connect URL carries `scope=agent:<id>` — the daemon strips that
  // prefix before it forces the agent onto our requests, so anything we key BY agent id has to
  // strip it too, or it writes a block under "agent:<id>" that the resolver never looks for.
  const AGENT_ID = (new URL(location.href).searchParams.get('scope') || '').replace(/^agent:/, '')

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
  /** Default TRUE — the flag exists to be turned OFF, and this must read the same way the
   *  daemon's resolver does, or the page describes behaviour the daemon does not have. */
  const overriding = () => !!AGENT_ID && getPath(draft, OVERRIDE_PATH) !== false

  const pathFor = (f) => (f.agent && overriding() ? `agents.${AGENT_ID}.${f.key}` : f.key)

  /** The value to SHOW: this agent's if it set one, else the daemon's. Mirrors the resolver. */
  function valueOf(f) {
    if (f.agent && overriding()) {
      const own = getPath(draft, `agents.${AGENT_ID}.${f.key}`)
      if (own !== undefined) return own
    }
    return getPath(draft, f.key)
  }

  const sourceOf = (f) =>
    f.agent && overriding() && getPath(draft, `agents.${AGENT_ID}.${f.key}`) !== undefined
      ? 'agent'
      : 'daemon'

  const GROUPS = [
    {
      title: 'This agent',
      help: 'Settings for this agent alone. With the override on they win over the shared ' +
            'values below — one knob at a time: anything left unset keeps using the shared ' +
            'one. Turning the override off does not erase them.',
      agentToggle: true,
      agent: true,
      fields: [
        { key: 'model', label: 'Model', type: 'select', catalog: 'models' },
        { key: 'reasoning_effort', label: 'Reasoning effort', type: 'select',
          options: ['minimal', 'low', 'medium', 'high'] },
        { key: 'max_turns', label: 'Max turns per run', type: 'number',
          help: 'Tool-call rounds before a run stops on its own.' },
        { key: 'verify_tool', label: 'Self-verify', type: 'toggle',
          help: 'Review its own draft before replying.' },
        { key: 'memory_enabled', label: 'Long-term memory', type: 'toggle' },
      ],
      costEfficiency: true,
    },
    {
      title: 'API keys',
      help: 'Your own provider keys. Stored in the .env beside the config, never in the ' +
            'config file itself. Shared by every agent on this machine.',
      secrets: true,
    },
    {
      title: 'Shared defaults',
      help: 'What every agent on this machine uses unless it overrides it.',
      fields: [
        { key: 'model', label: 'Model', type: 'select', catalog: 'models' },
        { key: 'reasoning_effort', label: 'Reasoning effort', type: 'select',
          options: ['minimal', 'low', 'medium', 'high'] },
      ],
      costEfficiency: true,
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
      // Say what went wrong, in place. A settings page that silently shows nothing is
      // indistinguishable from one with nothing to show.
      body.textContent = ''
      body.append(el('div', 'loading', `could not load settings: ${(e && e.message) || e}`))
      return
    }
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
      // With no scope in the URL there is no agent to override for; showing the group would
      // offer edits that land nowhere.
      if (group.agent && !AGENT_ID) continue

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
   *  the two layers — leaving the old layer's values on screen would be a lie. */
  function overrideRow() {
    const wrap = el('div', 'field override')
    const left = el('div')
    left.append(el('label', null, 'Override JARVIS settings'))
    left.append(el('span', 'fhelp', overriding()
      ? 'On — this agent\'s own values win, one knob at a time.'
      : 'Off — the shared values are in force. Anything set here is kept, just dormant.'))
    wrap.append(left)

    const t = el('button', `toggle ${overriding() ? 'on' : ''}`)
    t.addEventListener('click', () => {
      draft = setPath(draft, OVERRIDE_PATH, !overriding())
      render()
      dirty()
    })
    wrap.append(t)
    return wrap
  }

  /** Cost efficiency, and the Model row it overrules.
   *
   *  When this is ON, `model` is NOT what runs — a router picks per turn. Leaving a Model
   *  dropdown visible beside it would display a value that has no effect, which is the exact
   *  confusion this page exists to prevent. */
  /** Is cost efficiency on for this layer? Decides whether a `model` row means anything. */
  function ceEnabled(agentScoped) {
    return !!(valueOf({ agent: agentScoped, key: 'cost_efficiency' }) || {}).enabled
  }

  function costEfficiencyRows(sec, agentScoped) {
    const base = { agent: agentScoped, type: 'select', catalog: 'models' }
    const on = ceEnabled(agentScoped)

    sec.append(row({
      agent: agentScoped, key: 'cost_efficiency.enabled', label: 'Cost efficiency', type: 'toggle',
      help: 'Run a cheap model on ordinary turns and only switch to a stronger one when the ' +
            'turn actually involves an image.',
      onChange: () => render(),
    }))

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

  /** What goes to the daemon: the TOP-LEVEL keys whose value differs from what was loaded.
   *  Nested edits ride inside their own top-level key, which is why config.set needs no
   *  notion of paths. */
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
    // A value fixed by an environment variable cannot be changed here. Say WHY it is disabled
    // — a greyed-out control with no explanation reads as a bug.
    if (pinnedBy) left.append(el('span', 'fhelp pinned', `pinned by ${pinnedBy} in .env`))
    else if (f.agent) {
      const src = sourceOf(f)
      left.append(el('span', `fhelp src ${src === 'daemon' ? 'from-daemon' : 'from-agent'}`,
        src === 'daemon' ? 'using the shared value' : 'set for this agent'))
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
      // a current value the catalog does not list must still be shown, not silently swapped
      // for the first option — that would change the setting just by rendering the page
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
    // envValues is absent for an installed agent — then say "saved", never show the value
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
      await load()
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

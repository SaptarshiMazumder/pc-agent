/* The settings view — the whole agentd surface, rendered from what config.get returns.

   The daemon is the source of truth for BOTH the values and the option lists (`catalogs`),
   so this file describes only LAYOUT: which knob belongs in which group, and how to draw it.
   A knob the daemon stops exposing simply stops rendering; one it adds needs a row here.

   Provider keys are the reason this exists at all — BYOK. `env` says which keys are set;
   `envValues` carries the actual strings and is ABSENT for an installed agent (the daemon
   strips it), so the field renders as "•••• saved" with no way to read it back. That is the
   intended behaviour, not a failure: a page that shipped inside someone else's package must
   never be able to lift the user's key. */

window.Settings = (function () {
  const $ = (id) => document.getElementById(id)
  let client = null
  let data = null
  const patch = {}   // pending config edits
  const keys = {}    // pending .env edits

  const GROUPS = [
    {
      title: 'Identity',
      help: 'How this daemon presents itself.',
      fields: [
        { key: 'agent_name', label: 'Assistant name', type: 'text' },
        { key: 'model', label: 'Model', type: 'select', catalog: 'models' },
        { key: 'reasoning_effort', label: 'Reasoning effort', type: 'select',
          options: ['minimal', 'low', 'medium', 'high'] },
        { key: 'max_turns', label: 'Max turns per run', type: 'number',
          help: 'Tool-call rounds before the run stops on its own.' },
      ],
    },
    {
      title: 'API keys',
      help: 'Your own provider keys. Stored in the .env beside the config, never in the ' +
            'config file itself, and read straight from the environment by the model layer.',
      secrets: true,
    },
    {
      title: 'Behaviour',
      fields: [
        { key: 'verify_tool', label: 'Self-verify', type: 'toggle',
          help: 'Let the agent review its own draft before replying.' },
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
        { key: 'memory_enabled', label: 'Long-term memory', type: 'toggle' },
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

  function init(c) { client = c }

  async function load() {
    const body = $('settingsBody')
    try {
      data = await client.request('config.get')
    } catch (e) {
      body.textContent = ''
      body.append(el('div', 'loading', `could not load settings: ${(e && e.message) || e}`))
      return
    }
    render()
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

    for (const group of GROUPS) {
      const sec = el('section', 'set-group')
      sec.append(el('h2', null, group.title))
      if (group.help) sec.append(el('p', 'ghelp', group.help))

      if (group.secrets) {
        for (const name of data.providerKeys || []) sec.append(secretRow(name))
      } else {
        for (const f of group.fields) {
          if (!(f.key in values)) continue        // the daemon does not expose it -> do not invent it
          sec.append(row(f, values[f.key], pinned[f.key]))
        }
      }
      body.append(sec)
    }

    // save bar
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

  function dirty() {
    const save = $('setSave')
    if (save) save.disabled = !(Object.keys(patch).length || Object.keys(keys).length)
  }

  function row(f, value, pinnedBy) {
    const wrap = el('div', 'field')
    const left = el('div')
    left.append(el('label', null, f.label))
    if (f.help) left.append(el('span', 'fhelp', f.help))
    if (pinnedBy) left.append(el('span', 'fhelp pinned', `pinned by ${pinnedBy} in .env`))
    wrap.append(left)

    if (f.type === 'toggle') {
      const t = el('button', `toggle ${value ? 'on' : ''}`)
      t.disabled = !!pinnedBy
      t.addEventListener('click', () => {
        const next = !t.classList.contains('on')
        t.classList.toggle('on', next)
        patch[f.key] = next
        dirty()
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
      // a value the catalog does not list must still be shown, not silently swapped
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
      patch[f.key] = f.type === 'number' ? Number(input.value) : input.value
      dirty()
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
      if (Object.keys(patch).length) params.patch = patch
      if (Object.keys(keys).length) params.keys = keys
      const res = await client.request('config.set', params)
      for (const k of Object.keys(patch)) delete patch[k]
      for (const k of Object.keys(keys)) delete keys[k]
      msg.className = 'set-msg ok'
      msg.textContent = res && res.restartRequired
        ? 'Saved — restart the daemon for some of these to take effect.'
        : 'Saved.'
      await load()
    } catch (e) {
      msg.className = 'set-msg bad'
      msg.textContent = `could not save: ${(e && e.message) || e}`
      save.disabled = false
    }
  }

  return { init, load }
})()

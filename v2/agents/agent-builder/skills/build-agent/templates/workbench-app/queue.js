/* The workbench: drop a pile of things, watch each one go through.

   ─────────────────────────────────────────────────────────────────────────────────────────
   THE ONE THING TO CHANGE

   `TOOL` — the tool that processes ONE item — and `argsFor()`, which turns an uploaded file
   into that tool's arguments. Everything else is queue mechanics that already work.

   ─────────────────────────────────────────────────────────────────────────────────────────
   WHY EACH ITEM CARRIES ITS OWN STATE

   queued → uploading → working → done | failed, PER ITEM. A single global "processing…" spinner
   over forty files tells the user nothing they can act on: they cannot see that file 12 failed
   while 13 through 40 succeeded, so the whole batch becomes suspect and gets run again.

   AND WHY ONE FAILURE DOES NOT STOP THE REST. A bad file is a fact about that file. Aborting
   the batch on it means the user fixes one thing, reruns everything, and finds the next bad one
   — an afternoon of that instead of one pass and a short list.

   ─────────────────────────────────────────────────────────────────────────────────────────
   CONCURRENCY IS 2 ON PURPOSE

   These are real tool calls; a lot of them at once means a lot of subprocesses, model calls or
   API requests at once. Two keeps the queue moving without the machine (or a rate limit)
   noticing. Raise it when you know the work is cheap. */

window.Queue = (function () {
  const $ = (id) => document.getElementById(id)
  let client = null
  let items = []
  let running = false

  // CHANGE ME — the tool that handles ONE item.
  const TOOL = ''
  const CONCURRENCY = 2
  const MAX_BYTES = 20 * 1024 * 1024

  /** CHANGE ME — an uploaded file -> the tool's arguments.
   *  `item.path` is where the daemon saved it inside this agent's workspace. */
  function argsFor(item) {
    return { path: item.path }
  }

  /** CHANGE ME — the tool's result -> the one line shown under the item's name. */
  function summarize(result) {
    const text = agentd.resultText(result) || ''
    return text.split('\n')[0].slice(0, 200)
  }

  function init(c) {
    client = c
    const zone = $('drop')
    const picker = $('queuePicker')

    // Three routes, because people reach for all three.
    zone.addEventListener('click', () => picker.click())
    picker.addEventListener('change', () => { void add(picker.files); picker.value = '' })
    for (const type of ['dragenter', 'dragover']) {
      zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add('over') })
    }
    for (const type of ['dragleave', 'drop']) {
      zone.addEventListener(type, () => zone.classList.remove('over'))
    }
    zone.addEventListener('drop', (e) => { e.preventDefault(); void add(e.dataTransfer.files) })
    $('queueRun').addEventListener('click', () => void run())
    $('queueClear').addEventListener('click', () => {
      // Only the finished ones: clearing work in flight would leave rows disappearing under
      // the user while their tool calls carry on regardless.
      items = items.filter((i) => i.state === 'queued' || i.state === 'uploading' || i.state === 'working')
      draw()
    })
  }

  function readFile(f) {
    return new Promise((resolve, reject) => {
      const r = new FileReader()
      r.onload = () => resolve(String(r.result).split(',')[1] || '')
      r.onerror = () => reject(r.error)
      r.readAsDataURL(f)
    })
  }

  async function add(list) {
    for (const f of Array.from(list || [])) {
      // Refused HERE, with the file's name on screen, rather than as an opaque failure four
      // steps later when the daemon rejects the upload.
      const tooBig = f.size > MAX_BYTES
      items.push({
        name: f.name || 'file',
        size: f.size,
        file: f,
        path: '',
        state: tooBig ? 'failed' : 'queued',
        note: tooBig ? `too large (${Math.round(f.size / 1048576)} MB)` : '',
      })
    }
    draw()
  }

  async function run() {
    if (running) return
    if (!TOOL) {
      note('No tool wired yet — set TOOL in queue.js to one of this agent’s tools.', true)
      return
    }
    running = true
    setBusy(true)
    note('')
    try {
      // A small worker pool rather than Promise.all over everything: see the header.
      const pending = items.filter((i) => i.state === 'queued')
      const workers = Array.from({ length: Math.min(CONCURRENCY, pending.length) }, () => worker(pending))
      await Promise.all(workers)
    } finally {
      running = false
      setBusy(false)
      const failed = items.filter((i) => i.state === 'failed').length
      note(failed ? `${failed} of ${items.length} failed — see the rows above.` : '', !!failed)
    }
  }

  async function worker(pending) {
    for (;;) {
      const item = pending.shift()
      if (!item) return
      try {
        item.state = 'uploading'
        draw()
        const res = await client.request('workspace.upload', {
          name: item.name,
          dataBase64: await readFile(item.file),
        })
        if (!res || res.ok === false) throw new Error((res && res.error) || 'upload failed')
        item.path = res.path || res.name || item.name

        item.state = 'working'
        draw()
        const out = await client.invokeTool(TOOL, argsFor(item))
        item.state = 'done'
        item.note = summarize(out)
      } catch (e) {
        // ONE BAD ITEM IS A FACT ABOUT THAT ITEM. The loop continues; the reason is shown on
        // its own row, where the user can act on it.
        item.state = 'failed'
        item.note = (e && e.message) || String(e)
      }
      draw()
    }
  }

  // ── drawing ───────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    const n = document.createElement(tag)
    if (cls) n.className = cls
    if (text != null) n.textContent = text
    return n
  }

  function setBusy(on) {
    $('queueRun').disabled = on
    $('queueRun').textContent = on ? 'working…' : 'Run'
  }

  function note(text, bad) {
    const n = $('queueMsg')
    n.className = `queue-msg${bad ? ' bad' : ''}`
    n.textContent = text || ''
  }

  function draw() {
    const list = $('queueList')
    list.textContent = ''
    const counts = { queued: 0, working: 0, done: 0, failed: 0 }
    for (const i of items) counts[i.state === 'uploading' ? 'working' : i.state]++

    $('queueCount').textContent = items.length
      ? `${counts.done} done · ${counts.failed} failed · ${counts.queued + counts.working} to go`
      : ''

    if (!items.length) {
      list.append(el('li', 'empty', 'Nothing queued yet — drop files above.'))
      return
    }
    for (const i of items) {
      const li = el('li', `q-item ${i.state}`)
      const head = el('div', 'q-head')
      head.append(el('span', 'q-name', i.name))
      head.append(el('span', `q-state ${i.state}`, i.state))
      li.append(head)
      if (i.note) li.append(el('span', 'q-note', i.note))
      list.append(li)
    }
  }

  return { init, draw }
})()

/* Tokyo Weather dashboard — a pure CLIENT of the daemon. It INVOKES the agent's own
   get_weather tool over its scoped connection (tools.invoke); it has no backend of
   its own and never could — that's the platform invariant. */

/* Connect to the daemon serving this page. Desktop openers put ?token&scope= in the URL;
   on a PUBLIC (hosted) URL there is neither, so derive the scope from our own path
   (/apps/<id>/ -> agent:<id>) — the daemon admits us on the [app] public opt-in. On a
   vanity host (ui served at "/") both are absent and the SERVER derives scope from Host. */
const here = new URL(location.href)
const pathId = (location.pathname.match(/^\/apps\/([^/]+)/) || [])[1]
const token = here.searchParams.get('token') || ''
const scope =
  here.searchParams.get('scope') || (pathId ? `agent:${decodeURIComponent(pathId)}` : '')
const client = new agentd.AgentdClient()
client.connect({ url: here.origin, token: token || undefined, scope: scope || undefined })
const $ = (id) => document.getElementById(id)

const hhmm = (iso) => (iso ? String(iso).slice(11, 16) : '–')
const deg = (v) => (v == null ? '–' : `${Math.round(v)}`)

function render(w) {
  $('temp').textContent = deg(w.current.temperature_c)
  $('sky').textContent = w.current.sky || '–'
  $('feels').textContent =
    w.current.feels_like_c == null ? '' : `feels like ${Math.round(w.current.feels_like_c)}°C`
  $('hilo').textContent = `${deg(w.today.high_c)}° / ${deg(w.today.low_c)}°`
  $('rain').textContent =
    w.today.rain_probability_pct == null ? '–' : `${w.today.rain_probability_pct}%`
  $('wind').textContent = w.current.wind_ms == null ? '–' : `${w.current.wind_ms} m/s`
  $('hum').textContent = w.current.humidity_pct == null ? '–' : `${w.current.humidity_pct}%`
  $('sunrise').textContent = hhmm(w.today.sunrise)
  $('sunset').textContent = hhmm(w.today.sunset)
  $('updated').textContent = w.observed_at
    ? `observed ${String(w.observed_at).replace('T', ' ')}`
    : ''
}

async function refresh() {
  $('status').textContent = 'updating…'
  try {
    const res = await client.invokeTool('get_weather', { format: 'json' })
    render(JSON.parse(res.text))
    $('status').textContent = 'live'
  } catch (e) {
    $('status').textContent = `error: ${(e && e.message) || e}`
  }
}

// say WHY we're closed instead of looping on "closed": a tokened (desktop) link dies
// when the daemon restarted (token rotated); the public page just needs a refresh.
let started = false
client.onStatus((s) => {
  if (s === 'open') {
    if (!started) {
      started = true
      void refresh()
    } else {
      $('status').textContent = 'live'
    }
  } else if (s === 'closed') {
    $('status').textContent = token
      ? 'disconnected — token may be stale; reopen with: agentd app open weather'
      : 'disconnected — refresh the page'
  } else {
    $('status').textContent = s
  }
})

$('refresh').addEventListener('click', () => void refresh())
setInterval(() => void refresh(), 10 * 60 * 1000) // keep the dashboard fresh

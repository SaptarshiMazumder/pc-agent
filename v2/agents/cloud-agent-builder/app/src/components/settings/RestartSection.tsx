/* Restart the daemon, from the window where the reason to restart occurs.
 *
 * THE FAILURE THIS ENDS. `reload_agent` re-reads the roster and hot-loads NEW plugins, which
 * covers most authoring. It cannot re-import a module already in memory — so editing a private
 * tool's Python and reloading gives you the OLD code back, with nothing anywhere saying the
 * change was ignored. Until this button, the only fix was quitting the whole product, which is
 * something an author does many times an hour.
 *
 * It goes through the daemon (`daemon.restart`), not the desktop shell: an agent app window is
 * deliberately given no host bridge. The daemon stops itself and the supervisor starts a fresh
 * one — and refuses when nothing is supervising, because then stopping is not restarting.
 */

export function RestartSection({
  onRestart,
  busy,
  note,
}: {
  onRestart: () => void
  busy: boolean
  note: string
}) {
  return (
    <section className="set-group">
      <h2>Daemon</h2>
      <p className="ghelp">
        Restart agentd. Needed after editing a plugin&apos;s Python or any file read at startup —
        those are loaded once, so a change on disk is ignored until it restarts. Conversations
        survive; a run in progress does not.
      </p>
      <div className="field">
        <div>
          <label>Restart agentd</label>
          <span className="fhelp">
            {note || 'This window loses its connection for a moment and reconnects itself.'}
          </span>
        </div>
        <button className="ghost-btn" disabled={busy} onClick={onRestart}>
          {busy ? 'Restarting…' : 'Restart'}
        </button>
      </div>
    </section>
  )
}

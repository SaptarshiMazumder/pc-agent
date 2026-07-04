import { useApp } from '../state/store'

export default function StoreView() {
  const catalog = useApp((state) => state.catalog)
  const catalogError = useApp((state) => state.catalogError)
  const installed = useApp((state) => state.installed)
  const installBusy = useApp((state) => state.installBusy)
  const installBundle = useApp((state) => state.installBundle)
  const uninstallBundle = useApp((state) => state.uninstallBundle)
  const refreshCatalog = useApp((state) => state.refreshCatalog)

  return (
    <div className="store">
      <header className="store-head">
        <div>
          <h1>Store</h1>
          <div className="chat-sub">Install agents into this app — live, no restart.</div>
        </div>
        <button className="button" onClick={() => void refreshCatalog()}>↻ Refresh</button>
      </header>

      {catalogError && <div className="banner banner-error">{catalogError}</div>}

      <div className="cards">
        {catalog.map((bundle) => {
          const busy = installBusy[bundle.id]
          return (
            <div className="card" key={bundle.id}>
              <div className="card-top">
                <div className="card-name">{bundle.name}</div>
                <div className="badges">
                  <span className="badge">{bundle.version}</span>
                  <span className={`badge ${bundle.price === 'free' ? 'badge-free' : 'badge-paid'}`}>
                    {bundle.price}
                  </span>
                  {bundle.installed && <span className="badge badge-ok">installed {bundle.installedVersion}</span>}
                  {bundle.updateAvailable && <span className="badge badge-update">update</span>}
                  {!bundle.compatible && <span className="badge badge-warn">needs newer agentd</span>}
                </div>
              </div>
              <p className="card-desc">{bundle.description || 'No description.'}</p>
              <div className="card-actions">
                {busy ? (
                  <span className="busy">{busy}</span>
                ) : bundle.installed && !bundle.updateAvailable ? (
                  <button className="button danger" onClick={() => void uninstallBundle(bundle.id)}>
                    Uninstall
                  </button>
                ) : (
                  <button
                    className="button primary"
                    disabled={!bundle.compatible}
                    onClick={() => void installBundle(bundle.id)}
                  >
                    {bundle.updateAvailable ? `Update to ${bundle.version}` : 'Install'}
                  </button>
                )}
              </div>
            </div>
          )
        })}
        {catalog.length === 0 && !catalogError && (
          <div className="empty-sub pad">No bundles in the registry (or no registry configured yet).</div>
        )}
      </div>

      {installed.length > 0 && (
        <>
          <div className="section-label pad-top">Installed</div>
          <div className="installed-list">
            {installed.map((bundle) => (
              <div className="installed-row" key={bundle.id}>
                <span className="row-title">{bundle.id}</span>
                <span className="row-sub">
                  v{bundle.version} · {bundle.installedAt}
                  {bundle.pluginIds.length > 0 ? ` · plugins: ${bundle.pluginIds.join(', ')}` : ''}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

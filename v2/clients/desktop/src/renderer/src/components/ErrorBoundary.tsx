import { Component, type ErrorInfo, type ReactNode } from 'react'

/** App-wide error boundary: a render throw used to unmount the whole tree → a blank white
 *  window with no clue why. This catches it and shows the error + stack on screen (and logs
 *  to the console) so a crash is diagnosable instead of silent.
 *
 *  NOTE: styles here are INLINE BY DESIGN (the one sanctioned exception to the class-only
 *  rule) — this screen must render even when styles.css itself failed to load/parse. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // surfaced in the renderer devtools console too
    console.error('Renderer crashed:', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div style={{ padding: 24, fontFamily: 'system-ui, sans-serif', color: '#e5e5e5', background: '#1a1a1a', height: '100vh', overflow: 'auto' }}>
        <h2 style={{ color: '#ff6b6b', margin: '0 0 8px' }}>Something crashed the app</h2>
        <p style={{ opacity: 0.8, margin: '0 0 16px' }}>
          The error below stopped the UI from rendering. If it mentions stale data, restarting the daemon
          often fixes it.
        </p>
        <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: '#000', padding: 14, borderRadius: 8, fontSize: 12.5, lineHeight: 1.5 }}>
          {String(error.stack || error.message || error)}
        </pre>
        <button
          onClick={() => this.setState({ error: null })}
          style={{ marginTop: 16, padding: '8px 16px', borderRadius: 8, border: '1px solid #444', background: '#2a2a2a', color: '#e5e5e5', cursor: 'pointer' }}
        >
          Try again
        </button>
      </div>
    )
  }
}

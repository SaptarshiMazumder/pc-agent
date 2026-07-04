export interface FlavorInfo {
  productId: string
  productName: string
  defaultAgent: string
  storeEnabled: boolean
  preinstalledBundles: string[]
  bundledPackages: string[]
  version: string
}

export interface SupervisorStatus {
  phase: 'looking' | 'starting' | 'running' | 'failed'
  message: string
  info: { host: string; port: number; pid: number; version: string } | null
}

export interface DesktopApi {
  flavor(): Promise<FlavorInfo>
  supervisorStatus(): Promise<SupervisorStatus>
  ensureDaemon(): Promise<{ url: string; version: string; pid: number }>
  onSupervisorStatus(callback: (status: SupervisorStatus) => void): () => void
}

declare global {
  interface Window {
    agentd: DesktopApi
  }
}

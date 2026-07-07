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

export interface PickedFile {
  name: string
  size: number
  dataBase64: string
}

export interface DesktopApi {
  flavor(): Promise<FlavorInfo>
  supervisorStatus(): Promise<SupervisorStatus>
  ensureDaemon(): Promise<{ url: string; version: string; pid: number }>
  restartDaemon(): Promise<{ url: string; version: string; pid: number }>
  onSupervisorStatus(callback: (status: SupervisorStatus) => void): () => void
  openPath(path: string): Promise<string>
  revealPath(path: string): Promise<void>
  downloadPath(path: string): Promise<{ ok: boolean; path?: string; canceled?: boolean; error?: string }>
  readText(path: string): Promise<{ ok: boolean; text?: string; error?: string }>
  readBytes(path: string): Promise<{ ok: boolean; base64?: string; error?: string }>
  savePath(path: string, content: string, base64?: boolean): Promise<{ ok: boolean; error?: string }>
  saveAs(defaultName: string, content: string, base64?: boolean): Promise<{ ok: boolean; path?: string; canceled?: boolean; error?: string }>
  pickFiles(): Promise<PickedFile[]>
}

declare global {
  interface Window {
    agentd: DesktopApi
  }
}

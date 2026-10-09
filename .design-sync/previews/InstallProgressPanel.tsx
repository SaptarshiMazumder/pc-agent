/* InstallProgressPanel — every model download and node-pack install, one row each, shown above
 * the composer while anything installs (and for a minute after the last row ends). A model row
 * has its own bar, percent, bytes, speed and time left; a node pack clones with git and has no
 * percentage, so its bar is indeterminate. Rows end as done (full bar) or failed (error line).
 *
 * Fixture: the StudioState.installs shape the window's studio poll returns. `updated_at` is
 * stamped relative to now so the panel's linger/stale windows keep every row on screen.
 * Cells sweep: models mid-download, a node pack + a starting model, and finished rows
 * (done + failed). The indeterminate bar animates — the sheet freezes one frame of it. */
import { InstallProgressPanel } from 'agent-app'

const now = Math.floor(Date.now() / 1000)
const GiB = 1024 * 1024 * 1024
const MiB = 1024 * 1024

export const Downloading = () => (
  <div style={{ maxWidth: 680 }}>
    <InstallProgressPanel
      state={{
        installs: {
          models: [
            {
              name: 'wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors',
              state: 'downloading',
              received: Math.round(6.1 * GiB),
              total: Math.round(14.3 * GiB),
              bytes_per_second: Math.round(48.6 * MiB),
              updated_at: now - 2,
            },
            {
              name: 'umt5_xxl_fp8_e4m3fn_scaled.safetensors',
              state: 'downloading',
              received: Math.round(1.2 * GiB),
              total: Math.round(6.7 * GiB),
              bytes_per_second: Math.round(31.2 * MiB),
              updated_at: now - 2,
            },
            {
              name: 'wan_2.1_vae.safetensors',
              state: 'verifying',
              received: 254 * MiB,
              total: 254 * MiB,
              bytes_per_second: 0,
              updated_at: now - 4,
            },
          ],
        },
      }}
    />
  </div>
)

export const NodePackAndStarting = () => (
  <div style={{ maxWidth: 680 }}>
    <InstallProgressPanel
      state={{
        installs: {
          node_packs: [{ name: 'ComfyUI-VideoHelperSuite', state: 'installing', updated_at: now - 3 }],
          models: [
            {
              name: 'flux1-dev-fp8.safetensors',
              state: 'retrying',
              received: Math.round(3.4 * GiB),
              total: Math.round(11.9 * GiB),
              bytes_per_second: Math.round(12.4 * MiB),
              updated_at: now - 5,
            },
            { name: 'ae.safetensors', state: 'starting', updated_at: now - 1 },
          ],
        },
      }}
    />
  </div>
)

export const DoneAndFailed = () => (
  <div style={{ maxWidth: 680 }}>
    <InstallProgressPanel
      state={{
        installs: {
          node_packs: [{ name: 'ComfyUI-KJNodes', state: 'done', updated_at: now - 20 }],
          models: [
            {
              name: 'sd_xl_base_1.0.safetensors',
              state: 'done',
              received: Math.round(6.5 * GiB),
              total: Math.round(6.5 * GiB),
              updated_at: now - 12,
            },
            {
              name: 'realism_skin_v2.safetensors',
              state: 'failed',
              received: 38 * MiB,
              total: 218 * MiB,
              error: 'HTTP 401 from civitai.com — this model needs a Civitai API key (Settings → Civitai token).',
              updated_at: now - 8,
            },
          ],
        },
      }}
    />
  </div>
)

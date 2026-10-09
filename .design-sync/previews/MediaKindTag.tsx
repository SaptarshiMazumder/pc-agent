/* MediaKindTag — the word-and-glyph label that says what an output IS. The three kinds on the
 * page ground, then the `over` variant sitting on a render, where the tinted grounds would vanish. */
import { MediaKindTag } from 'agent-app'

export const Kinds = () => (
  <div style={{ display: 'flex', gap: 8, padding: 16 }}>
    <MediaKindTag kind="image" />
    <MediaKindTag kind="video" />
    <MediaKindTag kind="workflow" />
  </div>
)

export const OverMedia = () => (
  <div
    style={{
      position: 'relative',
      width: 280,
      height: 170,
      margin: 16,
      borderRadius: 12,
      background: 'linear-gradient(135deg, #3a2a4f 0%, #7a4b3a 55%, #d9a24a 100%)',
    }}
  >
    <div style={{ position: 'absolute', top: 10, left: 10, display: 'flex', gap: 6 }}>
      <MediaKindTag kind="video" over />
      <MediaKindTag kind="workflow" over />
    </div>
  </div>
)

/* "Free where it's as good. Premium where it wins." — the price-and-quality argument.
 *
 * THE ADVANTAGE IN ONE PICTURE: one EXAMPLE workflow as its steps, each marked Free or Premium,
 * then the two shelves Penguin picks from. Labelled as an example because it illustrates how every
 * workflow is priced, not which model made the renders on this page.
 */

import { Check, Sparkles } from 'lucide-react'

import { MediaKindTag } from '../media/MediaKindTag'
import type { MIX_EXAMPLE, MODEL_TIERS } from './landing-content'

function Tier({ tier, long = false }: { tier: 'open' | 'premium'; long?: boolean }) {
  return (
    <span className={`lp-tier is-${tier}`}>
      {tier === 'premium' ? <Sparkles size={12} strokeWidth={2.2} /> : <Check size={12} strokeWidth={2.6} />}
      {tier === 'premium' ? 'Premium' : long ? 'Free · open source' : 'Free'}
    </span>
  )
}

export function LandingModelMix({
  example,
  tiers,
}: {
  example: typeof MIX_EXAMPLE
  tiers: typeof MODEL_TIERS
}): JSX.Element {
  return (
    <>
      <div className="lp-mix" aria-label="Example: one workflow, step by step">
        <span className="lp-mix-label">Example · an image-to-video workflow</span>
        <ol className="lp-mix-steps">
          {example.map((s, i) => (
            <li key={s.step} className={`lp-mix-step is-${s.tier}`}>
              <span className="lp-mix-n">{String(i + 1).padStart(2, '0')}</span>
              <b>{s.step}</b>
              <Tier tier={s.tier} long />
            </li>
          ))}
        </ol>
      </div>

      <div className="lp-tiers">
        {tiers.map((t) => (
          <section key={t.tier} className={`lp-tier-card is-${t.tier}`}>
            <div className="lp-tier-head">
              <Tier tier={t.tier} />
              <h3>{t.title}</h3>
              <p>{t.sub}</p>
            </div>
            {t.groups.map((g) => (
              <div key={g.kind} className="lp-tier-group">
                <MediaKindTag kind={g.kind} />
                <div className="lp-models-list">
                  {g.names.map((n) => (
                    <span key={n}>{n}</span>
                  ))}
                </div>
              </div>
            ))}
          </section>
        ))}
      </div>
    </>
  )
}

export default LandingModelMix

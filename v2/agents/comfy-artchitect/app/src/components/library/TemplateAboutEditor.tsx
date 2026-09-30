/* The about, editable — the Save-as-template dialog's "About this template" section.
 *
 * The draft is model-written from the chat (template_about_draft); every section is a plain field
 * the person can rewrite. Lists are one item per line. What it makes and how it works are the two
 * a template cannot be saved without (the dialog checks `aboutComplete`).
 */

import type { TemplateAbout } from '../../agentd/template-about'

const split = (v: string): string[] =>
  v
    .split('\n')
    .map((x) => x.trim())
    .filter(Boolean)

export function aboutComplete(a: TemplateAbout | null): boolean {
  return !!a && !!a.makes.trim() && !!a.how_it_works.trim()
}

export function TemplateAboutEditor({
  about,
  onChange,
}: {
  about: TemplateAbout
  onChange: (next: TemplateAbout) => void
}) {
  const set = (patch: Partial<TemplateAbout>): void => onChange({ ...about, ...patch })
  const setNeed = (k: keyof TemplateAbout['needs'], v: string): void => set({ needs: { ...about.needs, [k]: v } })
  return (
    <div className="tpl-about">
      <label className="tpl-field">
        <span>What it makes</span>
        <textarea rows={2} value={about.makes} onChange={(e) => set({ makes: e.target.value })} />
      </label>
      <label className="tpl-field">
        <span>How it works</span>
        <textarea rows={4} value={about.how_it_works} onChange={(e) => set({ how_it_works: e.target.value })} />
      </label>
      {about.inputs.map((i, n) => (
        <div key={i.role} className="tpl-about-input">
          <span className="tpl-gap-name">@{i.role}</span>
          <input
            value={i.what}
            placeholder="What this input fixes in the result"
            onChange={(e) => set({ inputs: about.inputs.map((x, k) => (k === n ? { ...x, what: e.target.value } : x)) })}
          />
          <input
            value={i.tips}
            placeholder="How to pick a good one"
            onChange={(e) => set({ inputs: about.inputs.map((x, k) => (k === n ? { ...x, tips: e.target.value } : x)) })}
          />
        </div>
      ))}
      <label className="tpl-field">
        <span>What you can change (one per line)</span>
        <textarea
          rows={3}
          value={about.you_can_change.join('\n')}
          onChange={(e) => set({ you_can_change: split(e.target.value) })}
        />
      </label>
      <label className="tpl-field">
        <span>Good to know — limits (one per line)</span>
        <textarea rows={3} value={about.limits.join('\n')} onChange={(e) => set({ limits: split(e.target.value) })} />
      </label>
      <div className="tpl-about-needs">
        {(
          [
            ['runs_on', 'Runs on'],
            ['credits', 'Credits'],
            ['vram', 'GPU memory'],
            ['time', 'Time'],
          ] as const
        ).map(([k, label]) => (
          <label key={k} className="tpl-field">
            <span>{label}</span>
            <input value={about.needs[k] || ''} onChange={(e) => setNeed(k, e.target.value)} />
          </label>
        ))}
      </div>
    </div>
  )
}

export default TemplateAboutEditor

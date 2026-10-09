/* Brand kit — what every post is designed from, kept apart from the posts themselves:
 *   References   pictures of designs worth following — the structure new designs take
 *   Templates    the design templates the agent starts from
 *   Brand        the name, sign-off line, voice, fonts and colours every post carries */

import type { AgentdClient } from '@agentd/client'
import { useState } from 'react'

import { BrandForm } from './BrandForm'
import { ReferenceLibrary } from './ReferenceLibrary'
import { TemplateGallery } from './TemplateGallery'

type Section = 'references' | 'templates' | 'brand'
const SECTIONS: { id: Section; label: string }[] = [
  { id: 'references', label: 'References' },
  { id: 'templates', label: 'Templates' },
  { id: 'brand', label: 'Brand' },
]
const SECTION_KEY = 'ad-studio.brand-kit-section'

function remembered(): Section {
  try {
    const s = localStorage.getItem(SECTION_KEY) as Section | null
    return s && SECTIONS.some((x) => x.id === s) ? s : 'references'
  } catch {
    return 'references'
  }
}

export function BrandKitPage({ client }: { client: AgentdClient | null }) {
  const [section, setSectionState] = useState<Section>(remembered)
  const setSection = (s: Section) => {
    setSectionState(s)
    try {
      localStorage.setItem(SECTION_KEY, s)
    } catch {
      /* a private window: the section is simply not remembered */
    }
  }

  return (
    <div className="page">
      <header className="page-top">
        <span className="eyebrow-red">Studio</span>
        <h1>Brand kit</h1>
        <p className="page-note">What every post is designed from — the designs to follow, the templates, and your brand.</p>
      </header>
      <div className="seg-switch page-seg" role="tablist" aria-label="Brand kit">
        {SECTIONS.map((s) => (
          <button key={s.id} role="tab" aria-selected={section === s.id} className={section === s.id ? 'on' : ''} onClick={() => setSection(s.id)}>
            {s.label}
          </button>
        ))}
      </div>
      {section === 'references' && <ReferenceLibrary client={client} />}
      {section === 'templates' && <TemplateGallery client={client} />}
      {section === 'brand' && <BrandForm client={client} />}
    </div>
  )
}

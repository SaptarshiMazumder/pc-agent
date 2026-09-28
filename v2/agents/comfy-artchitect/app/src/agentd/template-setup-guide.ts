/* A template's SETUP GUIDE — `setup.json` in the template folder: every node pack and model file
 * its workflows need, with where each comes from. The contract is the plugin's
 * (plugins/comfy-bridge/template_setup_guide.py); this is the window's side of it.
 *
 * WHY THE SAVE DIALOG ASKS. The chat's installer lists source most things, but not always all:
 * a pack Manager does not list, a model the machine already had. A template saved with holes is
 * one the agent has to guess its way through later — which is how a made-up Hugging Face link
 * and forty minutes of failed runs happened. So the dialog shows each hole as a field for its
 * link, and the template is kept only when none is left empty.
 */

import type { AgentdClient } from '@agentd/client'

export const TEMPLATE_SETUP = 'setup.json'
export const SETUP_FORMAT = 'comfy-penguin-setup'

export interface SetupNodePack {
  name: string
  repository: string
  /** The node types it provides — what a missing node is matched against. */
  classes: string[]
}

export interface SetupModel {
  /** May carry a subfolder: `Krea2/lora.safetensors`. */
  filename: string
  /** The models/ folder: diffusion_models, loras, vae, text_encoders, … */
  kind: string
  url: string
}

export interface SetupGuide {
  format: typeof SETUP_FORMAT
  version: number
  node_packs: SetupNodePack[]
  models: SetupModel[]
  notes?: string
}

/** Something a workflow needs that no installer list could source. */
export interface SetupGap {
  type: 'model' | 'node_pack'
  /** The model's file name, or the node type. */
  name: string
  /** For a model: its models/ folder, when the loader says; '' = the person picks. */
  kind: string
  used_by: string
}

/** The folders a model can go in — what the person picks when a gap does not say. */
export const MODEL_KINDS = [
  'diffusion_models',
  'checkpoints',
  'loras',
  'vae',
  'text_encoders',
  'controlnet',
  'upscale_models',
] as const

export function gapKey(g: SetupGap): string {
  return `${g.type}:${g.name}`
}

/** The chat's guide and its gaps, from its workflows' installer lists (template_setup_guide). */
export async function readSetupGuide(
  client: AgentdClient,
  manifests: string[],
): Promise<{ guide: SetupGuide; gaps: SetupGap[] }> {
  const res = (await client.request('tools.invoke', {
    name: 'template_setup_guide',
    params: { manifests },
  })) as { details?: { guide?: SetupGuide; gaps?: SetupGap[] }; isError?: boolean; text?: string }
  if (!res?.details?.guide) throw new Error(res?.text || 'could not work out what this template needs')
  return { guide: res.details.guide, gaps: res.details.gaps || [] }
}

/** Is this link usable for the gap? A model is a direct https file link; a pack, a GitHub repository. */
export function linkProblem(g: SetupGap, url: string): string {
  const u = url.trim()
  if (!u) return 'needed'
  if (g.type === 'node_pack') {
    return /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/?$/.test(u) ? '' : 'a GitHub repository link: https://github.com/<owner>/<repo>'
  }
  return /^https:\/\/\S+$/.test(u) ? '' : 'a direct https link to the file'
}

/** The guide with every gap's link written in — what is saved as setup.json. */
export function withLinks(
  guide: SetupGuide,
  gaps: SetupGap[],
  links: Record<string, string>,
  kinds: Record<string, string>,
): SetupGuide {
  const packs = [...guide.node_packs]
  const models = [...guide.models]
  for (const g of gaps) {
    const url = (links[gapKey(g)] || '').trim().replace(/\/$/, '')
    if (g.type === 'node_pack') {
      const same = packs.find((p) => p.repository.replace(/\.git$/, '') === url.replace(/\.git$/, ''))
      if (same) same.classes = [...new Set([...same.classes, g.name])]
      else packs.push({ name: url.split('/').pop() || url, repository: url, classes: [g.name] })
    } else {
      models.push({ filename: g.name, kind: g.kind || kinds[gapKey(g)] || '', url })
    }
  }
  return { ...guide, node_packs: packs, models }
}

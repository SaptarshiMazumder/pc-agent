/* The shop's product catalogue, through the agent's product tools: set up once — photos, title,
 * kind, price — and picked for any story. Free and instant, so called directly. */

import type { AgentdClient } from '@agentd/client'

import type { Media } from './campaigns'
import { invoke, mediaFor } from './posts'

export interface ShopProduct {
  key: string
  title: string
  kind: 'wear' | 'jewellery' | 'other'
  description: string
  price: string
  photos: string[]
  angles: string[]
  in_use: string[]
  added: number
}

export async function listProducts(client: AgentdClient): Promise<{ products: ShopProduct[]; media: Media }> {
  const d = await invoke(client, 'product_list', {})
  return { products: (d.products || []) as ShopProduct[], media: mediaFor(String(d.root || '')) }
}

/** A new product (no key), or changes to one. */
export async function saveProduct(client: AgentdClient, product: Partial<ShopProduct>): Promise<ShopProduct> {
  return (await invoke(client, 'product_save', product)).product as ShopProduct
}

export async function removeProduct(client: AgentdClient, key: string): Promise<void> {
  await invoke(client, 'product_remove', { key })
}

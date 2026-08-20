/**
 * The shapes money arrives in. Field-for-field what the accounts service returns, renamed to
 * camelCase once, here — so no consumer parses `credits_remaining` a second time and no consumer
 * gets to disagree about what a pack is.
 */

export type Credits = {
  creditsRemaining: number
  fundingSource: string
  creditClass: string
  modelTierMax: string
  entitlementRequired: boolean
  entitled: boolean
  expiresAt: number
}

export type CreditPack = {
  id: string
  kind: string
  title: string
  priceUsd: number
  credits: number
  modelTierMax: string
  periodDays: number
}

export type Catalog = {
  packs: CreditPack[]
  /** Which payment rail is configured. For display only — never branch behaviour on it. */
  provider: string
  /** The rail's own sentence about what confirming will do ("no card is charged", or later the
   *  real thing). Rendered verbatim so swapping the rail rewrites the disclosure itself. */
  paymentNote: string
}

export type Purchase = {
  ok: boolean
  replayed: boolean
  credits: number
  priceUsd: number
  creditsRemaining: number
  /** The rail's own account of what it did — shown as-is on the receipt line. */
  paymentDetail: string
  /**
   * Set ONLY when the rail could not finish in one request and the customer must go and pay.
   * Empty means the purchase is already done and the credits are already granted.
   *
   * A caller that follows this when present and shows the balance otherwise is correct on every
   * rail, without ever asking which one is configured — which is the rule the whole payments
   * module is built on.
   */
  checkoutUrl: string
}

/** What the host has to answer before any of this can run. */
export type BillingHost = {
  /** Base URL of the accounts service, no trailing slash. */
  accountsUrl(): Promise<string> | string
  /** A CURRENT access token. Implementations refresh as needed; this must not return a stale one. */
  accessToken(): Promise<string> | string
  /** Idempotency keys. Injected because `crypto.randomUUID` is unavailable on some hosts. */
  newKey(): string
}

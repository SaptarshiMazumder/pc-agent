/* agentd's `lib/orgs` surface, for the byte-identical OrgView copy.
 *
 * THE COMPONENT IS AGENTD'S FILE, UNCHANGED — the user's requirement is carbon-copy behavior,
 * and the honest way to keep two copies identical is to make the diff empty. So this module
 * exists to answer the exact imports that file makes, with the SDK doing the work: same
 * function names, same no-options signatures, same shapes.
 */

export type { JoinableOrg, MyOrgs, OrgDetail, OrgInvite, OrgMember, OrgMembership, OrgUsageRow } from '@agentd/client'

export {
  createOrg,
  fetchMyOrgs,
  fetchOrgDetail,
  fetchOrgUsage,
  joinOrg,
  mintInvite,
  updateDomain,
  updateMember,
} from '@agentd/client'

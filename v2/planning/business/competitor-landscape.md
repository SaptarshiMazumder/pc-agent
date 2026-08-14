# Competitor Landscape — agentd platform

> **Status:** research snapshot, 2026-08-11. Competitive facts rot fast — Cowork's plugin
> marketplace and Replit's agent features are both moving monthly; re-verify before using in a
> pitch. · **Sources at the bottom.**

## What we are, in one line

Users create, modify and share agents by chat — and each agent is a **standalone product**: its
own app, its own users, its own economics, running on our infrastructure or locally as an exe,
not owned by any model vendor.

## The claims we make, and who else can make them

| | Build by chat, no engineering | Runs immediately, no FDE | Local / on-PC, per-machine permissions | Marketplace where strangers install agents | Per-agent metering + creator revenue |
|---|---|---|---|---|---|
| **agentd (us)** | yes | yes | **yes — exe, signed, sandboxed** | yes (empty today) | built — ledger, split, reserve; card rail in progress |
| **Replit** | yes — best-in-class | yes | no — their cloud only | templates/remixes, not agents | no |
| **Palantir AIP** | partially (AI FDE) | **no — the FDE is the product** | on-prem possible | no | n/a |
| **Claude / Cowork** | skills: yes (no-code) · products: no | yes | **yes — desktop, user's files** | curated directory + org sharing; no public creator publishing | **none — no price, no payout, no metering** |
| **OpenAI** | Workspace Agents (enterprise) | yes | no | GPT Store fizzled; Agent Builder wound down June 2026 | effectively dead |
| **Microsoft Copilot Studio** | low-code (230k orgs) | yes | no — M365 tenant | **Agent Store — real, but M365-locked** | partner channel only |
| **Lindy / MindStudio / Zapier Agents** | yes | yes | no | weak or none | mostly none |

**The combination in our row does not exist anywhere else.** Every competitor has one or two
columns; nobody has all five. That is the moat claim — not any single column.

---

## Palantir — the incumbent model we replace

**What they do.** Forward-deployed engineers build bespoke agents on Foundry/AIP at the client's
site. AI FDE (an AI that operates Foundry conversationally) is narrowing the gap, but the human
FDE remains the delivery mechanism.

**Their weaknesses are public record, and they are our pitch:**

- "Putting 25-year-old engineers with the customer" — Kinaxis executive, on-site engineers
  lacking domain knowledge.
- Anaplan's CEO: FDE is "an effective sales tactic with quick proofs-of-concept, but a poor
  long-term strategy" — lock-in plus limited functionality.
- Six months of custom integration becomes load-bearing infrastructure nobody can migrate off.
- Every change request is a ticket, a queue, and a human's calendar.

**Our counter, verbatim-usable:** the owner modifies their own agent by chatting with it, the
same day. No dispatch, no engagement letter, no queue.

**Where they still beat us:** trust with governments and regulated industry, on-prem compliance
posture, and the fact that an FDE *closes deals* — high-touch sells where self-serve stalls.
Notably, Anthropic and OpenAI are now *copying* the FDE model for enterprise, not abandoning it.

---

## Replit — closest on "chat → working software"

**What they do (2026).** Agent 3/4 builds, tests and self-fixes apps autonomously (up to
200-minute runs), and now **generates other agents and automations** — Slack bots, scheduled
workers. Every project gets hosting, auth, DB, monitoring and a public URL with zero setup.
Scheduled deployments cover recurring background automations. "Describe it and it runs
immediately" is genuinely true there.

**Do they solve our problem? Partially — but it is a different problem.**

| Dimension | Replit | Us |
|---|---|---|
| What you end up with | an app on Replit's cloud, at a URL | an installable product (exe or hosted), with its own identity |
| Who uses the result | mostly the builder / their company | strangers who installed it from the marketplace |
| Local execution | none | first-class — per-PC permissions, user's own files |
| Sharing | share a URL, remix a template | publish → anyone downloads, installs, runs |
| Agent economics | none — builder pays Replit for usage | per-agent credit silos, creator revenue share |
| Pricing reputation | effort-based, uncapped — "a third of the monthly budget in one night" complaints | credits derived from provider cost, **hard cap before the provider is called** |

**Where they beat us:** authoring maturity (the build-test-fix loop is ahead of ours), brand,
distribution, and a template ecosystem vs our empty marketplace. For anything web-app-shaped
that the builder runs for themselves, Replit is the faster answer today.

**The line:** Replit rents you infrastructure to run software you built. We give you a product
you can hand to someone else.

---

## Claude / Cowork — the most important one

**What they do (2026).** Cowork (GA April 2026, included in every paid plan from $20/mo) is
Claude Desktop acting on the user's own files — the same local-execution ground we claim.
Skills are an open standard (agentskills.io); the plugin marketplace (Feb 2026) bundles skills +
connectors + sub-agents and is the fastest-growing part of the ecosystem, with a partner
directory shipping Atlassian, Canva, Figma, Notion, Stripe, Zapier.

**⚠ Two claims of ours that are FALSE against Claude — do not use them:**

1. *"You have to engineer the whole thing."* **No longer true.** `skill-creator` writes the
   skill through a conversational interview (~15–30 min, no markdown/YAML), and **Record a
   Skill** (July 2026) turns a narrated screen recording into a working skill. Their no-code
   authoring is genuinely good. A competitor kills this claim in a 30-second demo.
2. *"You can't share with your team."* **Wrong on Team/Enterprise plans.** A shares a skill
   with named colleagues (lands greyed-out in their list until enabled) or publishes to an
   org-wide directory; admins provision approved skills to everyone.

**Where the real differences are — these hold:**

1. **A skill is a behavior modifier for Claude, not a product.** Teammates B and C must each
   open Claude, know the skill exists, invoke it in context, and drive the conversation. No app
   window, no task-built UI, no own workspace, no standalone identity. We ship an application a
   person can use without ever hearing the words "model" or "prompt".
2. **Every user needs a seat.** A, B and C each pay $20–30+/user/month regardless of usage. An
   agentd agent with 100 occasional users does not cost 100 subscriptions — usage is metered on
   the agent, not the person.
3. **Outside the org boundary, sharing degrades to zip files and a curated directory.** A
   random creator cannot publish to strangers, and there is **no monetization anywhere** — no
   price on a skill, no revenue share, no per-agent metering. That entire layer of our stack
   has no Claude equivalent.
4. **Provider lock-in.** A skill runs only inside Anthropic's app, on Anthropic's models, at
   Anthropic's prices. Our agents route per-tool through our proxy (DeepSeek, Gemini, …) or run
   BYOK. If Anthropic repriced tomorrow, every Claude "agent" reprices with it; ours do not.

**The threat scenario to watch:** Anthropic adds paid plugins + creator payouts to the
marketplace. That collapses differences 3 and half of 2, and they start from an ecosystem that
already has Fortune-500 partners. Our window on "agents as sellable products" is open *now*;
it is not guaranteed to stay open.

**The line:** Claude lets you teach *their* assistant your tasks, inside their app, one
subscription per person. We let you create a standalone product — its own app, its own users,
its own economics — that isn't owned by a model vendor.

---

## Signals worth remembering

- **OpenAI killed its no-code Agent Builder after ~8 months** (June 2026), refocusing on
  enterprise "fleets of permissioned agents" (Workspace Agents, Frontier). The giants are
  converging on enterprise agents in their own clouds — none is building "an individual creates
  an agent and sells it as a product." That gap is either our opportunity or evidence the
  creator market is unproven. Plan for both readings.
- **"Local = secure" needs sharpening before it is used on anyone technical.** Cowork is also
  on the desktop, and an enterprise buyer hears "downloads an exe from a marketplace and runs it
  locally" as attack surface, not as a feature. Lead with the signing chain, the publisher
  roster, and the sandbox — the *controls*, not the location.
- **Our marketplace is empty and theirs are not.** Every comparison above is architecture vs
  architecture; on ecosystem we lose to all of them today. Seeding first-party agents is not
  cosmetic — it is the demo.

## Sources

- Replit: [Agent 3 announcement](https://blog.replit.com/introducing-agent-3-our-most-autonomous-agent-yet) · [2026 guide](https://espressio.ai/blog/replit-guide-2026/) · [pricing 2026](https://emergent.sh/learn/replit-pricing)
- Palantir: [Forbes on FDE](https://www.forbes.com/sites/stevebanker/2026/07/10/palantir-and-forward-deployed-engineering-what-should-we-believe/) · [FDE model analysis](https://medium.com/activated-thinker/a-comprehensive-analysis-of-palantirs-forward-deployed-engineering-model-4502a036b5e4) · [AI FDE docs](https://www.palantir.com/docs/foundry/ai-fde/overview)
- Claude: [Cowork launch](https://venturebeat.com/technology/anthropic-launches-cowork-a-claude-desktop-agent-that-works-in-your-files-no) · [skills ecosystem report](https://agentman.ai/blog/agent-skills-ecosystem-report-2026) · [org skill provisioning](https://support.claude.com/en/articles/13119606-provision-and-manage-skills-for-your-organization) · [skill sharing](https://support.claude.com/en/articles/12512180-use-skills-in-claude) · [conversational skill creation](https://support.claude.com/en/articles/12599426-how-to-create-a-skill-with-claude-through-conversation) · [enterprise deployment guide](https://sidbharath.com/blog/claude-skills-for-teams-enterprise-deployment-guide/)
- OpenAI: [Workspace Agents](https://venturebeat.com/orchestration/openai-unveils-workspace-agents-a-successor-to-custom-gpts-for-enterprises-that-can-plug-directly-into-slack-salesforce-and-more) · [AgentKit](https://openai.com/index/introducing-agentkit/) · [AgentKit status](https://kanerika.com/blogs/openai-agentkit/)
- Microsoft: [Agent Store](https://devblogs.microsoft.com/microsoft365dev/introducing-the-agent-store-build-publish-and-discover-agents-in-microsoft-365-copilot/) · [Copilot Studio 2026](https://techjacksolutions.com/ai-tools/microsoft-copilot/microsoft-copilot-studio/)
- No-code field: [platform comparison](https://tinycommand.com/ai-agents/best-no-code-ai-agent-platforms) · [14-platform comparison](https://www.morphllm.com/no-code-ai-agent)

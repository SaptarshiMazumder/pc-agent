# Projects / Agents / Chats redesign — implementation plan

> **Status:** APPROVED to build (user said "i want this all to be built"). Not started.
> **Owner context:** desktop client (`v2/clients/desktop`) + gateway (`v2/agentd`).
> **How to use this doc:** this is the single source of truth after context compaction.
> Read it top-to-bottom before implementing. Build in the phase order in §9. Update the
> checkboxes in §9 as you go.

---

## 1. Vision / mental model

Move from **agent-centric** navigation (pick an agent → see its chats) to
**project/chat-centric**, ChatGPT-style:

- **New chat** and **Search** are compact rows at the top of the sidebar.
- **Projects** is a single sidebar entry that opens a **full page** (list of projects) in the main
  area. Clicking a project opens that **project's detail page**.
- **Agents KEEP their sidebar listing** — the visual list of agents does NOT go away and does NOT
  become a single nav-row. What changes is the **click target**: clicking an agent no longer resumes
  its last chat / filters the list — it opens that **agent's detail page** (its chats, workspace,
  skills, config). Symmetric with Projects: a listing/entry → a single-entity detail page.
- The sidebar's chat list is **Recents = ALL chats across all agents** (like ChatGPT), each row
  showing its **agent color dot** + a faint **project badge** if it belongs to a project. This
  **coexists** with the Agents listing (two regions: Agents listing + all-chats Recents).
- A **project is a first-class conversational entity**: you can "message the project" and its
  **lead agent** answers, and that lead can **orchestrate other agents** (sub-agents) to do
  the job — all inside the one human chat.
- **File ownership follows context, not identity**: a chat inside a project writes to the
  **project's shared workspace**; a standalone chat writes to the **agent's own workspace**
  (today's behavior, unchanged). See §11.

Two layers, built in order:
- **Layer A — Layout/shell** (mostly frontend + small query backend): the sidebar restructure
  (keeping the Agents listing), Projects page, Project detail page, Agent detail page, chat badges,
  cross-agent recents. No behavior change.
- **Layer B — Project-as-entity** (real backend): `project.defaultAgentId` (lead) + members,
  "message the project → lead" routing, sub-agent runs inheriting the project, hiding internal
  agent-to-agent sessions from the human list, and **project-scoped workspace binding** (§11).

---

## 2. Current architecture (verified facts — real names)

### Client (`v2/clients/desktop/src/renderer/src`)
- **State**: zustand store `state/store.ts`. `view` state routes the main area. Current `View`
  union: `'chat' | 'store' | 'settings' | 'datasources' | 'account' | 'subscription'`.
  `App.tsx` switches on `view` and renders the matching component + always renders `<Sidebar/>`
  and `<Canvas/>`.
- **Store state (relevant)**: `agents: AgentInfo[]`, `currentAgentId`, `sessionRows: SessionRow[]`
  (**current agent's** chats only), `currentSessionKey`, `projects: ProjectRow[]`,
  `currentProjectId`, `openTabs: {id, agentId}[]`, `tabTitles: Record<id,title>`, `view`.
- **Store actions**: `newSession(projectId?)`, `resumeSession(id)`, `renameSession`,
  `deleteSession`, `moveSession(id, projectId)`, `duplicateSession(id)`, `exportSessionMd(id)`,
  `createProject(name)`, `renameProject`, `deleteProject`, `selectAgent(id)`,
  `createAgent({name,description,identity})`, `sendMessage(text, attachments?)`, `setView(view)`.
- **Internal helpers in store**: `refreshSessions()` → RPC `sessions.list` with
  `agentId: currentAgentId` (**PER-AGENT** — this is the key thing that changes),
  `refreshProjects()` → RPC `projects.list`. `historyToItems()` builds chat items.
  `gateway.on('sessions.changed', () => refreshSessions())`.
- **Protocol types** (`gateway/protocol.ts`):
  - `SessionRow = { sessionId, title, titleManual, projectId, messages, modified }` — **no agentId**.
  - `ProjectRow = { id, name, createdAt }`.
  - `AgentInfo = { id, name, version?, tagline?, suggestions?, color? }`.
- **Components**: `Sidebar.tsx` (brand, `.new-chat` lime button, `.search` input, Agents
  `SectionHead`+`.agents-list`, then `.sidebar-scroll` with Projects `SectionHead`+project rows +
  Chats `SectionHead`+standalone chats, footer `SettingsMenu`), `TabBar.tsx`, `ChatView.tsx`,
  `MessageItem.tsx`, `StoreView.tsx`, `SettingsView.tsx`, `AccountView.tsx`, `DataSourcesView.tsx`,
  `SubscriptionView.tsx`, `NewAgentModal.tsx`, `SettingsMenu.tsx`, `ChatMenu.tsx` (per-chat ⋯),
  `HoverTip.tsx` (`useHoverTip`), `SessionItem` (inside Sidebar.tsx).
- Reusable: `useHoverTip()` (portal tooltip), `ChatMenu` (rename/move-to-project/export/duplicate/
  delete), `agentColor()`/`agentInitials()` in `lib/agentPresentation`, `whenLabel()` in `lib/timefmt`.
- **Standing conventions** (memory): native `title=""` tooltips on interactive elements; global
  soft-scroll fade + auto-hide scrollbars via `lib/softScroll.ts` (don't hand-add); light default +
  dark toggle, lime via tokens; font-weight tokens `--fw-*` (dark redefines lighter, Segoe UI has no
  500 face). NEVER auto-commit.

### Gateway (`v2/agentd/presentation/gateway.py`)
- RPC dispatch in `_dispatch`. Existing session/project RPCs: `sessions.list`, `sessions.history`,
  `sessions.rename`, `sessions.delete`, `sessions.move` (writes `projectId` to meta),
  `sessions.duplicate`, `projects.list`, `projects.create`, `projects.rename`, `projects.delete`,
  `agents.list`, `agents.remove`, `chat.send`.
- `_resolve_state_dir(agentId) -> (agentId, state_dir)` — sessions are **per-agent** (each agent
  partitions its own transcripts under its `state_dir`). `agentId=""`/None → `"main"`.
- **`_all_state_dirs() -> list`** — already exists, returns all agents' state dirs "for project-wide
  session operations". Use this for cross-agent listing.
- `_sessions_list(params)` → `{sessions: list_sessions(state_dir), agentId}` for ONE agent.
- Sub-agent / agent-messaging machinery ALREADY EXISTS: `_build_subagents`, `_spawn_subagent(agent_id, task)`,
  `_message_agent(target_id, message)`, `_build_agent_messaging`, `_subagent_depth(session_key)`.
  Agent-to-agent session keys are like `agent:<id>:<peer>` (contain `:`, sanitized for filenames).
- Projects persisted by `agentd/infrastructure/memory/projects_store.py` in `config.state_dir`
  (daemon root) as `projects.json`: `{"projects":[{id, name, createdAt}]}`. Functions:
  `list_projects`, `get_project`, `create_project`, `rename_project`, `delete_project`,
  `clean_project_name`. **No defaultAgentId / members yet.**
- Sessions storage `agentd/infrastructure/memory/local_store.py`: `<state_dir>/sessions/<safe_id>.jsonl`
  (append-only transcript, header line has logical `id`) + `<safe_id>.meta.json` sidecar
  (`{title, manual, projectId, ...}`). Functions: `list_sessions(state_dir)` (returns
  `{sessionId(=stem), title, titleManual, projectId, messages, modified}`), `read_session_messages`,
  `write_session_meta(state_dir, id, **fields)` (generic merge), `read_session_meta`, `delete_session`,
  `duplicate_session`, `sessions_in_project(state_dir, project_id)`.

---

## 3. Target model (entities + relationships + decisions)

- **Agent** = configured assistant. **Stays GLOBAL** (usable anywhere). No per-agent project scope
  in this build. (Project-private agents = future.)
- **Project** = workspace. Gains: `defaultAgentId` (the **lead**/"project main") and `members`
  (list of agentIds curated into the project). Keeps `id, name, createdAt`.
- **Chat (session)** = conversation. Already tied to **one agent** (by which agent's folder holds
  its transcript) + **optional project** (`projectId` in meta). **No new fields, no migration.**

Relationships: Chat→Agent = 1 (immutable, = storage location). Chat→Project = 0..1 (meta tag).
Project→Agents = many (members + lead). Agent→scope = global.

**Resolved decisions:**
- Recents = **ALL chats across agents** (cross-agent listing).
- **Agents KEEP their sidebar listing** (unchanged visual). Clicking an agent opens that **agent's
  detail page** (`view:'agent'`) — NOT its last chat, NOT a filter. (Supersedes the earlier "Agents
  become a single nav-row → grid page" idea.) No separate all-agents grid page; the sidebar list IS
  the directory. "New agent" (+) stays on the Agents section header.
- **File ownership**: project chat → project workspace; standalone chat → agent workspace. One
  `effective_workspace()` seam at the RunContext handoff; agent memory/skills/sessions stay per-agent
  (only the file cwd follows the project). See §11.
- Sources tab in project view = **deferred**.
- Member rule: lead can call **any global agent freely** (default `openOrchestration=true`);
  `project.members` is the **curated roster** shown in the project UI + the default set offered to
  the lead. (Knob to restrict later.)
- Drop the lime `.new-chat` CTA (compact row instead).
- Search = **inline expand** (row → input, Esc/empty-blur collapses), keeps live filtering.

---

## 4. New screens (detailed specs)

### 4.1 Sidebar (restructured) — `components/Sidebar.tsx`
Top → bottom (full, expanded state):
1. **Brand** (logo + product name + collapse button) — unchanged.
2. **`✎ New chat`** — compact `.nav-row` (icon `SquarePen` from lucide + label). Click → `newSession()`
   (standalone, current/last agent). `title="New chat"`.
3. **`🔍 Search`** — compact `.nav-row` (icon `Search` + label). Click → expands into the search
   input in place (auto-focus). Typing filters Recents live (existing `query` state). Esc or
   empty-blur collapses back to the row. `title="Search chats"`.
4. **`📁 Projects`** — compact `.nav-row` (icon `Folder`). Click → `setView('projects')`. Active
   style when `view==='projects'||'project'`. `title="Projects"`.
5. **Agents section — KEPT (do not remove).** The existing `SectionHead` "Agents" (collapsible, with
   its `+` → `NewAgentModal`) + `.agents-list` of agent rows STAYS, visually unchanged. The **only**
   change is each agent row's click handler:
   - click an agent → **open that agent's detail page**: `setView('agent')` + `viewedAgentId = id`
     (NOT `selectAgent` → chat, NOT a chat-list filter). Active style when
     `view==='agent' && viewedAgentId===id`.
   - keep the agent avatar/dot + name + tagline exactly as now; keep `title=` tooltip.
   - (Starting a chat with an agent now happens from the agent's detail page — a "New chat with
     [agent]" button — or by clicking one of its chats there / in Recents.)
6. **Divider.**
7. **Recents** — small label "Recents" (or "Chats"), then the flat list of **ALL chats** (cross-agent),
   newest first. **Coexists with the Agents listing above.** Each row = `SessionItem` reworked:
   - left: **agent color dot** (`agentColor(agent.color, agentId)` — look up agent by row.agentId).
   - title (ellipsis).
   - faint **project badge** at the right/inline if `row.projectId` (project name via
     `projects.find(id)` lookup; small pill like ChatGPT's `ᴮᴶᵀ`).
   - hover: existing `⋯` `ChatMenu` + `useHoverTip` tooltip (name + `N msgs · date`).
   - click → `resumeSession(sessionId)` (must set currentAgentId to the row's agent — see §7).
   Search filters this list (title contains query). Keep soft-scroll fade.
- **Remove**: the old Projects/Chats `SectionHead`s from the sidebar (Projects moves to a nav-row;
  Chats becomes the flat Recents). **KEEP the Agents `SectionHead` + `.agents-list`.** Remove
  `.new-chat` button + `.search` box (replaced by nav-rows).
- Collapsed **rail** (`.sidebar--rail`): keep icon buttons; add a Projects icon. Keep the agent
  avatars in the rail (clicking one → that agent's detail page, matching the expanded behavior).

### 4.2 Projects page — new `components/ProjectsView.tsx` (`view: 'projects'`)
Mirrors ChatGPT "Projects" page:
- Header row: title **"Projects"**, right side: search-projects input + **"New"** button
  (creates a project → opens it).
- (Skip the All/Created/Shared tabs — single list.)
- Column header: `Name | Modified` (+ optionally chat count).
- Rows: folder icon + project name + modified date (+ chat count). Row hover → `⋯`
  (rename / delete). Click a row → `setView('project')` + `currentProjectId = id`.
- Data: `projects.overview` RPC (§6) → `[{id,name,createdAt,chatCount,modified,defaultAgentId,members}]`.
  Fallback: `projects.list` + client counts.
- Empty state: "No projects yet — New to create one."

### 4.3 Project view — new `components/ProjectView.tsx` (`view: 'project'`, uses `currentProjectId`)
Mirrors ChatGPT single-project page:
- Header: `📁 [project name]` + `⋯` menu (rename, delete, **set lead agent**, manage members).
  (Share button = deferred.)
- **"New chat in [project]"** composer/button → `newSession(projectId)` with the chat's agent =
  project lead (`defaultAgentId` || `main`); go to `view:'chat'`.
- Tabs: **Chats** (built) / Sources (deferred, hidden or disabled).
- Chats list: the project's chats **across agents** (RPC `sessions.list` with `projectId`, rows carry
  `agentId`). Each row: agent dot + title + snippet + date; reuse `SessionItem`. Click → open chat.
- (Layer B) A small "Agents in this project" strip (member chips + "add agent") near the header.

### 4.4 Agent DETAIL page — new `components/AgentView.tsx` (`view: 'agent'`, uses `viewedAgentId`)
Reached by clicking an agent in the **sidebar Agents listing** (which is KEPT — see §4.1). This is the
agent analogue of the single-**Project** detail page (§4.3): one agent, all its stuff.
- Header: agent avatar (`agentInitials`+`agentColor`) + name + tagline + version. Right side: **"New
  chat with [agent]"** button (`selectAgent(id)` → `newSession()` → `view:'chat'`) + `⋯` (edit /
  remove via existing flows; refuse remove for `main`).
- **Sections** (show what exists; stub/hide the rest gracefully):
  1. **Chats** — this agent's chats (RPC `sessions.list { agentId }` — already exists & is per-agent).
     Reuse `SessionItem`; click → open chat. This is the agent's own history (a subset of Recents).
  2. **Workspace** — files in the agent's workspace (`agents/<id>/workspace/`). Needs RPC
     `agents.detail`/`workspace.list` (§6) → `[{name, kind, size, modified}]`; render View/Download
     chips like deliverables. (First pass may stub with an empty state.)
  3. **Skills** — the agent's skills (shared/global + its own `agents/<id>/skills/`). From
     `agents.detail` → `[{name, description}]`. (First pass may stub.)
  4. (Optional) **Config** — model, tools allow/deny, tagline — read-only summary.
- There is **no separate all-agents grid page**; the sidebar listing is the directory. `NewAgentModal`
  is opened from the sidebar Agents section header `+` (unchanged).

### 4.5 Chat view (`ChatView.tsx` / `MessageItem.tsx`) — small
- (Layer A) No structural change. Optionally show the current chat's agent + project in the header/empty
  state.
- (Layer B) When in a project chat, the composer/header shows "in [project] · with [agent]" and allows
  switching the agent for the chat.

---

## 5. Data model changes

- **`ProjectRow`** (protocol.ts) + `projects_store.py` records: add `defaultAgentId?: string` and
  `members?: string[]` (agentIds). `projects.overview` also returns computed `chatCount` and
  `modified` (max session mtime in project, across agents).
- **`SessionRow`** (protocol.ts): add **`agentId: string`** (which agent owns the transcript). Populate
  in cross-agent listing (the state_dir → agentId mapping). For single-agent `sessions.list` it's the
  requested agent.
- **Session meta** (`.meta.json`): unchanged for Layer A. Layer B: sub-agent/internal sessions get
  `internal: true` (or rely on `agent:` key prefix) so they're excluded from human lists; and inherit
  `projectId` from the spawning chat.
- **Agents**: no change (stay global).

---

## 6. Backend RPCs (new + changed) — `gateway.py`

**Layer A:**
- **`sessions.list` — extend** to support cross-agent + project filters:
  - `params.all === true` → scan `_all_state_dirs()`, merge all agents' `list_sessions`, tag each row
    with its `agentId`, sort by `modified` desc. (Powers Recents.)
  - `params.projectId` (with `all` or explicitly cross-agent) → same scan, filter `projectId===X`,
    rows carry `agentId`. (Powers Project view.)
  - Exclude internal/agent-to-agent sessions (Layer B: key startswith `agent:` or meta `internal`).
  - Keep the existing single-agent behavior when neither is set (back-compat).
  - Return rows extended with `agentId`.
- **`projects.overview` — new**: `[{id, name, createdAt, defaultAgentId, members, chatCount, modified}]`.
  Compute counts/modified by one pass over `_all_state_dirs()` sessions grouped by `projectId`.
  (Or fold counts into `projects.list`.)
- **`agents.detail` — new** (powers the Agent detail page §4.4): given `agentId`, return
  `{id, name, tagline, version, model, workspaceFiles:[{name,kind,size,modified}], skills:[{name,description}]}`.
  Workspace files = a listing of the agent's `workspace/` (reuse the resource manager / a simple dir
  scan gated by `is_under_roots`); skills = shared library + `agents/<id>/skills/`. The agent's chats
  come from the existing `sessions.list { agentId }`, so `agents.detail` need not include them.

**Layer B:**
- **`projects.setLead` (or `projects.update`) — new**: set `defaultAgentId`. Persist via projects_store.
- **`projects.addMember` / `projects.removeMember` — new**: mutate `members`. Persist.
- **`chat.send` routing**: "message the project" is resolved **client-side** (client sends
  `agentId = project.defaultAgentId || 'main'` + `projectId`). If we want server-side default, add:
  when `projectId` set and `agentId` empty → look up `project.defaultAgentId`.
- **Sub-agent project inheritance**: in `_spawn_subagent` / run-creation, when the parent session has a
  `projectId`, propagate it to the child session meta and mark it `internal:true`. Ensures orchestration
  outputs stay in the project and stay hidden from human lists.
- Broadcast `projects.changed` on project mutations (client already listens to `sessions.changed`; add a
  `projects.changed` handler → `refreshProjects()`).

**projects_store.py changes:** extend records with `defaultAgentId`, `members`; add
`set_lead(state_dir, id, agentId)`, `set_members(state_dir, id, list)`, and include the fields in
`create_project`/`list_projects` output (default `""`/`[]`).

---

## 7. Frontend changes (store, routing, components)

**`state/store.ts`:**
- `View` union: add `'projects' | 'project' | 'agent'` (single-agent detail; **no** `'agents'` grid).
- New state: `viewedAgentId: string` (which agent the detail page is showing) + optional
  `agentDetail` cache. `recents: SessionRow[]` (all chats, cross-agent) OR reuse `sessionRows` as the
  cross-agent list. New `projectOverview` cache for the Projects page. Keep `currentProjectId`.
- **Agent click ≠ selectAgent**: clicking a sidebar agent sets `viewedAgentId` + `view:'agent'` (opens
  the detail page). `selectAgent(id)` (switch the agent for a NEW chat) is now only invoked from the
  agent detail page's "New chat with [agent]" button and by `resumeSession` (row's agent).
- `refreshRecents()` → `sessions.list { all: true }` → set the cross-agent list (rows carry `agentId`).
  Call on bootstrap + on `sessions.changed`.
- `refreshProjectChats(projectId)` → `sessions.list { projectId }` for Project view.
- `refreshProjectsOverview()` → `projects.overview`.
- **`resumeSession(id)` must set `currentAgentId`** to the row's `agentId` (since Recents are cross-agent)
  before/while loading history (history RPC is agent-scoped). Look up the row to get its agentId.
- `newSession(projectId?)`: agent = for project → `project.defaultAgentId || 'main'`; else current/last.
- Layer B actions: `setProjectLead(projectId, agentId)`, `addProjectMember`, `removeProjectMember`.
- Add `gateway.on('projects.changed', refreshProjectsOverview/refreshProjects)`.

**`App.tsx`:** add `{view==='projects' && <ProjectsView/>}`, `{view==='project' && <ProjectView/>}`,
`{view==='agent' && <AgentView/>}`.

**`Sidebar.tsx`:** rebuild per §4.1 — compact nav rows (New chat / Search / Projects) + **KEEP the
Agents `SectionHead`+`.agents-list`** (only rewire each agent row's click → `view:'agent'` +
`viewedAgentId`) + flat Recents-with-badges below. Remove the `.new-chat` CTA, `.search` box, and the
Projects/Chats `SectionHead`s (NOT the Agents one).

**New components:** `ProjectsView.tsx`, `ProjectView.tsx`, `AgentView.tsx` (single-agent detail).
Reuse `SessionItem`, `ChatMenu`, `useHoverTip`, `NewAgentModal`, `agentPresentation`, `whenLabel`, and
the deliverable View/Download chips for workspace files.

**CSS (`styles.css`):** `.nav-row` (compact icon+label row, subtle hover), project badge pill,
Projects/Agents/Project page layouts (reuse `.page-title`, `.btn`, table/list patterns already in
Store/Settings views). Respect `--fw-*` tokens, soft-scroll, tooltips.

---

## 8. Behaviors (the "brain" — Layer B)

1. **Message a project directly** → routes to the project's **lead agent** (`defaultAgentId`), fallback
   global `main`. The "New chat in [project]" composer shows "with [lead]" and lets you switch agent
   for that chat. Resolved client-side (send `agentId=lead`), optional server-side default.
2. **Lead orchestrates** → agent can spawn/message other agents (already works). Default: may call **any
   global agent freely**; `project.members` is the curated roster surfaced in UI. Sub-agent runs
   **inherit the project** (child meta `projectId` + `internal:true`).
3. **Internal sessions hidden** → human-facing lists (Recents, Project chats) exclude `internal`/`agent:`
   sessions. Only the human's chats appear; delegated sub-work shows as tool steps inside the parent chat.
4. **New chat defaults** → standalone new chat uses current/last agent; project new chat uses lead.

---

## 9. Phasing + task checklist

### Phase A1 — cross-agent Recents (backend + store) — no visual change yet ✅ DONE
- [x] `gateway.py`: extended `_sessions_list` for `all`/`projectId` cross-agent scan via new
      `_agent_state_dirs()` (agentId-aware), tag every row with `agentId`, exclude internal `agent_…`
      stems. Single-agent path also tags rows now.
- [x] `protocol.ts`: `SessionRow += agentId`.
- [x] `store.ts`: `recents` state + `refreshRecents()` (`sessions.list {all:true}`), wired into
      `handshake` + `sessions.changed`; `resumeSession` resolves the row from recents∪sessionRows and
      switches `currentAgentId` to `row.agentId` before the agent-scoped history fetch.
- [x] Verify: `tests/test_sessions_list.py` (3 tests: single-agent tag, cross-agent merge + internal
      hidden, projectId filter) + existing `test_projects.py` → 7 passed; desktop `npm run typecheck` clean.

### Phase A2 — sidebar restructure (compact rows + KEEP agents listing + Recents + badges) ✅ DONE
- [x] Extracted `SessionItem` into its own `components/SessionItem.tsx` (shared by sidebar + Project +
      Agent views); added a leading agent dot + trailing project badge (props `withAgentDot`/`withProjectBadge`).
- [x] `Sidebar.tsx` rebuilt: `NavRow` compact rows (New chat / Search(inline-expand) / Projects);
      **KEPT** the Agents `SectionHead`+`.agents-list`, only rewired agent-row click → `viewAgent(id)`
      (`view:'agent'`); flat cross-agent **Recents** list from `recents`; removed `.new-chat` CTA,
      `.search` box, and the Projects/Chats `SectionHead`s + project-grouping. Rail: agent → `viewAgent`,
      added a Projects rail icon.
- [x] `styles.css`: `.nav-rows`/`.nav-row`, `.session-dot`, `.proj-badge`, page-view helpers.
- [x] **Cross-agent correctness fix**: added `agentOf(sessionId)` in the store; rename/delete/move/
      duplicate/export now send the ROW's agentId (not `currentAgentId`) and mirror optimistic updates
      onto `recents` — so a ⋯-action on a Recents row targets the right agent's partition.
- [x] Verify: `npm run typecheck` clean.  (Visual pass pending a relaunch.)

### Phase A3 — Projects page + Project detail view ✅ DONE
- [x] No new RPC needed for counts — `ProjectsView` computes chatCount/last-activity **client-side from
      `recents`** (each row carries `projectId`). (`projects.overview` deferred as an optimization.)
- [x] `ProjectsView.tsx` (`view:'projects'`): searchable project list + New + inline rename + arm-to-delete;
      click → `openProject(id)`. `ProjectView.tsx` (`view:'project'`): project's cross-agent chats
      (`recents.filter(projectId)`) + "New chat in project" + back button. `App.tsx` routes added.
- [x] `store.ts`: `openProject(id)`; `View += 'projects'|'project'`.
- [x] Verify: typecheck clean.  (Visual pass pending.)

### Phase A4 — Agent DETAIL page ✅ DONE
- [x] `store.ts`: `View += 'agent'` + `viewedAgentId` + `viewAgent(id)` + `newChatWithAgent(id)`.
- [x] `gateway.py`: `agents.detail` (identity + workspace file listing + skills = shared library + own).
      Tested in `tests/test_agents_detail.py` (2 tests).
- [x] `AgentView.tsx` (`view:'agent'`): hero + "New chat with [agent]"; sections Chats
      (`recents.filter(agentId)`), Workspace (from `agents.detail`), Skills (from `agents.detail`).
      `App.tsx` route added.
- [x] Verify: `tests/test_agents_detail.py` 2/2 pass; typecheck clean.  (Visual pass pending.)

### Phase B1 — project data model (lead + members) ✅ DONE
- [x] `projects_store.py`: records normalized with `defaultAgentId`/`members` (old files upgrade on
      read); `set_lead`, `set_members`, and `project_workspace_dir(state_dir, id)`.
- [x] `gateway.py`: `projects.setLead` (validates agent, '' clears), `projects.addMember`,
      `projects.removeMember` (both via `_projects_member`), all broadcast `projects.changed`.
- [x] `protocol.ts`: `ProjectRow += defaultAgentId?, members?`.
- [x] `store.ts`: `setProjectLead`/`addProjectMember`/`removeProjectMember` (optimistic + rollback via
      `projects.changed`, handler already existed).
- [x] Project view UI — **SIMPLIFIED after user feedback** ("lead/members too complex"): NO
      lead/member jargon, NO chips. One quiet row: **`Answers as: [main (default) ▾]`**
      (`.proj-answers` + `.proj-lead-select`) driving `defaultAgentId`. main auto-delegates to
      specialists, so most users never touch it. The `members` field + add/remove RPCs + store
      actions remain BUILT but UI-DORMANT (re-surface later as a power feature if wanted).

### Phase B2 — message-the-project + @mention orchestration ✅ DONE
- [x] Client: `newSession(projectId)` switches `currentAgentId` to `project.defaultAgentId || 'main'`
      (validated against the live agent list) — messaging a project talks to its LEAD.
- [x] `gateway.py`: `_inherit_project(parent_agent, parent_key, child_agent, child_key)` wired into
      BOTH `_spawn_subagent` and `_message_agent` — a child run delegated from a project chat gets
      meta `projectId` + `internal:true` BEFORE it runs. Human lists already exclude `agent_…` stems.
- [x] **@mention delegation**: `agent_service._mention_directive(text, agent, tools)` — matches
      `@id`/`@Display Name` (case-insensitive) against the registry, excludes self, and appends a
      "Delegation directive" to the system prompt telling the agent to `message_agent(agent=<id>, …)`
      and weave the replies in. Fires ONLY when the message_agent tool is really in the toolset —
      a mention degrades to plain text otherwise (no false capability). Composer `+` menu inserts
      `@Name`. Config: `agent_messaging_enabled: true` added to `v2/agentd.config.json`.
- [x] Verified by `tests/test_layer_b.py` (mention matching, inheritance, RPCs).

### Phase B3 — project-scoped workspace binding (file ownership — §11) ✅ DONE
- [x] `projects_store.project_workspace_dir(state_dir, id)` → `<state_dir>/projects/<id>/workspace/`
      (mkdir on demand).
- [x] Resolution seam: `AgentService(resolve_workspace=…)` — new injected callable
      `(agent, session_id) -> workspace`; `handle_message` binds `RunContext.workspace` through it
      (fallback: agent's own — byte-for-byte old behavior when absent/failing). Composition root
      (`container._effective_workspace`) reads the session meta's `projectId`, verifies the project
      still exists (stale tag → agent's own), returns the project workspace.
- [x] Manifest/sweep follow the EFFECTIVE workspace: `set_run_context` happens BEFORE `_build_prompt`,
      so the container's closure just reads `current_workspace()` — no signature changes anywhere.
- [x] Path guard: `_allowed_file_roots()` += `<state_dir>/projects` so /file serves project deliverables.
- [x] Sub-agent inheritance (B2) means children bind the same project workspace automatically.
- [x] Verified: `tests/test_layer_b.py::test_workspace_binding` (project chat → project ws; no
      resolver → agent ws) + full backend suite 638 passed.
- [ ] (Deferred polish) system-prompt "you're working in project [name]" line + per-chat `scratch/`.

Each phase is shippable & reversible; **no transcript migration** anywhere.

---

## 10. Risks / notes
- **`resumeSession` agent switch**: Recents are cross-agent, so opening a chat must switch
  `currentAgentId` to the row's agent (history RPC is agent-scoped). Easy to miss → chats load empty.
- **Cross-agent scan cost**: `_all_state_dirs()` scans every agent's sessions dir; fine at local scale,
  cache overview.
- **Agent removed but chats remain**: rows may reference a deleted agentId → show a neutral dot + still
  open (state_dir resolves to an empty/own partition). Handle gracefully.
- **Tabs** already carry `agentId`; keep as-is.
- **Verification**: no debug port has been available this session — drive via a relaunch with
  `--remote-debugging-port=9334` when verifying, or rely on typecheck + targeted Python tests
  (`python -m pytest v2/tests/...`) as done for the session store.
- **Never auto-commit** (standing rule); leave changes in the working tree.
- **Daemon restart required for backend changes**: the daemon PERSISTS across app relaunches
  (`supervisor.ensure()` reuses a live daemon), so gateway.py edits (agentId on rows, cross-agent
  `all`, `agents.detail`) only take effect after the OLD daemon is killed and a fresh one spawns.
  A new renderer against a stale daemon means rows lack `agentId`. **Hardened for this**: the color
  helpers tolerate undefined ids, the store normalizes `agentId`, and a new **ErrorBoundary**
  (`components/ErrorBoundary.tsx`, wrapping `<App/>`) turns any render throw into a visible error
  instead of a blank white window. (This caused the first blank-screen crash: `hashColor(undefined)`.)

---

## 11. File ownership & workspace binding (design decision — Layer B)

**The question:** when an agent works *inside a project*, where do the files it creates go — the
project's workspace or the agent's own? And how does the agent "know"?

**Verified current reality:**
- Workspace is a **static property of the agent definition**, baked into the immutable `AgentSpec`.
  `file_registry._load_dir` sets `workspace = agents/<id>/workspace/` (or an explicit `agent.toml`
  path). Every agent gets its own isolated dir "so files never collide."
- The run hands that to tools at ONE seam: `agent_service.handle_message` →
  `set_run_context(RunContext(..., workspace=str(agent.workspace)))`
  ([agent_service.py:139-141](v2/agentd/application/services/agent_service.py#L139)).
- Every file/exec tool reads the root from ONE chokepoint: `run_context.current_workspace(default)`
  ([run_context.py:37](v2/agentd/application/run_context.py#L37)). Tools only ever use **relative**
  paths — they never choose a root.
- **Projects own no files today** — a project is just `{id,name,createdAt}` + a `projectId` tag on
  sessions. Zero filesystem presence.

**Decision:**
1. **Workspace becomes a per-RUN binding, not a per-agent-definition constant.** The agent does not
   know or choose its workspace; the daemon binds it before the first tool runs. Rule:
   `effective_workspace = project.workspace  if the run's session has a projectId, else agent.workspace`.
2. **A project becomes a real folder**: `<state_dir>/projects/<id>/workspace/`. Inside a project, ALL
   agents (lead + delegated sub-agents) share this one folder — a project is a **shared room**, so
   agent B can build on agent A's output. Standalone chats are unchanged (agent's own workspace).
3. **One shared project workspace** (not per-chat, not per-agent-within-project). Isolation = use a
   separate project or a standalone chat. (Optional: a per-chat `scratch/` subdir for throwaway.)
4. **Only the file cwd follows the project.** The agent's memory bank, sessions, skills, and identity
   stay per-agent — the agent is portable across projects; deliverables stay with the project.

**Why it's a clean build (one seam, no tool rewrites):**
- Change the single line `workspace=str(agent.workspace)` → `workspace=str(effective_workspace(agent,
  project_id_of(session_id)))`. The run already knows `session_id → projectId` (meta).
- Add `project_workspace_dir(id)` and register project workspaces in the `files.is_under_roots` guard
  so `/file` can serve project deliverables.
- Build the workspace-index / resource manifest over the EFFECTIVE workspace, so a project chat's
  prompt automatically SEES the project's existing files and reuses them (cognitive layer — free).
- Optional one-line system-prompt note when a run is project-scoped (tone only).

**Mental model:** it's `cd`. Same agent (program); the daemon `cd`s it into the project folder when the
chat belongs to a project, and into the agent's own folder otherwise. The agent's own workspace isn't
taken away — it's just not the active root for that run. **No project → the `else` branch → byte-for-byte
today's behavior** (no migration, standalone chats and the normal `main` chat unchanged).

Build in **Phase B3** (after B2 gives sub-agents `projectId` inheritance, which the child workspace
binding rides on).

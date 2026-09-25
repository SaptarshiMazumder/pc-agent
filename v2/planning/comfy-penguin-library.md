# Comfy Penguin Library — implementation plan

> **Status:** plan, awaiting phase-by-phase approval. Nothing built.
> **Scope:** `agents/comfy-artchitect` (window, plugin, AGENTS.md) plus two small daemon
> additions in `agent_runtime/presentation/gateway.py`.
> **How to use this doc:** the single source of truth after context compaction. Build in the
> phase order in §7, one phase per approval. Tick the boxes as phases land.

---

## 1. What this fixes

1. **Pasted files are unreadable.** The agent has no file tools by design (agent.toml: "NO FILE
   TOOLS", it used to open other chats' drafts). A workflow JSON attached to a message lands as a
   path nobody can open, and the agent asks the user to paste the contents.
2. **Reuse is the product and it is the weakest part.** A built workflow can only be downloaded
   and carried into a ComfyUI by hand. Nothing lets a later chat say "edit the jacket reel to use
   Kling" or "run the storyboard again with this face".
3. **Deleting a file is a negotiation.** Select, ask the agent, get refused, say yes, get it
   deleted. It should be one warning and a delete.

Chat isolation stays exactly as it is: a chat reads only its own `references/`, `workflows/`
and `outputs/`. The Library is the one shared place, and only what the user put there is in it.

## 2. The Library, defined

### 2.1 Where it lives

Inside the agent's workspace, so nothing new has to be fenced, shipped or served:

```
<workspace>/library/
  index.json                       one record per item (see 2.3)
  uploaded/                        the user put it here from their PC
    workflows/<name>/v1/…
    references/<file>
    files/<file>
  saved/                           it came out of a chat (workspace "Add to Library",
    workflows/<name>/v<N>/…          card "Save to Library", the finish chip, the agent)
    references/<file>
    files/<file>
```

- Hosted: `<state_dir>/accounts/<acct>/agents/comfy-artchitect/workspace/library/` — per
  account, because the workspace is (`user_state.account_workspace`). Desktop: the agent's
  workspace as today. The existing `workspace.list / upload / delete` RPCs already reach it
  with `rel: "library/…"`, so the Library tab needs no new listing or upload path.
- The sandbox ships a plugin only the workspace scopes its `plugin.toml` declares. The Library's
  **text** parts are declared (index, workflows, files); its **media** (`references/`) is not,
  so a library of renders never rides along on a node search. Media moves by the window, on
  the tool's instruction, the same way deletion already works (`comfy_delete` decides, the
  window acts through `workspace.delete`).

### 2.2 Kinds

| kind | what | stored as |
|---|---|---|
| workflow | a built graph, reusable | folder per version: `<name>.api.json`, `<name>.json`, `install_<name>.py`, `install_<name>.manifest.json` when they exist |
| reference | an image or video the user reuses as workflow input (a face, a product) | one file |
| file | anything else the agent may need to read (a workflow JSON pasted in, a prompt list, notes) | one file |

A workflow is versioned. Saving an edited workflow under an existing name adds `v<N+1>`; the
index points at the latest and keeps the list.

### 2.3 `index.json`

```json
{
  "version": 1,
  "items": [
    {
      "id": "wf_8f3a…",
      "kind": "workflow",
      "origin": "saved",
      "name": "jacket-reel",
      "note": "8 s Kling reel, two refs",
      "path": "saved/workflows/jacket-reel",
      "versions": [{ "v": 1, "at": "2026-09-24T01:12:00Z", "slots": ["model", "garment"] }],
      "from": { "chat": "e2e_comfy-artchitect_f28ee67e", "title": "AI Influencer Jacket Content Cost" },
      "created": "2026-09-24T01:12:00Z"
    }
  ]
}
```

- `from.title` is captured at save time by the window, so a deleted chat keeps its name and
  nothing has to look it up later. Uploaded items have no `from`.
- The window is the only writer of the index. The plugin reads it. (One writer, no merge
  problem; the agent never invents Library entries.)
- Workflow `slots` are the roles the graph declares (`@model`, `@garment`), read from the
  api.json with the same rule `reference_slots.roles_in` uses, so "Run again" knows what to ask
  for before any agent turn.

### 2.4 Daemon additions (the only ones)

Both in `gateway.py` beside `_workspace_delete`, app-callable like it:

- `workspace.copy { from, to, agentId }` — copy a file or folder from one workspace-relative
  path to another under the same root. Never overwrites unless `overwrite: true`. Traversal
  guarded by `_ws_resolve` like the rest. This is what moves a render into `library/saved/`
  and a Library reference into a chat's slot without the browser downloading and re-uploading
  megabytes.
- `workspace.upload` gains `overwrite: true` — today it dedupes names (`index (2).json`), which
  is right for user files and wrong for rewriting `library/index.json`.

Unit tests beside the existing workspace RPC tests. Daemon redeploy needed once, in phase 1.

## 3. The agent side (plugin + instructions)

New modules in `plugins/comfy-bridge/`, one class per file, filename = class name:

| file | what |
|---|---|
| `library_paths.py` | `library/` layout helpers; pure |
| `library_index.py` | `LibraryIndex` — reads `index.json`, resolves an item's files; read-only |
| `workflow_summary.py` | `WorkflowSummary(api_json)` — nodes (class, title), models named, prompts, slots, links; the thing the agent reads instead of raw JSON |
| `library_find_tool.py` | `LibraryFindTool` → `library_find(kind?, query?)` |
| `library_read_tool.py` | `LibraryReadTool` → `library_read(id, version?)` |
| `library_use_tool.py` | `LibraryUseTool` → `library_use(id, as?)` |

Registered in `comfy_bridge.register`. `plugin.toml` `[sandbox].workspace` adds
`library/index.json`, `library/uploaded/workflows`, `library/saved/workflows`,
`library/uploaded/files`, `library/saved/files`.

- **`library_find`** lists items: id, kind, origin, name, note, from-chat title, versions.
- **`library_read`** returns, for a workflow, the `WorkflowSummary` plus the api.json (capped);
  for a file, its text (capped, 200 KB); for a reference, metadata only (the agent never sees
  pixels, as today).
- **`library_use`** brings an item into THIS chat, after which every existing comfy tool works
  on it unchanged:
  - workflow → copied to `workflows/<chat>/<name>.api.json` and `.json`, its slots recorded
    with `reference_slots.record`, so `comfy_validate` and `comfy_run` see it as this chat's
    role. Returns the path.
  - reference → the tool does not have the bytes (media is not shipped). It returns
    `details.copy = [{from: "library/…/x.png", to: "references/<chat>/<role>.png"}]` and the
    window performs the copy through `workspace.copy`, exactly the `approvedDeletions` pattern
    in `agentd/workspace-files.ts`. The slot is then filled like any other.
  - file → same as read; nothing to copy.
- **No `library_save` tool.** Saving is a user act done by the window (§4), which is also the
  index's only writer. When the user asks the agent in chat to "save this to my library", the
  agent answers with the button (a chip in the message footer, §4.4) rather than writing. This
  keeps "the agent never invents Library entries" true by construction.

**AGENTS.md** gets one new numbered rule and two edits:

- New rule: *Attached and remembered files.* A non-image file the user attaches, or anything
  they call "the workflow from before", "the reel we made", "my saved face", is found with
  `library_find` and read with `library_read`. Never ask the user to paste a JSON. To edit or
  run a Library workflow, `library_use` it first; from then on it is this chat's workflow and
  the normal protocol applies (validate, price, ask, run).
- Rule 12 (do not read other jobs' drafts) stays and gains one line: the Library is not
  "another job's draft", it is what the user chose to keep, and it is read only through the
  three tools.
- Reference-media section: a slot can be filled from the Library too; the window does the
  copy, so from the agent's side a Library reference is a filled slot like any other.

## 4. The window

New folder `app/src/components/library/` (React, zustand, tokens only, same rules as
`creations/`): `LibraryPanel.tsx`, `LibrarySection.tsx`, `LibraryItemRow.tsx`,
`LibraryUploadZone.tsx`, `library.css`. Client module `app/src/agentd/library.ts`: read index
(via `/file`), write index (`workspace.upload` overwrite), `addToLibrary(artifact, origin)`,
`useInChat(item, sessionKey, role?)`, `deleteItem`, all through `workspace.*` RPCs.

### 4.1 The Library tab

A sibling of Workspace in the studio panel. `StudioTopBar` gets a two-way switch
"Workspace | Library" where the word "Workspace" sits today; `StudioDashboard` renders
`LibraryPanel` for the second. Same drawer behaviour on phones.

Layout, top to bottom:

- **Upload zone**: "Drop files here or choose" → `library/uploaded/<kind>/`, kind by
  extension (`.api.json` and ComfyUI `.json` → workflow, image/video → reference, else file).
- **Uploaded** section, then **Saved from chats** section. Each row: icon by kind, name,
  note (inline editable), size, and for saved items a caption "from: <chat title>" that is
  also a filter chip at the top of the section.
- Row actions: **Use in this chat** (workflow → copy into this chat's `workflows/`, reference →
  choose a slot if the chat has empty ones, else "Other"), **Run again** (workflows, phase 3),
  Download, Delete (one dialog: "Deleting files can break workflows that use them." then
  delete).
- Empty state copy that explains the tab in two lines, since nobody arrives here by instinct.

### 4.2 Workspace selection bar

Today: tick files → "Ask the agent to delete". Becomes two buttons:

- **Delete** — one dialog with the warning, then `workspace.delete` per file. No agent turn.
- **Add to Library** — `workspace.copy` each file into `library/saved/<kind>/` and add index
  entries with `from` = this chat. Workflows are gathered by role name (`stills.api.json` +
  `stills.json` + installer) into one versioned item.

### 4.3 My creations

`WorkflowCard` and the render tiles get **Save to Library**, same helper. The card's Delete
uses the same one-warning dialog.

### 4.4 The finish chip

When a turn ends and this chat has a validated workflow or new renders, the last message's
footer shows "Save <name> to Library" chips, one per workflow role, plus "Save renders". This
is the moment people care, so it is the main door into the Library. Window-side only; it reads
the chat's artifacts, no agent involvement.

### 4.5 Paste detection in the composer

`useRun.addFiles` today accepts images for analysis. For any other file it now does not
attach; the composer shows one line under the box: *"Comfy Penguin reads workflows and files
from your Library. Save it there and mention it?"* with one button that uploads the file to
`library/uploaded/<kind>/`, adds the index entry, and inserts "the Library file <name>" into
the message text (phase 2), later a real `@` mention (phase 3). Images keep today's behaviour.

## 5. Reuse, end to end (phase 3)

- **`@` mentions in the composer.** Typing `@` opens a picker over Library items. Sending a
  message with mentions makes the window bring each item into this chat first (workflow →
  `workspace.copy` into `workflows/<chat>/`, reference → copy into a slot named after the
  item or "Other"), then sends the text with the mention rendered as "Library workflow
  <name>, now in this chat's workflows folder". The agent then reads it with `library_read`
  or `comfy_validate` as it would any file of this chat. No new agent tool for mentions.
- **Slot picker sources.** `ReferenceSlots`' Replace / Add file becomes a two-entry menu:
  "From Library" (picker filtered to references) and "From this PC" (today's file input).
- **Run again.** On a Library workflow: start a new conversation, copy the workflow into its
  `workflows/`, seed the composer with "Run the Library workflow <name>, already in this chat.
  Same settings; I'll fill the slots." The agent's normal protocol (validate → price → ask →
  slots → run) takes it from there, and the slot record from `index.versions[].slots` lets the
  References panel show the empty slots before the first turn ends.

## 6. Deletion cleanup (phase 0)

- `comfy_delete` keeps only the folder fence (this chat's three folders, never `.studio/`,
  never elsewhere). The "rendered in this conversation and may be a later input" and the
  "bound slot / armed validation / recorded download" refusals go; the gate-record cleanup it
  did (drop a validation record, drop a download record) moves into the tools that read those
  records: `comfy_install` and `comfy_download` check the file still exists before acting on
  a record. Verify that in phase 0 before removing the refusals.
- The window's delete paths (workspace bar, My creations, Library) all use one
  `DeleteFilePrompt` with the single warning.

## 7. Phases

Each phase is independently shippable and gets its own plan-review before code.

- [x] **Phase 0 — Deletion.** (built 2026-09-24: rail Delete goes to the daemon after one warning; `comfy_delete` keeps only the folder fence; a validation whose workflow file is gone authorises no install) `comfy_delete` refusals reduced; `comfy_install` and
      `comfy_download` tolerate a missing file; workspace bar "Delete" goes direct; one
      warning everywhere. Files: `comfy_delete.py`, `comfy_bridge.py` (install/download),
      `FileExplorer.tsx`, `DeleteFilePrompt.tsx`, `workspace-files.ts`, tests.
      Deploy: daemon (agent package).
- [x] **Phase 1 — Storage and the agent.** (built 2026-09-24; e2e `library-edit-workflow` green on desktop staging) Layout and index (§2), daemon `workspace.copy`
      and upload `overwrite` (§2.4), the three tools and `WorkflowSummary` (§3), sandbox
      scopes, AGENTS.md rules, unit tests, and an e2e scenario: a workflow JSON in the Library,
      "edit this to use Kling", the agent reads it without asking for a paste.
      Deploy: daemon (RPCs) and agent package.
- [x] **Phase 2 — The Library in the window.** (built 2026-09-24, including the chips under a finished run: `SaveToLibraryChips`, drawn by the thread's `after` slot while nothing runs; hidden for anything the Library already holds from this chat) Library tab, Uploaded / Saved from chats,
      workspace bar "Add to Library", My creations "Save to Library", the finish chip, paste
      detection, `library.ts` client. Deploy: agent package.
- [x] **Phase 3 — Reuse.** (built 2026-09-24: `@` picker writes the item into the message in words; every slot has a From Library door; Run again seeds a new conversation; e2e `library-run-again`) `@` mentions, slot picker sources, Run again, and an e2e scenario
      that runs a saved workflow with two new references and no redesign.
      Deploy: agent package.

## 8. What does not change

- Chats cannot read each other's folders. Rule 12 stays. The sandbox scopes for chat folders
  stay `{session}`-bound.
- The agent never writes to the Library. The window does, on a user action.
- Images pasted into chat remain "for analysis only", as today.
- Nothing here touches the common auth modules, the skeleton, or other agents.

## 9. Open points to settle at each phase review

- Phase 1: cap sizes for `library_read` (proposed 200 KB text, api.json uncapped up to 1 MB).
- Phase 2: whether "Saved from chats" also offers a per-chat folder view; proposed no, the
  filter chip is enough.
- Phase 3: whether "Run again" should skip the ask when the saved version has a recorded
  price; proposed no, price is re-quoted every run.

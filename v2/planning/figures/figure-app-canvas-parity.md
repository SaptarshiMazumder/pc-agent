# "Bio Figure" (id: figure-create) app — canvas parity with the agentd client

TARGET AGENT: `agents/figure-create/` (display name "Bio Figure") — the NEW canvas-first
agent, NOT `figure-creator` (which keeps its name and its simpler strip UI). figure-create
already ships a first-cut canvas; parity below upgrades it to the shell's real one.

Status: PHASES 1-3 BUILT (uncommitted); phase 4 (template) pending. Goal: the figure-creator APP's canvas is for all intents the agentd
client's canvas — annotate, crop, vector/SVG editing, PNG editing, send-to-chat, artifact
actions (Convert-to-Vector) — plus a Workspace tab: the file tree, click-to-open, edit on
canvas, same as the shell.

## The constraint that shapes everything

The shell's canvas is REACT + fabric.js, living in `clients/ui/src` and wired to the shell's
store. Agent apps are build-free static pages served from `agents/<id>/ui/`. Hand-porting the
canvas to vanilla JS would fork it — two canvases to keep identical forever, the exact
maintenance trap the never-hardcode/reuse rule exists to prevent.

## Decision: ONE canvas, packaged like the SDK

`@agentd/canvas` — a new workspace package extracted FROM `clients/ui` (canvasViewers,
FabricEditor, WorkspaceTree, artifacts/canvasFile libs), built to a self-contained IIFE
(React + fabric bundled) exactly the way `@agentd/client` builds `agentd-client.js`, and
VENDORED into an agent's ui/ (`vendor/agentd-canvas.js|css`). The shell keeps importing the
same source as a workspace dep — one implementation, two consumers. Mount API (stable tier):

    agentd.mountCanvas(el, { client, artifact })      // viewer+editor incl. annotate/vector
    agentd.mountWorkspace(el, { client, agentId, onOpen })  // tree tab; opens into mountCanvas
    onSendToChat(cb)                                  // app decides what "send" does

Everything data-driven as in the shell: artifact actions from plugins.catalog (tools.invoke),
kinds from mime — zero figure-specific hardcoding in the package.

## Phases (each shippable)

1. [DONE] **Extract** — move viewer/editor/tree components into `clients/canvas/` (workspace pkg),
   decouple from the shell store (props/callbacks instead of useApp), shell re-imports.
   Zero visual change to the shell = the acceptance test.
2. [DONE] **Bundle** — IIFE build + vendor script (same `vendor:` flow the SDK uses); size budget
   ~1.2 MB (React+fabric); served from the agent dir so CSP/self-containment holds.
3. [DONE] **Adopt in figure-create** — replace its bespoke figure strip with mountCanvas; add the
   Workspace tab via mountWorkspace (workspace.list/upload/delete are already app-scoped
   methods). Bump + republish.
4. [PENDING] **Template** — add the canvas option to the build-agent skill's chat-app template so any
   new app agent can opt in (`[app] canvas = true` scaffolds the mounts).

## Interim (works today, zero code)

The SHELL already renders figure-creator with the full canvas + workspace tree (AgentView).
Until phase 3 lands, do figure work from the shell's agent view; the branded app window is
what's waiting on parity.

## Open questions

1. Bundle React inside `@agentd/canvas` (self-contained, big) vs require apps to be React —
   PROPOSAL: bundle; apps stay build-free, that's the platform promise.
2. Does send-to-chat post into the app's own session or the shell's? PROPOSAL: the app's
   (`chat.send` on its scoped connection) — the shell isn't necessarily open.
3. workspace.* write scope for app pages on hosted (tenant-root audit ties into Step 5).

# planning/

PlantUML diagrams for the project, organized by subject. Each `.puml` lives
next to its rendered `.png`.

## Structure

- **openclaw/** — diagrams of the OpenClaw reference (`reference/openclaw-main/`):
  architecture, flows, and tool deep-dives used to understand the system we ported.
  - `openclaw-flow.puml` — full agentic flow (terminal → gateway → loop → tools)
  - `openclaw-web-tools-flow.puml` — web_search / web_fetch / browser deep dive
- **agentd/** — diagrams of **our app** (`v2/`, package `agentd`):
  - `agentd-flow.puml` — full backend flow incl. the continuation/verification loop and
    skills (loadable SKILL.md playbooks, advertised in the prompt + read on demand)
- **platform/** — design docs + diagrams for the target platform, split by subject:
  - `app-architecture/` — overall clean/hexagonal app architecture: `ARCHITECTURE.md`,
    `REQUIREMENTS.md`, and the diagrams (`hexagonal-architecture`, `platform-architecture`,
    `trust-boundary`). **`request-flowchart`** is the easy-read top-down flowchart of one
    request through every layer (the reason→act loop); `request-flow` is the older
    sequence-diagram view of the same.
  - `tools/` — how tools are built (scalable provider/adapter pattern). *Design in progress.*

Add new categories as sibling folders (e.g. `comparisons/`, `sessions/`).

## Japanese versions

Every diagram has a Japanese counterpart named `<name>-ja.puml` / `<name>-ja.png`
(same structure and IDs; only the display text is translated — tool names, method
names, file names, and identifiers stay in English). They use a CJK font
(`skinparam defaultFontName "Yu Gothic UI"`) and **must be rendered with
`-charset UTF-8`** or the text comes out as mojibake.

> Gotcha: PlantUML names the output PNG from the `@startuml <name>` directive, not
> the filename. The `-ja.puml` files use `@startuml <name>-ja` so they render to
> `<name>-ja.png` and do not overwrite the English PNG.

## Rendering

The shared `plantuml.jar` sits at the repo root (`v2/`). Use a relative path with
one `../` per folder depth — `../../` from a top-level category (`agentd/`,
`openclaw/`), `../../../` from a nested one (`platform/app-architecture/`):

```
java -jar ../../plantuml.jar -tpng <name>.puml
# large diagrams (avoid 4096px clamp):
PLANTUML_LIMIT_SIZE=16384 java -DPLANTUML_LIMIT_SIZE=16384 -jar ../../plantuml.jar -tpng <name>.puml
# syntax-check only:
java -jar ../../plantuml.jar -checkonly <name>.puml
# Japanese version (UTF-8 required):
java -jar ../../plantuml.jar -charset UTF-8 -tpng <name>-ja.puml
# from a nested folder (e.g. platform/app-architecture/) use ../../../ instead.
```

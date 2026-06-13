# planning/

PlantUML diagrams for the project, organized by subject. Each `.puml` lives
next to its rendered `.png`.

## Structure

- **openclaw/** — diagrams of the OpenClaw reference (`reference/openclaw-main/`):
  architecture, flows, and tool deep-dives used to understand the system we ported.
  - `openclaw-flow.puml` — full agentic flow (terminal → gateway → loop → tools)
  - `openclaw-web-tools-flow.puml` — web_search / web_fetch / browser deep dive
- **agentd/** — diagrams of **our app** (`v2/`, package `agentd`):
  - `agentd-flow.puml` — full backend flow incl. the continuation/verification loop

Add new categories as sibling folders (e.g. `comparisons/`, `tools/`, `sessions/`).

## Rendering

The shared `plantuml.jar` sits at the repo root. From inside a category folder:

```
java -jar ../../plantuml.jar -tpng <name>.puml
# large diagrams (avoid 4096px clamp):
PLANTUML_LIMIT_SIZE=16384 java -DPLANTUML_LIMIT_SIZE=16384 -jar ../../plantuml.jar -tpng <name>.puml
# syntax-check only:
java -jar ../../plantuml.jar -checkonly <name>.puml
```

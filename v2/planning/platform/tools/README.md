# tools/ — tool architecture (design in progress)

Design docs + diagrams for **how tools are built**: a single, scalable
provider/adapter pattern applied to every tool, so a tool (the dispatcher) is
never coupled to a specific backend.

Motivating case: `web_search` should run a **chain of swappable providers**
(e.g. Gemini Google-Search grounding → Brave → DuckDuckGo) chosen by config —
the tool itself stays backend-agnostic. The same shape should generalize to all
tools (each native/provider integration is one isolated adapter behind a port).

## Diagrams

- **`tools-architecture.puml`** — the exact, scalable architecture for **all** tools:
  the `Tool` port + `ToolRegistry`, direct tools vs **dispatcher tools** (which add a
  `Provider` port + a config-selected adapter chain), and where the composition root
  wires it. The rule: *a tool is a dispatcher with no backend knowledge; a backend is
  one adapter behind a Provider port; the chain/order is config.*
- **`web-tools-providers.puml`** — the detailed view of the web-facing tools
  (`web_search`, `web_fetch`, `browser`). Shows the **SearchProvider chain** and how
  **Gemini's Google-Search grounding** plugs in (free via the Gemini key) alongside the
  other **LLM-native** (OpenAI/Kimi/MiniMax/xAI) and **agnostic** (Brave/Tavily/Exa/
  SearXNG/DuckDuckGo) providers — including the exact Gemini-grounding call flow.

Render: `java -jar ../../../plantuml.jar -tpng <name>.puml` (see [../../README.md](../../README.md)).

> Next step: implement the `SearchProvider` port + `providers/` adapters and the
> Gemini-grounding provider, per these diagrams. For the overall app/clean
> architecture, see [../app-architecture/](../app-architecture/).

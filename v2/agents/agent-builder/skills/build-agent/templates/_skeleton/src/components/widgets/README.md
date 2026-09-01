# Placeholder widgets — chat template

**Everything in this folder is a PLACEHOLDER.** Each file carries `@placeholder` in its header,
and `validate_agent` refuses to pack or publish while that tag is anywhere in `app/src`. Find them
from any template with:

```
grep -rn "@placeholder" app/src
```

They exist to show three shapes a conversational agent almost always needs, already written in
this template's style. They are **not** this agent's screen.

## The one rule

**Decide what the agent needs first, then look at what shipped here.** Keep whichever of these is
closest to a real need, make it real, and delete the rest. An agent that produces nothing needs no
result card; an agent with no standing caveat needs no note. Keeping one because it renders is how
a window ends up looking finished and saying nothing.

Each ends in one of two states — **adopted** (changed to real data, tag deleted, file renamed so
the imports stop saying `Placeholder`) or **deleted**, along with every import of it.

## What is here

| File | The shape | Where it belongs |
| --- | --- | --- |
| `PlaceholderResultCard.tsx` | The thing the agent MADE — a title, a meta line, and chips where some are missing | Inside an assistant turn, rendered from a tool's result or an artifact |
| `PlaceholderStat.tsx` | One figure about this run, with an optional bar | The aside beside the conversation |
| `PlaceholderNote.tsx` | A standing sentence that is always true of this agent | The foot of the aside |
| `PlaceholderLibraryView.tsx` | A whole SECOND SCREEN, reached from the rail | A destination beside Credits |

`PlaceholderLibraryView` is the one with **two things to delete**: the file, and the
`extraDestinations` prop plus the `view === 'library'` branch in `App.tsx`. Deleting only the
file leaves a rail row that opens nothing; deleting only the branch leaves a screen nobody can
reach. It is here because the moment an agent says "forty-eight recipes on the shelf" it has a
corpus, and a corpus reachable only by scrolling back through old turns is one nobody opens
twice — but an agent whose answers are prose has nothing to shelve, and should delete it.

`PlaceholderResultCard` is deliberately **not wired into the transcript**. A result card belongs in the
transcript, next to the turn that produced it — which means rendering it from your own tool
results (see `agentd/artifacts.ts` and `components/ArtifactView.tsx`), not from a fixed spot in
the layout. It is here as a reference for that, and it should be deleted if this agent's answers
are prose.

## What is NOT a placeholder

- `src/common/` — sign-in, credits, organizations, settings. Copied verbatim into every agent and
  diffed by the validator. Their **CSS is yours**; their fields and flows are not.
- The opening screen's suggestion cards in `App.tsx`. Those are real UI an author edits — the
  text is a placeholder in the ordinary sense, but the component stays.

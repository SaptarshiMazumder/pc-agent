"""SemanticSearchTool — retrieval over what the documents actually say (RAG).

`library_search` finds a word you wrote in a note. This finds the PASSAGE that answers a question,
in the source text, even when it shares no words with the question. That is the difference between
"where did I put that" and "what does the evidence say".

IT RETURNS PASSAGES, NOT AN ANSWER. The agent composes the answer from what comes back and cites
the document. A tool that returned a synthesised answer would be a second, invisible model call
whose reasoning nobody can inspect — and the citation is the whole reason to trust it.

THE MODE IS ALWAYS IN THE RESULT. Semantic retrieval needs an embedding provider; without one this
falls back to BM25 keyword matching, which is a genuinely different quality of answer. Every result
says which one ran and why, so a lexical answer can never be mistaken for a semantic one. That is
the difference between a fallback and a cover-up.
"""

from __future__ import annotations

import json
import re

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from document_embedder import DocumentEmbedder
from library_database import LibraryDatabase, unpack_vector
from library_note_store import database_path, library_root

_WORD = re.compile(r"[^\w]+", re.UNICODE)


class SemanticSearchTool(Tool):
    name = "library_ask"
    label = "Ask the library"
    default_retryable = True
    default_timeout_sec = 60.0
    description = (
        "Search the FULL TEXT of every indexed document by meaning and get back the passages "
        "that bear on your question, each with the document it came from. Use it to answer "
        "questions about what the sources actually say — then answer FROM the passages and cite "
        "them. Falls back to keyword matching when no embedding model is configured, and the "
        "result always states which mode ran."
    )
    parameters = {
        "type": "object",
        "required": ["question"],
        "properties": {
            "question": {"type": "string", "description": "A real question, not keywords."},
            "k": {"type": "integer", "minimum": 1, "description": "Passages to return. Default 8."},
        },
    }

    def __init__(self, embedder: DocumentEmbedder, db: LibraryDatabase | None = None):
        self._embedder = embedder
        self._db = db

    def _database(self) -> LibraryDatabase:
        db = self._db or LibraryDatabase(database_path())
        db.ensure_schema()
        return db

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            question = str(params.get("question") or "").strip()
            if not question:
                return ToolResult.text("library_ask needs a `question`", is_error=True)
            k = max(1, int(params.get("k") or 8))

            db = self._database()
            stats = db.stats()
            if stats["documents"] == 0:
                return ToolResult.text(
                    f"Nothing is indexed yet in {database_path()} (notes live in "
                    f"{library_root()}). Ingest a document, or point me at a folder with "
                    f"library_scan — library_ask can only search text that library_put stored."
                )

            passages, mode, reason = self._retrieve(db, question, k, stats)
            if not passages:
                return ToolResult.text(
                    json.dumps(
                        {
                            "mode": mode,
                            "mode_reason": reason,
                            "indexed": stats,
                            "passages": [],
                            "note": (
                                "Nothing matched. Say so — do not answer from memory of the "
                                "conversation and present it as what the documents say."
                            ),
                        },
                        indent=1,
                    )
                )
            return ToolResult.text(
                json.dumps(
                    {
                        "mode": mode,
                        "mode_reason": reason,
                        "indexed": stats,
                        "passages": passages,
                        "note": "Answer from these passages and cite the document each came from.",
                    },
                    indent=1,
                )
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"library_ask failed: {type(e).__name__}: {e}", is_error=True)

    def _retrieve(self, db, question: str, k: int, stats: dict):
        """Semantic when it can be, lexical when it cannot — and it always says which."""
        if not self._embedder.available:
            return (
                self._lexical(db, question, k),
                "lexical",
                f"keyword matching: {self._embedder.unavailable_reason}",
            )
        if stats["embedded"] == 0:
            return (
                self._lexical(db, question, k),
                "lexical",
                "keyword matching: nothing has been embedded yet",
            )
        try:
            return (
                self._semantic(db, question, k),
                "semantic",
                f"meaning-based, model {self._embedder.model}",
            )
        except Exception as e:  # noqa: BLE001 — the reason travels with the result
            return (
                self._lexical(db, question, k),
                "lexical",
                f"embedding the question FAILED ({type(e).__name__}: {e}) — fell back to keyword "
                f"matching. Tell the user; results are weaker than usual.",
            )

    def _semantic(self, db, question: str, k: int) -> list[dict]:
        from agent_runtime.infrastructure.embeddings import cosine

        query = self._embedder.embed([question])[0]
        scored = []
        for row in db.vectored_chunks():
            scored.append((cosine(query, unpack_vector(row["vector"])), row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {
                "document": row["slug"],
                "title": row["title"],
                "passage_no": row["ord"],
                "similarity": round(score, 4),
                "text": row["text"],
            }
            for score, row in scored[:k]
        ]

    def _lexical(self, db, question: str, k: int) -> list[dict]:
        terms = [t for t in _WORD.split(question) if len(t) > 2]
        if not terms:
            return []
        # Quote every term: an unquoted apostrophe or a bare `-` is FTS5 *syntax*, and a user's
        # question is prose, not a query language.
        match = " OR ".join(f'"{t}"' for t in terms)
        return [
            {
                "document": row["slug"],
                "title": row["title"],
                "passage_no": row["ord"],
                "bm25": round(row["score"], 4),
                "text": row["text"],
            }
            for row in db.search_text(match, k)
        ]

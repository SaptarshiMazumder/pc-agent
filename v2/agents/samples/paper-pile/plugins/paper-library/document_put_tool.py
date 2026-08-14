"""DocumentPutTool — put a document's FULL TEXT into the index, and file its source under the
name the library knows it by.

THE NOTE IS THE SUMMARY; THIS IS THE EVIDENCE. Without it, the agent can only ever answer from
its own summaries, so any question the note did not anticipate ("what sample size?") requires
re-reading the PDF. With it, `library_ask` retrieves the actual sentences.

ASSORTING IS A COPY, NEVER A MOVE. The user pointed the agent at their folder; renaming or
relocating files inside it is destructive and not what "add this to my library" asks for. The copy
lands in `library/sources/<slug>.<ext>` so the sources carry the same names as the notes, and the
original folder is left exactly as it was found.

EMBEDDING FAILURE IS REPORTED, NOT SWALLOWED — and it does not fail the put. The text is stored and
lexically searchable either way; a failure here means search is degraded, which the result says in
so many words. Returning an error instead would tell the agent the document was not stored, and it
would put it again.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.write_scope import check_read

from document_chunker import DocumentChunker
from document_embedder import DocumentEmbedder
from library_database import LibraryDatabase, pack_vector
from library_note_store import database_path, sources_root


class DocumentPutTool(Tool):
    name = "library_put"
    label = "Index document"
    default_retryable = False  # writes; a blind retry would redo the copy and re-embed
    default_timeout_sec = 180.0
    description = (
        "Index a document's full text so it can be searched by meaning later, and file a copy of "
        "the source under the library's name for it. Call this right after writing the note. "
        "Give `source_path` and the text is extracted for you (.pdf/.docx/.xlsx/.pptx), or pass "
        "`text` directly if you already have it."
    )
    parameters = {
        "type": "object",
        "required": ["slug"],
        "properties": {
            "slug": {"type": "string", "description": "The note's slug, without .md."},
            "source_path": {"type": "string", "description": "The original file, if there is one."},
            "text": {"type": "string", "description": "The document text, if you already have it."},
            "title": {"type": "string", "description": "Human title, for search results."},
            "assort": {
                "type": "boolean",
                "description": "Copy the source into library/sources/<slug>.<ext>. Default true.",
            },
        },
    }

    def __init__(
        self,
        chunker: DocumentChunker,
        embedder: DocumentEmbedder,
        db: LibraryDatabase | None = None,
    ):
        self._chunker = chunker
        self._embedder = embedder
        self._db = db

    def _database(self) -> LibraryDatabase:
        db = self._db or LibraryDatabase(database_path())
        db.ensure_schema()
        return db

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            slug = str(params.get("slug") or "").strip().removesuffix(".md")
            if not slug:
                return ToolResult.text("library_put needs a `slug`", is_error=True)

            raw_source = str(params.get("source_path") or "").strip()
            text = str(params.get("text") or "")
            source = check_read(Path(raw_source).expanduser()) if raw_source else None

            if not text and source is not None:
                text = self._extract(source)
            if not text.strip():
                # The whole point of the agent's "never summarise what you could not read" rule.
                # An empty index entry would make the document appear searchable and return
                # nothing, which is indistinguishable from "the document does not discuss it".
                where = f" from {source.name}" if source else ""
                return ToolResult.text(
                    f"could not extract any text{where} — nothing to index, and nothing to "
                    f"summarise. Usually a scanned PDF with no text layer, or a corrupt file. "
                    f"Tell the user which file and what happened; do NOT write a note for it "
                    f"from the file name.",
                    is_error=True,
                )

            chunks = self._chunker.split(text)
            if not chunks:
                return ToolResult.text("the text produced no chunks — nothing to index", is_error=True)

            db = self._database()
            stat = source.stat() if source is not None else None
            doc_id = db.upsert_document(
                slug=slug,
                source_path=str(source) if source else "",
                sha256=self._hash(source) if source is not None else "",
                size=stat.st_size if stat else len(text.encode("utf-8")),
                mtime=stat.st_mtime if stat else 0.0,
                title=str(params.get("title") or slug.replace("-", " ")),
                indexed_at=date.today().isoformat(),
            )
            db.replace_chunks(doc_id, chunks)

            result = {
                "slug": slug,
                "chunks": len(chunks),
                "characters": len(text),
                "search": "lexical",
            }

            if self._embedder.available:
                try:
                    pending = db.chunks_without_vectors(limit=len(chunks))
                    vectors = self._embedder.embed([c["text"] for c in pending])
                    db.set_vectors(
                        [(c["id"], pack_vector(v)) for c, v in zip(pending, vectors)]
                    )
                    result["search"] = "semantic + lexical"
                    result["embedded"] = len(pending)
                    result["embed_model"] = self._embedder.model
                except Exception as e:  # noqa: BLE001 — named in the result, never hidden
                    result["EMBEDDING_FAILED"] = (
                        f"{type(e).__name__}: {e} — the document IS indexed and lexically "
                        f"searchable, but library_ask will fall back to keyword matching for it. "
                        f"Tell the user; this usually means the embedding key is missing or "
                        f"expired."
                    )
            else:
                result["semantic_unavailable"] = self._embedder.unavailable_reason

            if params.get("assort", True) and source is not None:
                result["filed_copy"] = self._assort(source, slug)

            return ToolResult.text(json.dumps(result, indent=1))
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"library_put failed: {type(e).__name__}: {e}", is_error=True)

    def _extract(self, source: Path) -> str:
        """Office formats through the runtime's extractor; text formats read directly. Reusing
        `documents.extract_text` is what keeps this plugin dependency-free — pypdf and
        python-docx are already the daemon's, and `read` extracts exactly the same way.

        NEVER FALL BACK TO read_text FOR A BINARY DOCUMENT. `extract_text` answers None for both
        "unsupported" and "corrupt / no text layer / missing dependency", so for a .pdf it means
        extraction FAILED. Reading the bytes instead yields a few characters of PDF header, which
        chunks and indexes perfectly happily — and the library then holds an entry that looks like
        a document and contains none of it. An empty return here becomes a visible error upstairs,
        which is the only honest outcome.
        """
        from agent_runtime.infrastructure.documents import extract_text, is_document

        if is_document(source):
            return extract_text(source) or ""
        return source.read_text(encoding="utf-8", errors="ignore")

    @staticmethod
    def _hash(source: Path) -> str:
        from folder_scan_tool import sha256_of

        return sha256_of(source)

    @staticmethod
    def _assort(source: Path, slug: str) -> str:
        dest_dir = sources_root()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{slug}{source.suffix.lower()}"
        shutil.copy2(source, dest)  # copy2 keeps the original timestamps
        return str(dest)

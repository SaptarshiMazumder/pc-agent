"""LibraryDatabase — the SQLite store behind the library: what has been ingested, and its text.

WHY A DATABASE AND NOT MORE MARKDOWN. The notes stay the product; this holds the three things
markdown cannot answer:

1. **"Have I already done this file?"** — a content hash per source file. Without it, pointing the
   agent at the same folder twice re-reads everything, and a folder scan is only useful if it is
   idempotent.
2. **The full text, in chunks** — a note is a summary; retrieval needs the actual sentences.
3. **Vectors** — so "what did I read about X" can match meaning rather than spelling.

FTS5 IS NOT AN OPTIMISATION HERE, IT IS THE FLOOR. Embeddings need a provider and a key; a laptop
with neither must still be able to search. So every chunk is written to FTS5 whether or not it is
ever embedded, and the search tool reports which mode answered.

The FTS table is a plain (non-external-content) one carrying its own `chunk_id`. An
external-content table is the idiomatic choice and needs three triggers to stay in sync; for a
sample that is read as documentation, explicit double-writes are clearer than trigger subtleties.

CONNECTION PER OPERATION. Tools run on whatever thread the loop hands them, and a sqlite3
connection belongs to the thread that opened it. Opening per call sidesteps that entirely; WAL
keeps concurrent readers off the writer's back.
"""

from __future__ import annotations

import sqlite3
from array import array
from contextlib import closing
from pathlib import Path

#: Bumped when the schema changes in a way an existing file cannot satisfy.
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY,
    slug        TEXT    NOT NULL UNIQUE,
    source_path TEXT    NOT NULL DEFAULT '',
    sha256      TEXT    NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    mtime       REAL    NOT NULL DEFAULT 0,
    title       TEXT    NOT NULL DEFAULT '',
    indexed_at  TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS documents_sha ON documents(sha256);
CREATE INDEX IF NOT EXISTS documents_src ON documents(source_path);

CREATE TABLE IF NOT EXISTS chunks (
    id     INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord    INTEGER NOT NULL,
    text   TEXT    NOT NULL,
    vector BLOB
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, chunk_id UNINDEXED);
"""


def pack_vector(values) -> bytes:
    """A float vector -> bytes for the BLOB column. float32: half the size of float64 and well
    inside the precision cosine similarity needs."""
    return array("f", values).tobytes()


def unpack_vector(blob: bytes) -> list[float]:
    out = array("f")
    out.frombytes(blob)
    return list(out)


class LibraryDatabase:
    """Every read and write of the index. Owns the schema; owns no policy.

    Raises `sqlite3.Error` outward. A tool that cannot reach its own index must say so — a caller
    that silently treats a broken database as an empty one reports "nothing found" for a library
    that is full, which is worse than a visible failure.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def ensure_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    # -- documents --------------------------------------------------------------------------

    def state_of(self, source_path: str, sha256: str) -> str:
        """`new`, `changed` or `indexed` — the whole point of the scan.

        Matched on PATH first: a file whose bytes changed is `changed`, and the same bytes under a
        new name is still `new` work for the library only if that path was never ingested. Hash
        equality under a different path means a duplicate, reported as `duplicate` so the caller
        can skip it without writing a second note for the same document.
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT sha256 FROM documents WHERE source_path = ?", (source_path,)
            ).fetchone()
            if row is not None:
                return "indexed" if row["sha256"] == sha256 else "changed"
            dupe = conn.execute(
                "SELECT 1 FROM documents WHERE sha256 = ? LIMIT 1", (sha256,)
            ).fetchone()
            return "duplicate" if dupe is not None else "new"

    def upsert_document(
        self,
        *,
        slug: str,
        source_path: str,
        sha256: str,
        size: int,
        mtime: float,
        title: str,
        indexed_at: str,
    ) -> int:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO documents (slug, source_path, sha256, size, mtime, title, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    source_path = excluded.source_path,
                    sha256      = excluded.sha256,
                    size        = excluded.size,
                    mtime       = excluded.mtime,
                    title       = excluded.title,
                    indexed_at  = excluded.indexed_at
                """,
                (slug, source_path, sha256, size, mtime, title, indexed_at),
            )
            row = conn.execute("SELECT id FROM documents WHERE slug = ?", (slug,)).fetchone()
            return int(row["id"])

    def delete_document(self, slug: str) -> bool:
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT id FROM documents WHERE slug = ?", (slug,)).fetchone()
            if row is None:
                return False
            doc_id = int(row["id"])
            ids = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,))]
            conn.executemany("DELETE FROM chunks_fts WHERE chunk_id = ?", [(i,) for i in ids])
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            return True

    def documents(self) -> list[dict]:
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY slug")]

    def document_inventory(self) -> list[dict]:
        """Every RAG-indexed source with per-document retrieval coverage.

        This is deliberately sourced from SQLite rather than the markdown notes: a note is a
        summary, while a row here proves the source text was chunked for retrieval.
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT d.*,
                       COUNT(c.id) AS chunks,
                       SUM(CASE WHEN c.vector IS NOT NULL THEN 1 ELSE 0 END) AS embedded_chunks
                FROM documents d
                LEFT JOIN chunks c ON c.doc_id = d.id
                GROUP BY d.id
                ORDER BY d.indexed_at DESC, d.title COLLATE NOCASE, d.slug
                """
            )
            return [dict(r) for r in rows]

    # -- chunks -----------------------------------------------------------------------------

    def replace_chunks(self, doc_id: int, texts: list[str]) -> int:
        """Re-indexing a document REPLACES its chunks. Appending would leave the old text
        searchable, so a corrected document would keep answering with the version it replaced."""
        with closing(self._connect()) as conn, conn:
            old = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,))]
            conn.executemany("DELETE FROM chunks_fts WHERE chunk_id = ?", [(i,) for i in old])
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            for ordinal, text in enumerate(texts):
                cur = conn.execute(
                    "INSERT INTO chunks (doc_id, ord, text) VALUES (?, ?, ?)",
                    (doc_id, ordinal, text),
                )
                conn.execute(
                    "INSERT INTO chunks_fts (text, chunk_id) VALUES (?, ?)",
                    (text, cur.lastrowid),
                )
            return len(texts)

    def chunks_without_vectors(self, limit: int = 256) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, text FROM chunks WHERE vector IS NULL ORDER BY id LIMIT ?", (limit,)
            )
            return [dict(r) for r in rows]

    def set_vectors(self, pairs: list[tuple[int, bytes]]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executemany("UPDATE chunks SET vector = ? WHERE id = ?", [(b, i) for i, b in pairs])

    def vectored_chunks(self) -> list[dict]:
        """Every embedded chunk with its document. A personal library is thousands of chunks, so
        brute-force cosine in Python is milliseconds and needs no vector extension."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.text, c.vector, c.ord, d.slug, d.title
                FROM chunks c JOIN documents d ON d.id = c.doc_id
                WHERE c.vector IS NOT NULL
                """
            )
            return [dict(r) for r in rows]

    def search_text(self, query: str, limit: int) -> list[dict]:
        """FTS5 lexical search — the floor that always works."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.text, c.ord, d.slug, d.title, bm25(chunks_fts) AS score
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.chunk_id
                JOIN documents d ON d.id = c.doc_id
                WHERE chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (query, limit),
            )
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with closing(self._connect()) as conn:
            one = lambda sql: int(conn.execute(sql).fetchone()[0])  # noqa: E731
            return {
                "documents": one("SELECT COUNT(*) FROM documents"),
                "chunks": one("SELECT COUNT(*) FROM chunks"),
                "embedded": one("SELECT COUNT(*) FROM chunks WHERE vector IS NOT NULL"),
            }

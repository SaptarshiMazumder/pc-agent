"""The paper-library plugin: composition root.

This file wires; it does not work. Every tool is one class in one file named after it, so
`grep LibraryLinksTool` finds the definition and every call site in one search, and the import
list below is the plugin's table of contents.

WHY THESE TOOLS ARE A PLUGIN AND NOT "THE AGENT JUST READS FILES". Three of them are called by the
APP as well as by the model — `library_index` fills the list on every open, `library_search` runs
on every keystroke, `library_links` draws the Connections view. Those are `tools.invoke` calls: no
chat turn, no model call, no tokens. Asking the model to enumerate a folder and reformat it would
cost a full turn and answer differently each time.

The dependencies are constructed here and injected, so a tool can be exercised against a temporary
database in a test without a daemon, a workspace, or a network.
"""

from __future__ import annotations

from document_chunker import DocumentChunker
from document_embedder import DocumentEmbedder
from document_put_tool import DocumentPutTool
from folder_browse_tool import FolderBrowseTool
from folder_scan_tool import FolderScanTool
from library_index_tool import LibraryIndexTool
from library_inventory_tool import LibraryInventoryTool
from library_links_tool import LibraryLinksTool
from library_note_store import LibraryNoteStore
from library_search_tool import LibrarySearchTool
from semantic_search_tool import SemanticSearchTool


def register(api, ctx):
    notes = LibraryNoteStore()
    chunker = DocumentChunker()
    # The embedder resolves its model from config at registration; it opens no connection and
    # makes no network call until something is actually embedded.
    embedder = DocumentEmbedder(getattr(ctx, "config", None))

    # The notes: what has been read and how it connects.
    api.register_tool(LibraryIndexTool(notes))
    api.register_tool(LibraryInventoryTool())
    api.register_tool(LibrarySearchTool(notes))
    api.register_tool(LibraryLinksTool(notes))

    # The documents: finding them, indexing them, and searching what they say.
    # `library_browse` exists for the APP — a sandboxed page has no native file
    # dialog, so the folder picker is built from a tool call instead.
    api.register_tool(FolderBrowseTool())
    api.register_tool(FolderScanTool())
    api.register_tool(DocumentPutTool(chunker, embedder))
    api.register_tool(SemanticSearchTool(embedder))

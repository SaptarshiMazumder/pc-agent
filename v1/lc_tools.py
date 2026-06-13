"""
LangChain tool wrappers for the LangGraph agent (lc_agent.py).

Each @tool delegates to tools.dispatch(), so the model-agnostic LangGraph path
reuses the exact same implementations, the risky-shell approval gate, AND the
per-tool watchdog (no tool can freeze the agent) as the Gemini custom framework.
"""
from langchain_core.tools import tool

import tools as base


def _log(msg: str):
    print(f"  · {msg}")


def _approve(desc: str) -> bool:
    print(f"\n🔧 Allow this?\n    {desc}")
    return input("   [y/N] ").strip().lower() in ("y", "yes")


def _call(name, args):
    return base.dispatch(name, args, _approve, _log)


@tool
def run_shell(command: str, shell: str = "powershell") -> str:
    """Run a command on the user's Windows PC and return combined stdout/stderr.
    Use to install software, run scripts, manage files, use git, query the system.
    shell is 'powershell' (default), 'bash', or 'cmd'. Non-interactive only."""
    return _call("run_shell", {"command": command, "shell": shell})


@tool
def read_file(path: str) -> str:
    """Read a PLAIN-TEXT file (code, .txt, .md, .json). For PDF/Word use read_document."""
    return _call("read_file", {"path": path})


@tool
def read_document(path: str) -> str:
    """Extract text from a PDF (.pdf), Word (.docx), or text document — CVs,
    resumes, reports, any non-plain-text document."""
    return _call("read_document", {"path": path})


@tool
def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file with the given content."""
    return _call("write_file", {"path": path, "content": content})


@tool
def list_dir(path: str = ".") -> str:
    """List the entries of a directory (default: current directory)."""
    return _call("list_dir", {"path": path})


@tool
def find_file(name: str) -> str:
    """Find files by name (case-insensitive substring) across common folders —
    Desktop, OneDrive Desktop, Documents, Downloads. Fast and bounded. ALWAYS use
    this to locate a file; never run recursive shell scans, which can hang."""
    return _call("find_file", {"name": name})


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web (Tavily) and return a short answer plus top results with
    titles, URLs, and snippets. Use for research and looking things up online."""
    return _call("web_search", {"query": query, "max_results": max_results})


@tool
def fetch_url(url: str) -> str:
    """Fetch ONE specific static page and return its readable text (sends real
    browser headers + strips boilerplate). On HTTP 403/401 the site blocks plain
    HTTP — open it with web_open instead. Not for JS-heavy sites or gathering a
    list."""
    return _call("fetch_url", {"url": url})


@tool
def gmail_recent(query: str = "newer_than:1d", max_results: int = 10) -> str:
    """List recent Gmail messages (read-only) matching a Gmail search query;
    returns sender, subject, date, snippet. Use 'is:unread' for unread. Requires
    Google OAuth (credentials.json)."""
    return _call("gmail_recent", {"query": query, "max_results": max_results})


@tool
def calendar_events(days: int = 1) -> str:
    """List Google Calendar events (read-only). Default: today; set 'days' to
    widen the window (e.g. 7 for the week). Requires Google OAuth."""
    return _call("calendar_events", {"days": days})


# --- web browser (agent-browser: CDP + accessibility @refs) ---
# Core loop: web_open -> web_snapshot (read @eN refs) -> act on a ref -> re-snapshot.
@tool
def web_open(url: str) -> str:
    """Open a URL and return a CONTENT snapshot: the page's readable TEXT (headings,
    paragraphs, list items — e.g. a full job description) PLUS interactive @eN refs
    and link URLs. START any web task here. To FIND/LIST many items, open the site's
    SEARCH-RESULTS URL (with query params) and read the whole list from this one
    snapshot — don't open results one link at a time."""
    return _call("web_open", {"url": url})


@tool
def web_snapshot() -> str:
    """Re-snapshot the CURRENT page: readable TEXT + @eN refs + link URLs. Refs go
    stale after the page changes — call this after every click/navigation before
    using a ref. Reading this snapshot is usually enough to extract data; you rarely
    need web_get_text."""
    return _call("web_snapshot", {})


@tool
def web_click(ref: str) -> str:
    """Click an element by its @eN ref (from the latest snapshot). Returns a fresh
    snapshot."""
    return _call("web_click", {"ref": ref})


@tool
def web_fill(ref: str, text: str) -> str:
    """Clear a field (@eN ref) and type text into it."""
    return _call("web_fill", {"ref": ref, "text": text})


@tool
def web_press(key: str) -> str:
    """Press a key at the current focus ('Enter', 'Tab', 'Control+a'). Returns a
    fresh snapshot."""
    return _call("web_press", {"key": key})


@tool
def web_get_text(ref: str) -> str:
    """Get the visible text of an element by its @eN ref."""
    return _call("web_get_text", {"ref": ref})


@tool
def web_scroll(direction: str, pixels: int = 0) -> str:
    """Scroll the page ('up'/'down'/'left'/'right'), optional pixel amount. Returns
    a fresh snapshot."""
    return _call("web_scroll", {"direction": direction, "pixels": pixels or None})


@tool
def web_login(url: str = "") -> str:
    """When web_open reports '[NOT LOGGED IN]' / a sign-in wall, call this to let
    the USER log in: it opens a VISIBLE browser at the login URL and waits for them
    to finish. The session saves to the persistent profile — then retry web_open
    and you'll be authenticated. Pass the site's login URL."""
    return _call("web_login", {"url": url})


@tool
def web_close() -> str:
    """Close the browser when the web task is done."""
    return _call("web_close", {})


@tool
def use_computer_visually(task: str) -> str:
    """LAST RESORT, ~10x slower. Visually control the screen (screenshots + real
    mouse/keyboard). Use ONLY for NATIVE DESKTOP apps (not websites) — for anything
    on the web use browse_web instead. (Uses the Gemini computer-use model; needs
    GEMINI_API_KEY.)"""
    return _call("use_computer_visually", {"task": task})


TOOLS = [run_shell, read_file, read_document, write_file, list_dir, find_file,
         web_search, fetch_url, gmail_recent, calendar_events,
         web_open, web_snapshot, web_click, web_fill, web_press, web_get_text,
         web_scroll, web_login, web_close, use_computer_visually]

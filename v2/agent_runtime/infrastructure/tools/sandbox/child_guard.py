"""The in-CHILD enforcement layer: an audit hook that turns a CapabilityGrant into refusals.

WHAT THIS IS. `sys.addaudithook` (3.8+) gets called by CPython before file opens, socket calls,
process spawns and ctypes calls. Raising from the hook makes the operation fail with a normal
`PermissionError`, which the plugin sees as an ordinary I/O error. Once added, a hook cannot be
removed — there is no API for it — so plugin code cannot politely uninstall it.

WHAT THIS IS NOT, stated plainly because the difference decides where you can deploy it: this is
not a kernel-enforced boundary. A plugin shipping a compiled extension module runs machine code
that never consults the interpreter's hook, and that is the escape. Denying `ctypes` closes the
easy version of it; it does not close the real one. The OS boundary is `sandbox_child_uid` (below)
or a container/microVM backend behind the same port.

WHAT IT DOES BUY, which is most of the practical value:

  * the child holds NO handles — no browser, no vault, no credential store, no task ledger. Those
    are `None` in its PluginContext, and `None` cannot be escaped from.
  * its environment carries no provider keys, so the cheapest exfiltration has nothing to read.
  * its config is a redacted projection, so the second-cheapest has nothing to read either.
  * every remaining path — reading another account's directory, opening a socket, spawning a
    helper — hits this hook first and is refused and COUNTED.

Denials are recorded (bounded) and travel back in the result, so `sandbox_denied_total{capability}`
is a real metric instead of a guess, and a plugin refused for a legitimate need is diagnosable
rather than mysteriously broken.

ONE OS-LEVEL OPTION, when the operator provides it. `sandbox_child_uid`/`sandbox_child_gid` drop
the child to an unprivileged user before `exec`. That IS kernel-enforced, and it is the difference
between "the interpreter declined" and "the kernel declined" — but it only works on POSIX, only
when the daemon runs as root, and only if the tenant workspace is writable by that uid. Off unless
configured, because a half-configured version fails every tool call with a confusing EACCES.
"""

from __future__ import annotations

import os
import sys
import sysconfig

#: Audit events that cost a check. Anything else returns immediately — the hook fires on VERY hot
#: paths (`os.stat` on every import), so the first line of the hook has to be a set lookup.
_FS_READ = frozenset(
    {"open", "os.listdir", "os.scandir", "shutil.copyfile", "shutil.copytree", "shutil.move"}
)
_FS_WRITE = frozenset(
    {
        "os.mkdir",
        "os.rmdir",
        "os.remove",
        "os.rename",
        "os.link",
        "os.symlink",
        "os.truncate",
        "os.chmod",
        "os.chown",
        "shutil.rmtree",
    }
)
_NET = frozenset({"socket.connect", "socket.bind", "socket.getaddrinfo", "urllib.Request"})
_SPAWN = frozenset(
    {"subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "pty.spawn"}
)
_NATIVE = frozenset({"ctypes.dlopen", "ctypes.dlsym", "ctypes.call_function"})
_POLICED = _FS_READ | _FS_WRITE | _NET | _SPAWN | _NATIVE

#: How many denials to remember. A plugin in a retry loop must not be able to grow this without
#: bound; the first few are what a human needs to see anyway.
MAX_DENIALS = 25


class SandboxDenied(PermissionError):
    """Raised INTO the plugin when it reaches for something its grant does not cover."""


def default_read_roots() -> list[str]:
    """Where the interpreter itself lives. The child must be able to import the stdlib, its
    site-packages, and our own package or it cannot run a tool at all — so these are read-allowed
    regardless of the grant. They are also the paths a plugin gains nothing from reading."""
    roots = [p for p in sys.path if p]
    for key in ("stdlib", "platstdlib", "purelib", "platlib", "data"):
        path = sysconfig.get_paths().get(key)
        if path:
            roots.append(path)
    prefix = getattr(sys, "base_prefix", "") or sys.prefix
    if prefix:
        roots.append(prefix)
    return roots


def _normalize(paths) -> list[str]:
    out = []
    for raw in paths:
        if not raw:
            continue
        try:
            out.append(os.path.realpath(str(raw)))
        except (OSError, ValueError):
            continue
    return out


def _under(path: str, roots: list[str]) -> bool:
    for root in roots:
        if path == root or path.startswith(root + os.sep):
            return True
    return False


def install(
    *,
    fs_paths=(),
    read_paths=(),
    net_allowlist=(),
    plugin_root: str = "",
    temp_dir: str = "",
    deny_paths=(),
    allow_native: bool = False,
    denials: list | None = None,
) -> list:
    """Add the audit hook. Returns the (mutable) denial list the hook appends to.

    Three tiers, checked in this order, and the order is the interesting part:

      1. GRANTED (the run's workspace, the temp dir) — read and write, always.
      2. DENIED (`deny_paths`) — refused even though tier 3 would allow them. This tier exists
         because tier 3 has to include the interpreter's own path roots or the child cannot import
         anything, and in a source checkout that root is the repository — which contains `.env`.
         The daemon knows exactly which paths hold its secrets and its other accounts' files, so it
         names them rather than leaving them covered by a rule meant for importing modules.
      3. READABLE (interpreter roots, the plugin's own folder, and the grant's `read_paths` — the
         agent folder this plugin shipped inside, holding the data it was authored beside) — read
         only. A plugin that could write into site-packages would own every later run in this
         process tree, and one that could rewrite its own agent folder would be editing the
         package its publisher signed.

    Tier 1 beats tier 2 deliberately: an account's workspace lives INSIDE the tenant root, which is
    denied wholesale. Without that precedence, the sandbox would deny the plugin its own files.
    A READ of tier 3's `read_paths` beats tier 2 on identical reasoning — a signed-in user's
    agents also live under the tenant root, and its package data is no less its own than its
    workspace. Writes never do: the package stays exactly as its publisher shipped it.
    """
    record: list = [] if denials is None else denials
    granted = _normalize(fs_paths)
    temp = _normalize([temp_dir]) if temp_dir else []
    denied = _normalize(deny_paths)
    readable = _normalize(list(read_paths))
    read_roots = (
        _normalize(default_read_roots() + ([plugin_root] if plugin_root else []))
        + readable
        + granted
        + temp
    )
    write_roots = granted + temp
    allowed_hosts = {str(h).strip().lower() for h in net_allowlist if str(h).strip()}
    # Reentrancy: the hook itself calls realpath, and a future policed event inside that call would
    # recurse forever. A flag is enough — the hook is called on the same thread that triggered it.
    state = {"busy": False}

    def deny(capability: str, target: str) -> None:
        if len(record) < MAX_DENIALS:
            record.append({"capability": capability, "target": target[:200]})
        raise SandboxDenied(
            f"sandbox: this plugin is not allowed to {capability} ({target[:200]}). "
            "Untrusted plugin code runs with filesystem access limited to the agent's workspace "
            "and no network."
        )

    def _path_of(args) -> str:
        for arg in args:
            if isinstance(arg, (str, bytes, os.PathLike)):
                try:
                    return os.fspath(arg) if not isinstance(arg, bytes) else arg.decode(errors="replace")
                except (TypeError, ValueError):
                    continue
        return ""

    def hook(event: str, args) -> None:
        if event not in _POLICED or state["busy"]:
            return
        state["busy"] = True
        try:
            if event in _NATIVE:
                if not allow_native:
                    deny("load native code", str(args[0] if args else ""))
                return
            if event in _SPAWN:
                deny("start another process", str(args[0] if args else ""))
                return
            if event in _NET:
                if not allowed_hosts:
                    deny("use the network", _net_target(event, args))
                    return
                host = _net_host(event, args)
                # An allowlisted host is matched by NAME. `socket.connect` sees an address that has
                # already been resolved, so it cannot be matched back to a name reliably — the
                # allowlist is enforced at resolution time and connect is then permitted. Honest
                # limitation: the strong case is the DEFAULT one, an empty allowlist denying all.
                if event == "socket.getaddrinfo" and host and not _host_allowed(host, allowed_hosts):
                    deny("reach that host", host)
                return
            path = _path_of(args)
            if not path:
                return
            try:
                real = os.path.realpath(path)
            except (OSError, ValueError):
                real = path
            if event == "open":
                # `open`'s third audit arg is the flags int; anything but a pure read needs write
                # rights. Checked through the FLAGS rather than the mode string because the event
                # fires for os.open too, which has no mode string.
                flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
                writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC))
                _check(real, writing)
                return
            _check(real, event in _FS_WRITE)
        finally:
            state["busy"] = False

    def _check(real: str, writing: bool) -> None:
        verb = "write there" if writing else "read that path"
        if _under(real, granted) or _under(real, temp):
            return
        # A READ of the plugin's own shipped package beats the deny tier, for the same reason
        # tier 1 does: on a hosted daemon an account's agents live INSIDE the tenant root, which
        # is denied wholesale, so without this a plugin installed by a signed-in user would be
        # refused its own templates — while the identical package on a desktop worked.
        if not writing and _under(real, readable):
            return
        if _under(real, denied):
            deny(verb, real)
        if not _under(real, write_roots if writing else read_roots):
            deny(verb, real)

    sys.addaudithook(hook)
    return record


def _net_host(event: str, args) -> str:
    if event == "socket.getaddrinfo" and args:
        host = args[0]
        if isinstance(host, bytes):
            host = host.decode(errors="replace")
        return str(host or "").lower()
    return ""


def _net_target(event: str, args) -> str:
    return f"{event}: {args[1] if len(args) > 1 else (args[0] if args else '')}"[:200]


def _host_allowed(host: str, allowed: set[str]) -> bool:
    for pattern in allowed:
        pattern = pattern.split(":", 1)[0]
        if pattern.startswith("*."):
            if host == pattern[2:] or host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


def apply_rlimits(cpu_ms: int = 0, mem_mb: int = 0) -> None:
    """POSIX resource ceilings, applied in the child between fork and exec.

    No-op on Windows, which has no `resource` module: the wall-clock timeout in the parent is the
    only limit there. Worth saying out loud rather than leaving as an inference — a memory bomb in
    a plugin is survivable on Linux and is not on the desktop.
    """
    try:
        import resource
    except ImportError:
        return
    if cpu_ms > 0:
        seconds = max(1, int(cpu_ms / 1000))
        _set(resource.RLIMIT_CPU, seconds)
    if mem_mb > 0:
        _set(getattr(resource, "RLIMIT_AS", None), mem_mb * 1024 * 1024)


def _set(which, value) -> None:
    if which is None:
        return
    import resource

    try:
        soft, hard = resource.getrlimit(which)
        ceiling = value if hard in (resource.RLIM_INFINITY, -1) else min(value, hard)
        resource.setrlimit(which, (ceiling, hard))
    except (ValueError, OSError):
        pass  # a limit we cannot set is not a reason to fail the call

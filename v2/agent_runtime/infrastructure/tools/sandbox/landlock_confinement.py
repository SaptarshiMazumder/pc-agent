"""LandlockConfinement — lock the CURRENT process, and everything it later runs, to named folders.

WHY LANDLOCK AND NOT A CONTAINER TRICK. A hosted daemon's background shell runs on the box that
holds every account's files, so a command must not be able to open anything outside its own
account. The obvious tool, a namespace sandbox (bubblewrap, `unshare`), is refused inside our
ECS container: the runtime's default seccomp filter blocks creating a user namespace, and the
fix for that is granting the container CAP_SYS_ADMIN — widening what the daemon may do in order
to narrow what its children may. Landlock needs no privilege at all: an unprivileged process
restricts ITSELF, the restriction is inherited by every child and survives exec, and it can
only ever be tightened. Measured on the staging daemon: ABI 2, allowed by the default filter.

AN ALLOWLIST, NOT A DENYLIST. Every filesystem right is "handled", so anything not granted by a
rule is refused — including paths nobody thought to list. The failure mode of forgetting a path
is a command that cannot open something, never one that can open too much.

IT CANNOT BE UNDONE BY THE PROCESS IT CONFINES, which is what makes it safe to apply just before
exec: `no_new_privs` is set first (Landlock requires it), so no setuid binary can hand the
command back what the rules took away.

LINUX ONLY. Any other platform, or a kernel without Landlock, raises LandlockUnavailable — the
caller refuses the command rather than running it unconfined. There is deliberately no fallback:
a lock that silently does nothing is worse than no lock, because everything above it believes
the boundary holds.
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Iterable

# Syscall numbers are shared by x86_64 and aarch64 (the generic syscall table).
_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT_SELF = 446

_CREATE_RULESET_VERSION = 1 << 0
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

# Filesystem rights (uapi/linux/landlock.h). REFER exists from ABI 2.
_EXECUTE = 1 << 0
_WRITE_FILE = 1 << 1
_READ_FILE = 1 << 2
_READ_DIR = 1 << 3
_REMOVE_DIR = 1 << 4
_REMOVE_FILE = 1 << 5
_MAKE_CHAR = 1 << 6
_MAKE_DIR = 1 << 7
_MAKE_REG = 1 << 8
_MAKE_SOCK = 1 << 9
_MAKE_FIFO = 1 << 10
_MAKE_BLOCK = 1 << 11
_MAKE_SYM = 1 << 12
_REFER = 1 << 13

_READ_ONLY = _EXECUTE | _READ_FILE | _READ_DIR
# Everything a working directory needs. Device nodes are left out: nothing a user's command does
# in its own folder needs to create one.
_READ_WRITE = (
    _READ_ONLY | _WRITE_FILE | _REMOVE_DIR | _REMOVE_FILE | _MAKE_DIR | _MAKE_REG
    | _MAKE_SOCK | _MAKE_FIFO | _MAKE_SYM
)


class LandlockUnavailable(RuntimeError):
    """This host cannot confine a process with Landlock. Never swallowed — see the module doc."""


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    # Packed in the kernel header: a u64 followed by an s32, 12 bytes, no tail padding.
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


class LandlockConfinement:
    """Grants read-only and read-write access to named folders; everything else is refused.

    Construct it with the folders, then `apply()` in the process to be confined — which is the
    launched child, never the daemon itself.
    """

    def __init__(self, read_only: Iterable[str], read_write: Iterable[str]) -> None:
        self._read_only = [p for p in read_only if p]
        self._read_write = [p for p in read_write if p]

    @staticmethod
    def abi_version() -> int:
        """The kernel's Landlock ABI, or raises LandlockUnavailable."""
        if not sys.platform.startswith("linux"):
            raise LandlockUnavailable(f"Landlock is Linux-only (this is {sys.platform})")
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        version = libc.syscall(_SYS_CREATE_RULESET, None, 0, _CREATE_RULESET_VERSION)
        if version < 1:
            err = ctypes.get_errno()
            raise LandlockUnavailable(f"this kernel has no usable Landlock (errno {err})")
        return int(version)

    def apply(self) -> None:
        """Confine this process and all its descendants. Irreversible by design."""
        abi = self.abi_version()
        handled = _READ_WRITE | _MAKE_CHAR | _MAKE_BLOCK
        # REFER (moving a file between directories) only exists from ABI 2. On ABI 1 it is not
        # handled, which the kernel then refuses outright across directories — stricter, not
        # looser, so an old kernel degrades safely.
        if abi >= 2:
            handled |= _REFER
        rw_rights = _READ_WRITE | (_REFER if abi >= 2 else 0)

        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        attr = _RulesetAttr(handled_access_fs=handled)
        ruleset = libc.syscall(_SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0)
        if ruleset < 0:
            raise LandlockUnavailable(f"landlock_create_ruleset failed (errno {ctypes.get_errno()})")
        try:
            for path in self._read_only:
                self._allow(libc, ruleset, path, _READ_ONLY)
            for path in self._read_write:
                self._allow(libc, ruleset, path, rw_rights)
            if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
                raise LandlockUnavailable(f"prctl(NO_NEW_PRIVS) failed (errno {ctypes.get_errno()})")
            if libc.syscall(_SYS_RESTRICT_SELF, ruleset, 0) != 0:
                raise LandlockUnavailable(
                    f"landlock_restrict_self failed (errno {ctypes.get_errno()})"
                )
        finally:
            os.close(ruleset)

    @staticmethod
    def _allow(libc, ruleset: int, path: str, rights: int) -> None:
        """One rule. A path that does not exist is skipped — /lib64 is absent on some images,
        and a missing system folder is not a reason to refuse the whole command. A path that
        EXISTS but cannot be ruled is an error: that is the boundary failing to form."""
        if not os.path.exists(path):
            return
        # A rule on a FILE may only carry file rights; directory-only rights on it are EINVAL.
        if not os.path.isdir(path):
            rights &= _EXECUTE | _WRITE_FILE | _READ_FILE
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = _PathBeneathAttr(allowed_access=rights, parent_fd=fd)
            if libc.syscall(_SYS_ADD_RULE, ruleset, _RULE_PATH_BENEATH, ctypes.byref(rule), 0) != 0:
                raise LandlockUnavailable(
                    f"landlock_add_rule failed for {path} (errno {ctypes.get_errno()})"
                )
        finally:
            os.close(fd)


__all__ = ["LandlockConfinement", "LandlockUnavailable"]

"""Where models should live on a person's own Vast machine: a volume, if one is attached.

Stdlib only — it is shipped to the machine and runs there (GpuCommandBundle).

VAST'S OWN RULE (vast-ai/base-image, base.md): only a mounted host VOLUME survives the machine
being recycled or destroyed; `/workspace` is where one is mounted IF the machine has one, and
otherwise it is ordinary container disk. So there are three answers:

  workspace_volume  `/workspace` itself is a volume — ComfyUI already lives on it; nothing to do
  volume            a volume is mounted elsewhere (a network volume at /data, say) — models go
                    to <volume>/ComfyUI/models, and ComfyUI is told to read them there
  disk              no volume — the machine's own disk, lost when it is destroyed

A volume is a mount on a different device from `/`, that is a writable directory, and is not one
of the system's own mounts (proc, cgroups, the GPU driver's files, /dev/shm…).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

WORKSPACE_VOLUME = "workspace_volume"
VOLUME = "volume"
DISK = "disk"

_SYSTEM_FS = {
    "proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "mqueue", "cgroup", "cgroup2", "overlay",
    "securityfs", "debugfs", "tracefs", "configfs", "fusectl", "nsfs", "autofs", "binfmt_misc",
    "pstore", "bpf", "hugetlbfs", "rpc_pipefs", "ramfs", "squashfs", "shm",
}
_SYSTEM_DIRS = ("/proc", "/sys", "/dev", "/run", "/etc", "/usr", "/boot", "/tmp", "/var",
                "/opt", "/root", "/bin", "/sbin", "/lib", "/lib64", "/snap", "/venv")


class ModelStorageDetector:
    def __init__(self, workspace: Path, *, mounts: str | None = None, stat=os.stat,
                 disk_usage=shutil.disk_usage, writable=lambda p: os.access(p, os.W_OK),
                 is_dir=os.path.isdir) -> None:
        self._workspace = workspace
        self._mounts = mounts
        self._stat = stat
        self._disk_usage = disk_usage
        self._writable = writable
        self._is_dir = is_dir

    def detect(self) -> dict:
        """{kind, path}: `path` is the volume's mount point ("" for disk)."""
        root_dev = self._stat("/").st_dev
        try:
            if self._stat(str(self._workspace)).st_dev != root_dev:
                return {"kind": WORKSPACE_VOLUME, "path": str(self._workspace)}
        except OSError:
            pass
        best, best_free = "", -1
        for point in self._candidates():
            try:
                if self._stat(point).st_dev == root_dev or not self._writable(point):
                    continue
                free = self._disk_usage(point).free
            except OSError:
                continue
            if free > best_free:
                best, best_free = point, free
        return {"kind": VOLUME, "path": best} if best else {"kind": DISK, "path": ""}

    def _candidates(self) -> list[str]:
        text = self._mounts if self._mounts is not None else Path("/proc/mounts").read_text()
        points = []
        for line in text.splitlines():
            fields = line.split()
            if len(fields) < 3:
                continue
            point = fields[1].replace("\\040", " ")
            if fields[2] in _SYSTEM_FS or point == "/" or point == str(self._workspace):
                continue
            if any(point == d or point.startswith(d + "/") for d in _SYSTEM_DIRS):
                continue
            if self._is_dir(point):
                points.append(point)
        return points

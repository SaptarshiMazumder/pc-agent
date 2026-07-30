# Assemble the EMBEDDED DAEMON RUNTIME the installers bundle (D5 in the plan):
# a python-build-standalone CPython with the agentd wheel installed straight into
# its OWN site-packages. NO venv, on purpose: a venv records the ABSOLUTE path of its
# base interpreter at creation time (pyvenv.cfg) and is NOT relocatable -- a venv built
# on the CI runner points at a D:\a\...\cpython that does not exist on the user's
# machine, so the bundled daemon can't start. A python-build-standalone `install_only`
# build IS relocatable and keeps pip alive, so marketplace pip-plugins can still
# install into it at runtime.
#
#   powershell -File scripts/build-runtime.ps1            # build runtime/cpython
#   powershell -File scripts/build-runtime.ps1 -Wheel ..\..\dist\agentd-0.1.0-py3-none-any.whl
#
# Output: clients/desktop/runtime/cpython/ (referenced by both electron-builder configs).

param(
    [string]$Wheel = "",
    [string]$PythonVersion = "3.11.11",
    [string]$PbsRelease = "20250115"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot          # clients/desktop
$V2 = (Resolve-Path (Join-Path $Root "..\..")).Path
$RuntimeDir = Join-Path $Root "runtime"
$CpythonDir = Join-Path $RuntimeDir "cpython"
$Python = Join-Path $CpythonDir "python.exe"

# 1. the wheel (build it from v2/ when not supplied)
if (-not $Wheel) {
    Write-Host "building the agentd wheel from $V2 ..."
    Push-Location $V2
    python -m pip install --quiet build
    python -m build --wheel --outdir (Join-Path $V2 "dist")
    Pop-Location
    $Wheel = (Get-ChildItem (Join-Path $V2 "dist") -Filter "agentd-*.whl" |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
Write-Host "wheel: $Wheel"

# 2. python-build-standalone (cached under runtime/cpython) -- the RELOCATABLE interpreter
if (-not (Test-Path $Python)) {
    New-Item -ItemType Directory -Force $RuntimeDir | Out-Null
    $archive = "cpython-$PythonVersion+$PbsRelease-x86_64-pc-windows-msvc-install_only.tar.gz"
    $url = "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsRelease/$archive"
    $tarPath = Join-Path $RuntimeDir $archive
    Write-Host "downloading $url ..."
    Invoke-WebRequest -Uri $url -OutFile $tarPath
    # Use Windows' bundled bsdtar (System32) explicitly: GNU tar (e.g. Git Bash's on
    # PATH) misparses a C:\... path as a remote "host:path" spec ("Cannot connect to
    # C:") and the extraction silently produces nothing. bsdtar handles Windows paths.
    $tarExe = Join-Path $env:SystemRoot "System32\tar.exe"
    if (-not (Test-Path $tarExe)) { $tarExe = "tar" }
    & $tarExe -xzf $tarPath -C $RuntimeDir
    Rename-Item (Join-Path $RuntimeDir "python") $CpythonDir
    Remove-Item $tarPath
}

# 3. install agentd (+ the mcp extra) straight INTO the standalone interpreter's
#    site-packages. --force-reinstall so a re-run picks up a rebuilt wheel of the
#    same version (the cpython dir is cached across runs).
& $Python -m pip install --quiet --upgrade pip
& $Python -m pip install --quiet --force-reinstall "$Wheel"
& $Python -m pip install --quiet "mcp>=1.0"

# 4. smoke: the embedded runtime must import + report its version
$version = & $Python -c "import agent_runtime; print(agentd.__version__)"
Write-Host "runtime ready: agentd $version at $CpythonDir"
Write-Host "next: npm run dist:core  (or dist:studio)"

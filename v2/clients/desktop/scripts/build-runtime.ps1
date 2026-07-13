# Assemble the EMBEDDED DAEMON RUNTIME the installers bundle (D5 in the plan):
# a python-build-standalone CPython + a real venv with the agentd wheel installed.
# A venv (not PyInstaller) on purpose: marketplace pip-plugins install into it at
# runtime; a frozen exe can't do that.
#
#   powershell -File scripts/build-runtime.ps1            # build runtime/agentd-env
#   powershell -File scripts/build-runtime.ps1 -Wheel ..\..\dist\agentd-0.1.0-py3-none-any.whl
#
# Output: clients/desktop/runtime/agentd-env/ (referenced by both electron-builder configs).

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
$EnvDir = Join-Path $RuntimeDir "agentd-env"

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

# 2. python-build-standalone (cached under runtime/cpython)
if (-not (Test-Path (Join-Path $CpythonDir "python.exe"))) {
    New-Item -ItemType Directory -Force $RuntimeDir | Out-Null
    $archive = "cpython-$PythonVersion+$PbsRelease-x86_64-pc-windows-msvc-install_only.tar.gz"
    $url = "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsRelease/$archive"
    $tarPath = Join-Path $RuntimeDir $archive
    Write-Host "downloading $url ..."
    Invoke-WebRequest -Uri $url -OutFile $tarPath
    tar -xzf $tarPath -C $RuntimeDir
    Rename-Item (Join-Path $RuntimeDir "python") $CpythonDir
    Remove-Item $tarPath
}

# 3. a REAL venv with the wheel (+ the mcp extra; heavy optionals stay out)
if (Test-Path $EnvDir) { Remove-Item -Recurse -Force $EnvDir }
& (Join-Path $CpythonDir "python.exe") -m venv $EnvDir
& (Join-Path $EnvDir "Scripts\python.exe") -m pip install --quiet --upgrade pip
& (Join-Path $EnvDir "Scripts\python.exe") -m pip install --quiet "$Wheel"
& (Join-Path $EnvDir "Scripts\python.exe") -m pip install --quiet "mcp>=1.0"

# 4. smoke: the embedded runtime must import + report its version
$version = & (Join-Path $EnvDir "Scripts\python.exe") -c "import agentd; print(agentd.__version__)"
Write-Host "runtime ready: agentd $version at $EnvDir"
Write-Host "next: npm run dist:core  (or dist:studio)"

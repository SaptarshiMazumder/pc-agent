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
    [string]$PbsRelease = "20250115",
    [string]$NodeVersion = "20.18.1"
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
#
#    The extra is requested BY NAME off the wheel — never by re-typing its contents here.
#    This line used to read `pip install "mcp>=1.0"`, a hand-copied duplicate of the extra,
#    and it silently drifted: pyproject's `mcp` extra gained the `uv` launcher, the Docker
#    image installed it, and the desktop runtime kept installing only the client SDK. Result:
#    every stdio MCP plugin (Gmail, Drive, Calendar) worked in the container and died in the
#    shipped exe. One source of truth, so that cannot happen again.
& $Python -m pip install --quiet --upgrade pip
& $Python -m pip install --quiet --force-reinstall "${Wheel}[mcp]"

# 3b. CHROMIUM, into the bundle. The playwright PACKAGE is a wheel dependency; the BROWSER is a
#     separate ~170MB download that pip never fetches, so a packaged install had python-side
#     playwright and no browser to drive. Every tool that opens a page -- the shared `browser`
#     tool, and agent-builder's verify_app -- failed on a fresh machine with "Executable doesn't
#     exist", and the only fix was a command line the product never mentions.
#
#     PLAYWRIGHT_BROWSERS_PATH puts it inside the runtime tree instead of the builder's user
#     cache, so it is a thing we SHIP rather than a thing that happened to be on the build agent.
#     supervisor.ts points the spawned daemon at the same folder.
$BrowsersDir = Join-Path $RuntimeDir "ms-playwright"
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowsersDir
Write-Host "installing chromium into $BrowsersDir (a few hundred MB, cached across runs) ..."
& $Python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "playwright could not download chromium. The installer would ship a browser-less runtime - see the error above."
}
# The download is silent about a partial result, so assert the thing itself is there. A build
# that ships no browser must fail HERE, not on a user's machine three steps into an agent.
if (-not (Get-ChildItem $BrowsersDir -Directory -Filter "chromium*" -ErrorAction SilentlyContinue)) {
    throw "no chromium under $BrowsersDir after `playwright install chromium`. Refusing to package a runtime whose browser tools cannot start."
}

# 3c. NODE, into the bundle. An agent's window is a React project: SOURCE in app/, built output
#     in ui/, and the daemon serves only the latter. Turning one into the other is `npm run build`
#     -- so without a Node here, a user who installed the product and built an agent through Agent
#     Builder could not rebuild that agent's own window. They would edit app/src, see nothing
#     change, and have no way to find out why: the missing piece is a toolchain the product never
#     mentions and they never agreed to install.
#
#     Same argument as chromium above, and the same answer: SHIP it rather than hope the build
#     agent had it. npm arrives inside the Node distribution, so this is both.
$NodeDir = Join-Path $RuntimeDir "node"
$NodeExe = Join-Path $NodeDir "node.exe"
if (-not (Test-Path $NodeExe)) {
    $nodeZip = "node-v$NodeVersion-win-x64.zip"
    $nodeUrl = "https://nodejs.org/dist/v$NodeVersion/$nodeZip"
    $nodeZipPath = Join-Path $RuntimeDir $nodeZip
    Write-Host "downloading $nodeUrl ..."
    Invoke-WebRequest -Uri $nodeUrl -OutFile $nodeZipPath
    Expand-Archive -Path $nodeZipPath -DestinationPath $RuntimeDir -Force
    Rename-Item (Join-Path $RuntimeDir "node-v$NodeVersion-win-x64") $NodeDir
    Remove-Item $nodeZipPath
}
# Assert BOTH halves. The archive carries npm as a plain script under node_modules, and a
# half-extracted tree that still has node.exe would ship a runtime that can run javascript and
# cannot build anything -- which fails on a user's machine, several steps into an agent.
$NpmCli = Join-Path $NodeDir "node_modules\npm\bin\npm-cli.js"
if (-not (Test-Path $NodeExe)) {
    throw "no node.exe under $NodeDir. Refusing to package a runtime that cannot build an agent's window."
}
if (-not (Test-Path $NpmCli)) {
    throw "no npm under $NodeDir. Refusing to package a runtime that cannot build an agent's window."
}
$nodeReported = & $NodeExe --version
if ($LASTEXITCODE -ne 0 -or -not $nodeReported) {
    throw "the bundled node cannot run - see the error above."
}

# 3d. THE SHARED APP DEPENDENCIES -- react, vite and the rest, installed ONCE for the product
#     instead of once per agent.
#
#     WHY NOT `npm install` PER AGENT. Every agent app is scaffolded from the same starter and so
#     declares the same seven packages. Installing them per agent would download a few hundred MB
#     from the network EVERY TIME a user creates an agent, take a minute each, cost that much disk
#     per agent, and fail outright on a machine with no internet. None of that buys anything: the
#     dependency list is ours, not the agent author's.
#
#     THE LIST IS COPIED FROM THE STARTER, never retyped here. A second hand-maintained copy of a
#     dependency list is the same drift this build script already got bitten by once (the `mcp`
#     extra, above) -- so the starter's package.json IS the input, and adding a dependency there
#     is the whole change.
#
#     Installed with the bundled node so the store is built by the same toolchain that will read
#     it, rather than by whatever happened to be on the build agent's PATH.
$AppDepsDir = Join-Path $RuntimeDir "app-deps"
$StarterPkg = Join-Path $V2 "agents\agent-builder\skills\build-agent\templates\_borrowed\react\package.json"
if (-not (Test-Path $StarterPkg)) {
    throw "no React starter package.json at $StarterPkg - cannot work out what an agent app needs."
}
New-Item -ItemType Directory -Force $AppDepsDir | Out-Null
Copy-Item $StarterPkg (Join-Path $AppDepsDir "package.json") -Force
Write-Host "installing the shared agent-app dependencies into $AppDepsDir ..."
Push-Location $AppDepsDir
# ALWAYS, not only when the folder is missing. The starter's dependency list changes over time and
# npm reconciles an existing tree cheaply, so re-running is what keeps a cached runtime honest.
& $NodeExe $NpmCli install --no-audit --no-fund --loglevel=error
$npmExit = $LASTEXITCODE
Pop-Location
if ($npmExit -ne 0) {
    throw "npm could not install the shared agent-app dependencies - see the error above."
}
# Assert the BUILDER specifically. `node_modules` exists after a failed install too, and vite is
# the package every agent's `npm run build` actually invokes.
if (-not (Test-Path (Join-Path $AppDepsDir "node_modules\vite"))) {
    throw "no vite under $AppDepsDir\node_modules. Refusing to package a product that cannot build an agent's window."
}

# 4. smoke: the embedded runtime must import + report its version. The check IS the point --
#    a runtime that cannot import is exactly what once shipped as a dead daemon -- so a failure
#    here STOPS the build instead of printing a traceback and carrying on. (It read
#    `print(agentd.__version__)` after the package rename, which raises NameError every time:
#    the smoke test had been silently failing while the script still said "runtime ready".)
#    ASCII ONLY in this file: powershell.exe reads .ps1 as the ANSI codepage unless there is a
#    BOM, and a UTF-8 em dash decodes to a byte PowerShell treats as a closing smart quote --
#    which ends the string early and breaks the parse.
$version = & $Python -c "import agent_runtime; print(agent_runtime.__version__)"
if ($LASTEXITCODE -ne 0 -or -not $version) {
    throw "the embedded runtime cannot import agent_runtime. Refusing to call it ready - see the error above."
}
$browserSize = [math]::Round((Get-ChildItem $BrowsersDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB)
Write-Host "runtime ready: agentd $version at $CpythonDir"
Write-Host "chromium bundled: $BrowsersDir ($browserSize MB)"
Write-Host "node bundled: $NodeDir ($nodeReported)"
$depsSize = [math]::Round((Get-ChildItem $AppDepsDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB)
Write-Host "agent-app deps bundled: $AppDepsDir ($depsSize MB, shared by every agent)"
Write-Host "next: npm run dist:core  (or dist:studio)"

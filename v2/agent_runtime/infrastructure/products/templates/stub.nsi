; stub.nsi — the per-agent installer. Everything a stranger runs to get one agent as an app.
;
; ~200 KB instead of ~250 MB, because it ships the agent and NOT the runtime. On a machine with no
; agentd it fetches the shared ENGINE once; every later agent installs in seconds and adds nothing
; but its own payload. Sizes aside, that split is what makes ONE code-signing certificate cover
; every agent in the marketplace: reputation attaches to the certificate and the binary, and there
; is one binary to sign instead of one per author.
;
; NOTHING IS HARDCODED IN HERE. Every value arrives as a /D define from
; infrastructure/products/nsis_stub_builder.py, which gets them from the ProductSpec and the
; EngineRef. There is no fallback url, no default product name, and no baked digest: a stub that
; "worked" with placeholder values would only fail on a stranger's machine.
;
; WHAT IT DOES, in order:
;   1. find the engine (HKCU, then HKLM)
;   2. missing, or older than this payload requires -> download it, VERIFY THE SHA256, run it /S
;   3. write the payload (distribution.toml + bundles/*.agentpkg + icon) into a per-user dir
;   4. shortcuts that run  <Engine>.exe --app-dir <payload>
;   5. its own Add/Remove Programs entry and uninstaller
;
; Uninstalling removes the payload, the shortcuts and the ARP entry. It NEVER removes the engine
; and never touches another product's payload: engines are shared, so an uninstall that took one
; away would break every other agent on the machine.

Unicode true
ManifestDPIAware true

;--------------------------------------------------------------------------- required defines
!ifndef PRODUCT_ID
  !error "stub.nsi: -DPRODUCT_ID is required"
!endif
!ifndef PRODUCT_NAME
  !error "stub.nsi: -DPRODUCT_NAME is required"
!endif
!ifndef PRODUCT_VERSION
  !error "stub.nsi: -DPRODUCT_VERSION is required"
!endif
!ifndef PAYLOAD_DIR
  !error "stub.nsi: -DPAYLOAD_DIR is required"
!endif
!ifndef ENGINE_URL
  !error "stub.nsi: -DENGINE_URL is required"
!endif
!ifndef ENGINE_SHA256
  !error "stub.nsi: -DENGINE_SHA256 is required (a stub must verify what it runs)"
!endif
!ifndef OUT_FILE
  !error "stub.nsi: -DOUT_FILE is required"
!endif
!ifndef AGENT_ID
  !error "stub.nsi: -DAGENT_ID is required"
!endif
!ifndef PRODUCT_PUBLISHER
  !define PRODUCT_PUBLISHER ""   ; unset is honest: most authors have no publisher name yet
!endif

!define ENGINE_KEY "Software\agentd\Engine"
!define ARP_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_ID}"

Name "${PRODUCT_NAME}"
OutFile "${OUT_FILE}"
; PER-USER. The engine installs per-user too, and its plugin runtime has to be able to write
; itself — a per-machine install would need admin on every plugin install.
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\agentd\apps\${PRODUCT_ID}"
ShowInstDetails show
ShowUnInstDetails show
SetCompressor /SOLID lzma

VIProductVersion "0.0.0.0"
VIAddVersionKey "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey "ProductVersion" "${PRODUCT_VERSION}"
VIAddVersionKey "FileDescription" "${PRODUCT_NAME} installer"
VIAddVersionKey "FileVersion" "${PRODUCT_VERSION}"
VIAddVersionKey "LegalCopyright" ""

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!ifdef ICON_FILE
  !define MUI_ICON "${ICON_FILE}"
  !define MUI_UNICON "${ICON_FILE}"
!endif
!define MUI_ABORTWARNING
; "Open <product>" on the last page. Via a FUNCTION rather than MUI_FINISHPAGE_RUN +
; MUI_FINISHPAGE_RUN_PARAMETERS: MUI's built-in launcher calls `Exec` with one argument, so a
; target plus parameters is a compile error ("Exec expects 1 parameters, got 2"). The function
; launches the SHORTCUT that was just created, which also makes this the first real test of it.
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchProduct
!define MUI_FINISHPAGE_RUN_TEXT "Open ${PRODUCT_NAME}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Var EngineExe
Var EngineVersion

Function LaunchProduct
  SetShellVarContext current
  ExecShell "" "$SMPROGRAMS\${PRODUCT_NAME}.lnk"
FunctionEnd

;--------------------------------------------------------------------------- helpers

; -> $EngineExe / $EngineVersion, both "" when no engine is installed.
; HKCU first because that is where a per-user engine registers itself; HKLM is checked so an
; enterprise per-machine install is found too rather than being installed over.
Function FindEngine
  StrCpy $EngineExe ""
  StrCpy $EngineVersion ""
  ReadRegStr $EngineExe HKCU "${ENGINE_KEY}" "Exe"
  ReadRegStr $EngineVersion HKCU "${ENGINE_KEY}" "Version"
  ${If} $EngineExe == ""
    ReadRegStr $EngineExe HKLM "${ENGINE_KEY}" "Exe"
    ReadRegStr $EngineVersion HKLM "${ENGINE_KEY}" "Version"
  ${EndIf}
  ; A registry key outliving its files is the common broken state (a half-removed install), and it
  ; is exactly the case where doing nothing produces a shortcut to a program that is not there.
  ${If} $EngineExe != ""
  ${AndIfNot} ${FileExists} "$EngineExe"
    DetailPrint "The recorded engine is gone ($EngineExe) — reinstalling it."
    StrCpy $EngineExe ""
    StrCpy $EngineVersion ""
  ${EndIf}
FunctionEnd

; $1 = file, $2 = expected lowercase hex sha256  ->  $3 = "ok" | a reason on failure.
;
; Get-FileHash rather than an NSIS crypto plugin: PowerShell is on every supported Windows, and a
; bundled plugin would be one more binary to trust inside the check that exists to establish trust.
;
; ON THE QUOTING, because getting it wrong here produces an installer that builds cleanly and then
; skips verification at runtime: the outer string is NSIS double-quoted, `$\"` emits the literal
; quote that wraps PowerShell's -Command argument, and PowerShell's own strings inside it are
; SINGLE-quoted. That way no quote is ever nested inside a quote of the same kind.
Function VerifySha256
  StrCpy $3 "unverified"
  nsExec::ExecToStack "powershell -NoProfile -ExecutionPolicy Bypass -Command $\"try { (Get-FileHash -LiteralPath '$1' -Algorithm SHA256).Hash.ToLower() } catch { '' }$\""
  Pop $4   ; exit code
  Pop $5   ; stdout
  ${If} $4 != 0
    StrCpy $3 "could not run Get-FileHash (exit $4)"
    Return
  ${EndIf}
  StrCpy $6 $5 64   ; a sha256 is 64 hex chars; this also drops the trailing CR/LF
  ${If} $6 == $2
    StrCpy $3 "ok"
  ${Else}
    StrCpy $3 "digest mismatch (expected $2, got $6)"
  ${EndIf}
FunctionEnd

; Download ${ENGINE_URL} to $1, WITH A PROGRESS BAR when the compiling NSIS ships the inetc
; plugin (the platform's build image installs it, digest-pinned — see services/publish/Dockerfile).
; curl is one opaque blocking instruction, so for the minutes a 250 MB download takes the page's
; bar sits at zero and the installer reads as hung; inetc reports transfer progress live.
;
; GUARDED AT COMPILE TIME, because the plugin is a property of whichever NSIS is compiling: a
; local `agentd product build` uses electron-builder's cached NSIS, which does not carry inetc,
; and an unguarded plugin call fails that compile outright. Present -> progress; absent -> the
; curl chain below, same behaviour as before. Both paths land on the same file and the same
; sha256 gate, so trust never depends on which downloader ran.
;
; curl.exe ships with Windows 10 1803+ and handles redirects and large files well; PowerShell is
; the fallback for older builds.
!if /FileExists "${NSISDIR}\Plugins\x86-unicode\INetC.dll"
  !define HAVE_INETC
!endif
Function DownloadEngine
  DetailPrint "Downloading the agentd engine — this happens once; every later agent installs in seconds."
  !ifdef HAVE_INETC
    ; /CAPTION names the dialog; /QUESTION guards the cancel button (an aborted download is a
    ; missing file, which EnsureEngine already refuses on); /END terminates inetc's arg list.
    inetc::get /CAPTION "Downloading the ${PRODUCT_NAME} runtime" \
      /QUESTION "Stop downloading? ${PRODUCT_NAME} cannot be installed without it." \
      "${ENGINE_URL}" "$1" /END
    Pop $4   ; "OK" or a reason
    ${If} $4 == "OK"
      Return
    ${EndIf}
    DetailPrint "download failed ($4) — retrying with curl."
  !endif
  nsExec::ExecToLog "curl.exe -fLsS --retry 3 --retry-delay 2 -o $\"$1$\" $\"${ENGINE_URL}$\""
  Pop $4
  ${If} $4 != 0
    DetailPrint "curl unavailable or failed (exit $4) — retrying with PowerShell."
    nsExec::ExecToLog "powershell -NoProfile -ExecutionPolicy Bypass -Command $\"$$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -Uri '${ENGINE_URL}' -OutFile '$1' -UseBasicParsing } catch { exit 1 }$\""
    Pop $4
  ${EndIf}
FunctionEnd

Function EnsureEngine
  Call FindEngine
  ${If} $EngineExe != ""
    !ifdef ENGINE_MIN_VERSION
      ; Only compared when this payload DECLARES a minimum. The engine<->payload contract is
      ; additive-only, so the normal case is "any engine will do" and no comparison happens at
      ; all. A failed comparison is treated as satisfied on purpose: guessing "too old" would
      ; reinstall a working engine on every launch of every installer.
      nsExec::ExecToStack "powershell -NoProfile -ExecutionPolicy Bypass -Command $\"try { if ([version]'$EngineVersion' -ge [version]'${ENGINE_MIN_VERSION}') { 'ok' } else { 'old' } } catch { 'ok' }$\""
      Pop $4
      Pop $5
      StrCpy $6 $5 2
      ${If} $6 == "ol"
        DetailPrint "Engine $EngineVersion is older than this app needs (${ENGINE_MIN_VERSION}) — updating it."
        StrCpy $EngineExe ""
      ${EndIf}
    !endif
  ${EndIf}

  ${If} $EngineExe != ""
    DetailPrint "Using the installed agentd engine: $EngineExe ($EngineVersion)"
    Return
  ${EndIf}

  StrCpy $1 "$PLUGINSDIR\agentd-engine-setup.exe"
  Call DownloadEngine
  ${IfNot} ${FileExists} "$1"
    Abort "Could not download the agentd engine from ${ENGINE_URL}. Check the connection and run this installer again."
  ${EndIf}

  StrCpy $2 "${ENGINE_SHA256}"
  Call VerifySha256
  ${If} $3 != "ok"
    Delete "$1"
    ; Refuse, loudly, and delete it. This is the one place the installer could be turned into a
    ; way to run an attacker's executable, so a failure here is never "carry on anyway".
    Abort "The downloaded engine did not match its expected checksum ($3). Nothing was installed."
  ${EndIf}
  ; The one long step, and the only one that looks like a hang: the engine is ~250 MB of solid
  ; LZMA and takes MINUTES to unpack, during which ExecWait blocks and nothing here can move.
  ; Saying so is the difference between "installing" and "frozen, kill it".
  DetailPrint "Engine verified. Unpacking it — this takes a few minutes and happens only once."
  DetailPrint "(The window will look idle while it runs. Please leave it open.)"
  ; /S silent, /currentuser PER-USER. Without /currentuser a silent electron-builder install
  ; chooses ALL-USERS whenever it can elevate — it landed in C:\Program Files on the first real
  ; run — which needs admin, and the runtime's pip plugins then cannot write without it. This
  ; stub is RequestExecutionLevel user, so a per-user engine is the only mode it can promise.
  ExecWait '"$1" /S /currentuser' $4
  ${If} $4 != 0
    Abort "The agentd engine installer failed (exit $4). Nothing else was installed."
  ${EndIf}

  Call FindEngine
  ${If} $EngineExe == ""
    Abort "The engine installed but did not register itself under HKCU\${ENGINE_KEY}. Please report this."
  ${EndIf}
FunctionEnd

;--------------------------------------------------------------------------- install

Section "Install"
  SetShellVarContext current
  Call EnsureEngine

  ; The payload. Cleared first: it is fully derived, and a leftover .agentpkg from an older
  ; version would sit next to the new one with the engine free to install either.
  RMDir /r "$INSTDIR"
  CreateDirectory "$INSTDIR"
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD_DIR}\*"   ; `*` not `*.*` — a payload file without an extension must ship too

  ; Shortcuts run the SHARED engine, pointed at this payload. Note the honest limit: Windows
  ; groups taskbar buttons by the AppUserModelID the running app declares, which the engine sets
  ; from the payload's app_id ("${APP_ID}"). A .lnk can also carry that id, but setting it needs a
  ; shell-property plugin; without it, PINNING a shortcut before first run may group it under the
  ; engine. Running the app is what settles it.
  ;
  ; The icon is OPTIONAL — an agent that declares none ships a payload without one. Naming a file
  ; that is not there gives the shortcut and the Add/Remove row a blank placeholder icon; an empty
  ; icon argument makes both fall back to the engine's own, which is the honest default.
  StrCpy $R0 ""
  ${If} ${FileExists} "$INSTDIR\icon.ico"
    StrCpy $R0 "$INSTDIR\icon.ico"
  ${Else}
    StrCpy $R0 "$EngineExe"
  ${EndIf}
  CreateDirectory "$SMPROGRAMS"
  CreateShortcut "$SMPROGRAMS\${PRODUCT_NAME}.lnk" "$EngineExe" '--app-dir "$INSTDIR"' "$R0"
  CreateShortcut "$DESKTOP\${PRODUCT_NAME}.lnk" "$EngineExe" '--app-dir "$INSTDIR"' "$R0"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Its own Add/Remove Programs row, per product. The engine has its own, separately: a person who
  ; removes "Amazon Sales" is removing that app, not the runtime three other apps are using.
  WriteRegStr HKCU "${ARP_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${ARP_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "${ARP_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${ARP_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegStr HKCU "${ARP_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${ARP_KEY}" "DisplayIcon" "$R0"
  WriteRegStr HKCU "${ARP_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "${ARP_KEY}" "AgentId" "${AGENT_ID}"
  WriteRegDWORD HKCU "${ARP_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${ARP_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $1 $2 $3
  IntFmt $4 "0x%08X" $1
  WriteRegDWORD HKCU "${ARP_KEY}" "EstimatedSize" $4
SectionEnd

;--------------------------------------------------------------------------- uninstall

Section "Uninstall"
  SetShellVarContext current
  Delete "$SMPROGRAMS\${PRODUCT_NAME}.lnk"
  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
  ; ONLY this product's payload. Not the engine (shared), and not ~/.agentd (the user's agents,
  ; sessions and workspace live there — removing one app must never take someone's data with it).
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${ARP_KEY}"
SectionEnd

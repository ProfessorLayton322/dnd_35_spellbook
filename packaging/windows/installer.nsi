; Per-user installer for D&D 3.5 Spellbook. Built by build.py, which passes
; APP_VERSION, STAGE_DIR (the files to install), and OUT_FILE.

Unicode true
ManifestDPIAware true
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!define APP_NAME "D&D 3.5 Spellbook"
!define APP_ID "DnD35Spellbook"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}"
; Held by the running app (see spellbook_builder/desktop.py).
!define RUNNING_MUTEX "Local\DnD35SpellbookRunning"
!define LAUNCH_ARGS "-m spellbook_builder.desktop"

; Dialog text treats a single & as a keyboard shortcut marker.
Name "${APP_NAME}" "D&&D 3.5 Spellbook"
OutFile "${OUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP_ID}"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
RequestExecutionLevel user
BrandingText "${APP_NAME} ${APP_VERSION}"

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "FileVersion" "${APP_VERSION}"
VIAddVersionKey "FileDescription" "${APP_NAME} Setup"

!define MUI_ICON "${STAGE_DIR}/Spellbook.ico"
!define MUI_UNICON "${STAGE_DIR}/Spellbook.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Start D&&D 3.5 Spellbook now"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

; Files of a running app cannot be replaced, so ask the user to quit it first.
!macro WaitForAppToClose
  retry_close:
    System::Call 'kernel32::OpenMutexW(i 0x00100000, i 0, t "${RUNNING_MUTEX}") p .r0'
    ${If} $0 != 0
      System::Call 'kernel32::CloseHandle(p r0)'
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "D&D 3.5 Spellbook is still running.$\n$\nQuit it (Spellbook menu > Quit, or click OK in its message), then click Retry." IDRETRY retry_close
      Abort
    ${EndIf}
!macroend

Function LaunchApp
  ExecShell "" "$SMPROGRAMS\${APP_NAME}.lnk"
FunctionEnd

Section "Install"
  !insertmacro WaitForAppToClose

  SetOutPath "$INSTDIR"
  ; Replace the bundled Python and app cleanly. User data lives in $LOCALAPPDATA\${APP_ID}.
  RMDir /r "$INSTDIR\python"
  File /r "${STAGE_DIR}/*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateShortCut "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\python\pythonw.exe" "${LAUNCH_ARGS}" "$INSTDIR\Spellbook.ico" 0 SW_SHOWNORMAL "" "Build printable D&D 3.5 spellbooks"
  CreateShortCut "$SMPROGRAMS\${APP_NAME} (web browser).lnk" "$INSTDIR\python\pythonw.exe" "${LAUNCH_ARGS} --browser" "$INSTDIR\Spellbook.ico" 0 SW_SHOWNORMAL "" "Use D&D 3.5 Spellbook in your web browser"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\python\pythonw.exe" "${LAUNCH_ARGS}" "$INSTDIR\Spellbook.ico" 0 SW_SHOWNORMAL "" "Build printable D&D 3.5 spellbooks"

  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\Spellbook.ico"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" $0
SectionEnd

Section "Uninstall"
  !insertmacro WaitForAppToClose

  Delete "$SMPROGRAMS\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME} (web browser).lnk"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$INSTDIR\python"
  Delete "$INSTDIR\Spellbook.ico"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"

  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "Also delete your spellbooks, imported spell lists, and PDFs?$\n$\nThey are stored in $LOCALAPPDATA\${APP_ID}." /SD IDNO IDNO keep_data
    RMDir /r "$LOCALAPPDATA\${APP_ID}"
  keep_data:
SectionEnd

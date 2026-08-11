; ClaudeStatusLight.iss
;
; Customer-facing installer. Bundles the three standalone exes built by
; build_specs\*.spec (ClaudeStatusWatcher.exe, ClaudeCodeHook.exe,
; ClaudeCodeHookInstaller.exe -- must all be built first, see
; "Build order" below) into one setup.exe: copies files, registers the
; Claude Code hook, sets up autostart, and reverses all of it cleanly on
; uninstall.
;
; Build order (each dist folder below must exist before compiling this):
;   py -3.11 -m PyInstaller build_specs\ClaudeStatusWatcher.spec
;   py -3.11 -m PyInstaller build_specs\ClaudeCodeHook.spec
;   py -3.11 -m PyInstaller build_specs\ClaudeCodeHookInstaller.spec
;   (all run with --workpath ..\build_work --distpath ..\build_dist)
; Then: iscc ClaudeStatusLight.iss
;
; Per-user install (PrivilegesRequired=lowest), not Program Files -- this
; is a personal hardware companion app, not shared machine software, and
; per-user means no UAC prompt and no admin dependency for something this
; small. ClaudeCodeHook.exe and ClaudeCodeHookInstaller.exe are installed
; into the SAME directory ({app}\Hook) deliberately: ClaudeCodeHookInstaller.exe
; locates ClaudeCodeHook.exe by looking next to its own running exe (see
; _this_install_dir() in claude_code_hook_installer.py) -- splitting them
; into separate folders would silently break hook registration.

#define MyAppName "Claude Status Light"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Claude Status Light"

[Setup]
AppId={{DE6832B8-DB8C-4A37-ACBE-C68E1F46B70B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\ClaudeStatusLight
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\installer_output
OutputBaseFilename=ClaudeStatusLightSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Watcher\ClaudeStatusWatcher.exe

[Files]
Source: "..\build_dist\ClaudeStatusWatcher\*"; DestDir: "{app}\Watcher"; Flags: recursesubdirs ignoreversion
Source: "..\build_dist\ClaudeCodeHook\*"; DestDir: "{app}\Hook"; Flags: recursesubdirs ignoreversion
Source: "..\build_dist\ClaudeCodeHookInstaller\*"; DestDir: "{app}\Hook"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\Watcher\ClaudeStatusWatcher.exe"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
; Autostart -- a Startup-folder shortcut rather than a registry Run key so
; a customer can see/remove it themselves without knowing where to look.
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\Watcher\ClaudeStatusWatcher.exe"

[Run]
; Idempotent (see claude_code_hook_installer.py) -- always safe to run,
; even for a customer who never uses Claude Code; it just sits unused.
Filename: "{app}\Hook\ClaudeCodeHookInstaller.exe"; Flags: runhidden waituntilterminated; StatusMsg: "Registering Claude Code hook..."
Filename: "{app}\Watcher\ClaudeStatusWatcher.exe"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; UninstallRun entries fire as the FIRST step of uninstall, before Setup
; deletes any files -- confirmed against Inno Setup's own docs -- which is
; why both of these are safe to rely on files/processes still being there.
; Stop the watcher first so its exe/DLLs aren't locked when Setup deletes them.
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM ClaudeStatusWatcher.exe"; Flags: runhidden; RunOnceId: "StopWatcher"
; Strip our hook entries from settings.json before ClaudeCodeHookInstaller.exe
; itself gets deleted -- otherwise the customer is left with a permanently
; broken hook command pointing at a now-missing exe (silently, by design;
; see claude_code_status_hook.py's "SILENT BY DESIGN" note).
Filename: "{app}\Hook\ClaudeCodeHookInstaller.exe"; Parameters: "--uninstall"; Flags: runhidden skipifdoesntexist; RunOnceId: "RemoveClaudeCodeHook"

[Code]
// Also kill a running watcher before (re)installing -- covers reinstall/
// upgrade over a live instance, which would otherwise fail to overwrite
// ClaudeStatusWatcher.exe while it's running.
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /IM ClaudeStatusWatcher.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

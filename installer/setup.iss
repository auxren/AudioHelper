; Inno Setup script for Trader's Little Jedi
; Run on Windows:  iscc installer\setup.iss
; Or via build_windows.ps1

#define AppName      "Trader's Little Jedi"
#define AppVersion   "0.2.0"
#define AppPublisher "auxren"
#define AppURL       "https://github.com/auxren/AudioHelper"
#define AppExeName   "TradersLittleJedi.exe"
#define AppId        "{{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
LicenseFile=
; OutputDir is set relative to the script location
OutputDir=..\dist
OutputBaseFilename=TradersLittleJedi-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest          ; install per-user, no UAC prompt
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763              ; Windows 10 1809+

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";    Description: "Create a &Desktop shortcut"; GroupDescription: "Additional icons:"
Name: "quicklaunchicon"; Description: "Create a &Quick Launch shortcut"; GroupDescription: "Additional icons:"; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Files]
; Main executable
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Bundled CLI tools (ffmpeg, flac, etc.) — included in the PyInstaller exe
; If using a folder-mode PyInstaller build, include the whole dist folder:
; Source: "..\dist\TradersLittleJedi\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
// Remove config.json on uninstall so paths don't linger
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ConfigPath: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    ConfigPath := ExpandConstant('{app}\config.json');
    if FileExists(ConfigPath) then
      DeleteFile(ConfigPath);
  end;
end;

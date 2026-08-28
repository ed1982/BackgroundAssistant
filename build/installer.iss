; Inno Setup script — per-user install, no administrator rights (§8.2).
#define AppName "Background Assistant"
#define AppVersion "0.2.0"
#define AppPublisher "Ed Martin"
#define AppExe "BackgroundAssistant.exe"

[Setup]
AppId={{9C1B4F2E-7A54-4D1C-9C0B-BA0F4C6E1D77}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=BackgroundAssistant-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\BackgroundAssistant\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
    Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start {#AppName} when I sign in"; \
    GroupDescription: "Startup:"

[Registry]
; Launch at login (D13). The value name is the unspaced identifier, matching
; what bgassist/platform/login_item.py writes, so the two agree.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "BackgroundAssistant"; ValueData: """{app}\{#AppExe}"""; \
    Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; \
    Flags: nowait postinstall skipifsilent

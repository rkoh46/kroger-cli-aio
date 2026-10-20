; Inno Setup installer for kroger-aio Windows executable
; Build the installer on Windows with: iscc kroger-aio.iss
;
; This creates a proper .exe installer that:
; - Installs to Program Files
; - Creates desktop and start menu shortcuts
; - Adds kroger-aio to PATH (optional)
; - Installs Playwright Chromium browser (first-run)

#define MyAppName "kroger-aio"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "rkoh46"
#define MyAppURL "https://github.com/rkoh46/kroger-aio"
#define MyAppExeName "kroger-aio.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={pf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=kroger-aio-setup
SetupIconFile=kroger-aio-icon.ico
Compression=lzma
SolidCompression=yes

[Files]
Source: "dist\kroger-aio.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} Website"; Filename: "{#MyAppURL}"
Name: "{desktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Run {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

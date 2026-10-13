; DiagTrack Single-Click Windows Installer
; Inno Setup Script (ISCC)

#define MyAppName "DiagTrack"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Pramodini Medicare Limited"
#define MyAppURL "https://github.com/gauthami2108/diagTrack"
#define MyAppExeName "Setup.cmd"

[Setup]
AppId={{5E5B9423-8B37-4D2A-9418-8A3A4016723B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={commonpf64}\DiagTrack
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=DiagTrack-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".git\*,.github\*,data\*,secrets\*,*.pyc,__pycache__\*"

[Icons]
Name: "{group}\{#MyAppName} Setup"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch DiagTrack Setup Wizard"; Flags: postinstall runascurrentuser nowait

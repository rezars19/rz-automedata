; ============================================================
; RZ Automedata - Inno Setup Installer Script
; ============================================================
; 
; Cara pakai:
; 1. Install Inno Setup dari https://jrsoftware.org/isinfo.php
; 2. Buka file ini di Inno Setup Compiler
; 3. Klik Build > Compile
; 4. Hasilnya di folder Output/
;
; Atau compile via command line:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
; ============================================================

#define MyAppName "RZ Automedata"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "RZ Studio"
#define MyAppURL "https://github.com/rezars19/rz-automedata"
#define MyAppExeName "RZAutomedata.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=RZAutomedata_Setup_v{#MyAppVersion}
SetupIconFile=
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Visual
WizardImageFile=
WizardSmallImageFile=

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; Start Menu shortcut (always created)
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; Option to launch app after install
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up app data on uninstall (optional)
Type: filesandordirs; Name: "{app}"

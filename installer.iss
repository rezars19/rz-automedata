; ============================================================
; RZ Automedata - Inno Setup Installer Script
; ============================================================
; 
; Prerequisites:
;   - Install Inno Setup 6 dari https://jrsoftware.org/isinfo.php
;   - Pastikan dist\RZAutomedata.exe sudah di-build dulu (python build.py)
;
; Cara compile:
;   1. Buka file ini di Inno Setup Compiler
;   2. Klik Build > Compile
;   3. Hasilnya di folder Output/
;
; Atau compile via command line:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
; ============================================================

; ── App Metadata ─────────────────────────────────────────────
#define MyAppName "RZ Automedata"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "RZ Studio"
#define MyAppURL "https://github.com/rezars19/rz-automedata"
#define MyAppExeName "RZAutomedata.exe"
#define MyAppCopyright "Copyright © 2026 RZ Studio"

[Setup]
; NOTE: Nilai AppId unik per aplikasi. Jangan ubah ini setelah rilis pertama!
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright={#MyAppCopyright}

; ── Install Directories ──────────────────────────────────────
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; ── Output ───────────────────────────────────────────────────
OutputDir=Output
OutputBaseFilename=RZAutomedata_Setup_v{#MyAppVersion}
SetupIconFile=icon.ico

; ── Compression ──────────────────────────────────────────────
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4

; ── Visual & UX ──────────────────────────────────────────────
WizardStyle=modern
; Uncomment baris berikut jika punya gambar wizard (164x314 px)
; WizardImageFile=wizard_image.bmp
; Uncomment baris berikut jika punya gambar small wizard (55x55 px)
; WizardSmallImageFile=wizard_small_image.bmp

; ── Privileges ───────────────────────────────────────────────
; "lowest" = bisa install tanpa admin (ke folder user)
; Tapi user tetap bisa pilih install untuk semua user (butuh admin)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; ── Versioning & Upgrade ─────────────────────────────────────
; Info versi di properties file Setup exe
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoCopyright={#MyAppCopyright}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

; Uninstall info
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

; ── Minimum Windows Version ──────────────────────────────────
MinVersion=10.0

; ── Misc ─────────────────────────────────────────────────────
; Tampilkan "app sudah jalan, tutup dulu?" saat install/uninstall  
CloseApplications=yes
RestartApplications=no
; Disable "ready to install" page utk flow yg lebih ringkas
DisableReadyPage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkablealone

[Files]
; Main executable
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; Icon file (untuk runtime access)
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

; Logo (untuk runtime access jika diperlukan)
Source: "logo.png"; DestDir: "{app}"; Flags: ignoreversion

; Cairo DLLs (jika ada file didalamnya)
Source: "cairo_dlls\*"; DestDir: "{app}\cairo_dlls"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
; Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; Start Menu shortcuts
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

[Registry]
; Simpan info versi di registry untuk deteksi upgrade
Root: HKCU; Subkey: "Software\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey

[Run]
; Option to launch app after install
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up app directory on uninstall
Type: filesandordirs; Name: "{app}"

[Code]
// ════════════════════════════════════════════════════════════
// Custom Pascal Script
// ════════════════════════════════════════════════════════════

// Cek apakah app sedang berjalan sebelum install/uninstall
function IsAppRunning(): Boolean;
var
  ResultCode: Integer;
begin
  // tasklist cek apakah proses running
  Result := False;
  if Exec('cmd.exe', '/C tasklist /FI "IMAGENAME eq {#MyAppExeName}" 2>NUL | find /I "{#MyAppExeName}" >NUL', 
           '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := (ResultCode = 0);
  end;
end;

// Tutup app jika sedang berjalan
function CloseRunningApp(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if IsAppRunning() then
  begin
    if MsgBox('{#MyAppName} sedang berjalan.' + #13#10 + 
              'Tutup aplikasi untuk melanjutkan instalasi?', 
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      Exec('cmd.exe', '/C taskkill /F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Sleep(1000); // Tunggu 1 detik agar proses benar-benar berhenti
    end
    else
    begin
      Result := False;
    end;
  end;
end;

// Hook: sebelum install dimulai
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not CloseRunningApp() then
  begin
    Result := '{#MyAppName} masih berjalan. Tutup aplikasi terlebih dahulu.';
  end;
end;

// Hook: sebelum uninstall
function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if IsAppRunning() then
  begin
    if MsgBox('{#MyAppName} sedang berjalan.' + #13#10 +
              'Tutup aplikasi untuk melanjutkan uninstall?',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      Exec('cmd.exe', '/C taskkill /F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Sleep(1000);
    end
    else
    begin
      Result := False;
    end;
  end;
end;

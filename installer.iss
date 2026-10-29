; ═══════════════════════════════════════════════════════════════
; FreQ — Radio Playlist Manager
; Modern Installer with Inno Setup 6.x
;
; Build: Open in Inno Setup → Build → Compile
; ═══════════════════════════════════════════════════════════════

#define MyAppName "FreQ"
; MyAppVersion is the bare sortable release id (tags, installer filename):
;   Ultimate era: yyyy.mmdd.round = year, Build month+day, update round
;   Legacy era:   MAJOR.MINOR.PATCH[-SUFFIX]
#define MyAppVersion "2026.1029.1"
; Windows file metadata (VersionInfoVersion) must be strictly numeric —
; release.py --set-version mirrors MyAppVersion into this define
; automatically (Ultimate 2026.0924.1 -> 2026.9.241).
#define MyAppVersionNumeric "2026.10.2901"
; Human-facing branding shown in the installer wizard, Add/Remove Programs
; and the splash — mirrored by release.py --set-version automatically.
#define MyAppVersionDisplay "Ultimate 2026 (Build 1029.1)"
#define MyAppPublisher "SocieticsTv Broadcasting & Network"
#define MyAppURL "https://github.com/SocieticsTv/freq"
#define MyAppExeName "FreQ.exe"
#define MyAppDescription "Radio Playlist Manager"

[Setup]
AppId={{B1E3C7A2-4F5D-4E8A-9C2B-1A3D5F6E7B8A}
AppName={#MyAppName}
; AppVersion carries the branded display form (spaces/parentheses are fine
; here); the strictly-numeric VersionInfo* fields below use MyAppVersionNumeric.
AppVersion={#MyAppVersionDisplay}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
LicenseFile=LICENSE

; Output
OutputDir=..\_final
OutputBaseFilename=FreQ-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes

; Icon
SetupIconFile=logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

; Modern UI + FreQ Branding
WizardStyle=modern
WizardSizePercent=110
WizardImageFile=wizard_large.bmp
WizardSmallImageFile=wizard_small.bmp
WizardKeepAspectRatio=yes
WizardBackColor=$F4F7FB
WizardImageBackColor=$0F172A
WizardSmallImageBackColor=$0F172A

; Privileges
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Version info — MyAppVersionNumeric always contains exactly three numeric
; parts, so appending ".0" is unnecessary and breaks quad values like 2026.9.241.
VersionInfoVersion={#MyAppVersionNumeric}
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersionNumeric}

; Close app if running
CloseApplications=force
CloseApplicationsFilter=FreQ.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel1=ยินดีต้อนรับสู่การติดตั้ง {#MyAppName}
WelcomeLabel2=ติดตั้ง {#MyAppName} เพื่อจัดการเพลย์ลิสต์และสตรีมวิทยุของคุณ\n\nเวอร์ชัน {#MyAppVersionDisplay}
SelectDirLabel3=โปรแกรมจะถูกติดตั้งในโฟลเดอร์ต่อไปนี้:
ReadyLabel1=พร้อมติดตั้ง {#MyAppName} แล้ว
FinishedLabel=ติดตั้ง {#MyAppName} เสร็จสมบูรณ์แล้ว
FinishedHeadingLabel=การติดตั้งเสร็จสมบูรณ์

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce
Name: "associatefreq"; Description: "Associate .freq playlist files"; GroupDescription: "File associations:"; Flags: checkedonce
Name: "installffmpeg"; Description: "Download & install FFmpeg (needed for YouTube/streaming)"; GroupDescription: "Optional components:"; Flags: checkedonce

[Files]
; Main application
Source: "dist\FreQ\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\FreQ\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

; Assets
Source: "logo.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "logo.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "logo.svg"; DestDir: "{app}"; Flags: ignoreversion

; Source code (for reference — free & open source release)
; Keep this list in step with the modules gui.py actually imports
; (release.py / build.py fail on a missing module long before this file).
Source: "gui.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "radio_manager.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "player.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "audio.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "audio_meter.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "features.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "cache.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "streaming.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "icons.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "tooltips.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "log_handler.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "app.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "asrun.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "aircheck.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "aircheck_split.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "dual_output.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "audio_editor.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "editor_window.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "focus_mode.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "keys_tab.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "livelan.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "mic_recorder.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "plugin_system.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "scheduler.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "silence_sentinel.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "theme.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "titlebar.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "update_checker.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "visualizer.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "freq_supervisor.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "sentinel_heal.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}\src"; Flags: ignoreversion

; The C++ sources of the audio extensions, plus the script that builds them:
; the binaries are shipped in _internal, so the code they were built from
; travels with them (they are the sample-work core of the editor and the
; engine behind playback, capture and streaming).
Source: "native_*.cpp"; DestDir: "{app}\src\native"; Flags: ignoreversion
Source: "miniaudio.h"; DestDir: "{app}\src\native"; Flags: ignoreversion
Source: "build_native_meter.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "build_native_meter.bat"; DestDir: "{app}\src"; Flags: ignoreversion

; The operator's end-of-song mark artwork + the converter that bakes it.
; The app itself reads the PNGs from the bundle (_internal\icons, shipped by
; build.py); these copies keep the source tree complete so the icons can be
; changed and re-baked without the repo.
Source: "icons\*.svg"; DestDir: "{app}\src\icons"; Flags: ignoreversion
Source: "icons\*.png"; DestDir: "{app}\src\icons"; Flags: ignoreversion
Source: "make_end_icons.py"; DestDir: "{app}\src"; Flags: ignoreversion

; GPL-3.0 license and project documentation
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

; FFmpeg (if bundled)
Source: "deps\ffmpeg-essentials\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
; Start Menu
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Open {#MyAppName}"
Name: "{group}\{#MyAppName} Documentation"; Filename: "{app}\README.md"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

; Desktop
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "Open {#MyAppName}"

[Run]
; Launch after install
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

; FFmpeg download (only if the user selected the task)
Filename: "{sys}\cmd.exe"; Parameters: "/c curl -L --progress-bar -o {tmp}\ffmpeg.zip https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"; StatusMsg: "Downloading FFmpeg (this may take a few minutes)..."; Flags: runhidden waituntilterminated skipifsilent; Tasks: installffmpeg
Filename: "{sys}\cmd.exe"; Parameters: "/c powershell Expand-Archive -Path {tmp}\ffmpeg.zip -DestinationPath {tmp}\ffmpeg -Force"; StatusMsg: "Extracting FFmpeg..."; Flags: runhidden waituntilterminated skipifsilent; Tasks: installffmpeg
Filename: "{sys}\cmd.exe"; Parameters: "/c powershell Copy-Item {tmp}\ffmpeg\ffmpeg-*\bin\ffmpeg.exe {app}\ffmpeg\ -Force & Copy-Item {tmp}\ffmpeg\ffmpeg-*\bin\ffprobe.exe {app}\ffmpeg\ -Force"; StatusMsg: "Installing FFmpeg..."; Flags: runhidden waituntilterminated skipifsilent; Tasks: installffmpeg

[Registry]
; .freq file association
Root: HKA; Subkey: "Software\Classes\.freq"; ValueType: string; ValueName: ""; ValueData: "FreQ.FreqFile"; Flags: uninsdeletevalue; Tasks: associatefreq
Root: HKA; Subkey: "Software\Classes\FreQ.FreqFile"; ValueType: string; ValueName: ""; ValueData: "FreQ Playlist File"; Flags: uninsdeletekey; Tasks: associatefreq
Root: HKA; Subkey: "Software\Classes\FreQ.FreqFile\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: associatefreq
Root: HKA; Subkey: "Software\Classes\FreQ.FreqFile\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: associatefreq

; NOTE: FFmpeg PATH entry is no longer managed here.
; The old approach used Flags: uninsdeletevalue on the whole "Path" value,
; which would WIPE THE USER'S ENTIRE PATH on uninstall (not just the FFmpeg
; entry), and it added {app}\ffmpeg to PATH even when the user did not
; select the "installffmpeg" task. Both are fixed in [Code] below using
; surgical add/remove of just the FreQ ffmpeg segment.

[UninstallDelete]
Type: filesandordirs; Name: "{app}\radio_queue.freq"
Type: filesandordirs; Name: "{app}\src"

[Code]
var
  FFmpegPathAdded: Boolean;

// ─── PATH helpers: only ever touch the single {app}\ffmpeg segment ───

function GetFFmpegPathDir(): String;
begin
  Result := ExpandConstant('{app}\ffmpeg');
end;

procedure AddFFmpegToPath();
var
  CurrentPath: String;
  FFmpegDir: String;
begin
  FFmpegDir := GetFFmpegPathDir();

  if not RegQueryStringValue(HKCU, 'Environment', 'Path', CurrentPath) then
    CurrentPath := '';

  // Don't add a duplicate entry if it's already there (e.g. re-installing)
  if Pos(LowerCase(FFmpegDir), LowerCase(CurrentPath)) = 0 then
  begin
    if (CurrentPath <> '') and (CurrentPath[Length(CurrentPath)] <> ';') then
      CurrentPath := CurrentPath + ';';
    CurrentPath := CurrentPath + FFmpegDir;
    RegWriteExpandStringValue(HKCU, 'Environment', 'Path', CurrentPath);
  end;
end;

procedure RemoveFFmpegFromPath();
var
  CurrentPath, NewPath, FFmpegDir: String;
  P: Integer;
begin
  FFmpegDir := GetFFmpegPathDir();

  if not RegQueryStringValue(HKCU, 'Environment', 'Path', CurrentPath) then
    Exit;

  // Remove only the exact FreQ ffmpeg segment, wherever it sits in the list,
  // leaving every other PATH entry the user has completely untouched.
  NewPath := CurrentPath;

  P := Pos(LowerCase(FFmpegDir + ';'), LowerCase(NewPath));
  if P > 0 then
    Delete(NewPath, P, Length(FFmpegDir) + 1)
  else
  begin
    P := Pos(LowerCase(';' + FFmpegDir), LowerCase(NewPath));
    if P > 0 then
      Delete(NewPath, P, Length(FFmpegDir) + 1)
    else
    begin
      P := Pos(LowerCase(FFmpegDir), LowerCase(NewPath));
      if (P > 0) and (NewPath = FFmpegDir) then
        NewPath := '';
    end;
  end;

  if NewPath <> CurrentPath then
    RegWriteExpandStringValue(HKCU, 'Environment', 'Path', NewPath);
end;

// Check if app is running
function InitializeSetup(): Boolean;
begin
  Result := True;
  if CheckForMutexes('Global\{#MyAppName}') then
  begin
    if MsgBox('{#MyAppName} is currently running.'#13#10'Please close it before continuing.',
              mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;

// Welcome page custom message
procedure InitializeWizard;
begin
  WizardForm.Bevel.Visible := False;
end;

// Only add FFmpeg to PATH if the user actually chose to install it
// AND the folder genuinely exists after the [Run] steps finished.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if IsTaskSelected('installffmpeg') and DirExists(GetFFmpegPathDir()) then
    begin
      AddFFmpegToPath();
      FFmpegPathAdded := True;
    end;
  end;
end;

// Surgically remove only the FreQ ffmpeg PATH segment on uninstall —
// never touches or deletes the rest of the user's PATH.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveFFmpegFromPath();
end;

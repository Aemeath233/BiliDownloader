#define MyAppName "BiliDownloader"
#define MyAppVersion "2.2.0"
#define MyAppPublisher "BiliDownloader"
#define MyAppExeName "BiliDownloader.exe"

[Setup]
AppId={{A845D3E4-8D4E-48A2-9D1E-6B59E248947D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=BiliDownloader-Setup
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
MinVersion=10.0.14393
LicenseFile=..\LICENSE
CloseApplications=no
RestartApplications=no
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "..\dist\installer\app\BiliDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\vendor\webview2-bootstrapper\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"; Check: FileExists(ExpandConstant('{app}\{#MyAppExeName}'))

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebView2Key = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function HasWebView2At(Root: Integer): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(Root, WebView2Key, 'pv', Version);
  if Result then
    Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function HasWebView2: Boolean;
begin
  Result := HasWebView2At(HKLM32) or HasWebView2At(HKCU32) or HasWebView2At(HKLM64) or HasWebView2At(HKCU64);
end;

function AppIsRunning: Boolean;
begin
  Result := FindWindowByWindowName('{#MyAppName}') <> 0;
end;

function InitializeSetup: Boolean;
var
  Release: Cardinal;
begin
  Result := False;
  if not RegQueryDWordValue(HKLM32, 'Software\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release', Release) or (Release < 394802) then begin
    SuppressibleMsgBox('需要 .NET Framework 4.6.2 或更新版本。请先更新 Windows 后再安装。', mbError, MB_OK, IDOK);
    Exit;
  end;
  if AppIsRunning then begin
    SuppressibleMsgBox('请先退出 BiliDownloader（有下载任务时请先暂停或停止），再运行安装程序。', mbInformation, MB_OK, IDOK);
    Exit;
  end;
  Result := True;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpReady then begin
    if HasWebView2 then
      WizardForm.ReadyMemo.Lines.Add(#13#10 + 'WebView2：使用系统已有运行时，无需重复下载。')
    else
      WizardForm.ReadyMemo.Lines.Add(#13#10 + 'WebView2：此电脑缺少运行时，安装时将联网下载微软组件。');
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
begin
  Result := '';
  if AppIsRunning then begin
    Result := '请先退出 BiliDownloader，再继续安装。';
    Exit;
  end;
  if HasWebView2 then begin
    Log('WebView2 runtime already installed; skipping bootstrapper.');
    Exit;
  end;
  WizardForm.StatusLabel.Caption := '正在下载并安装 Microsoft WebView2，请保持网络连接……';
  ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
  if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then begin
    Result := '无法启动 WebView2 安装程序。请安装微软 WebView2 Runtime 后重试。';
    Exit;
  end;
  Log(Format('WebView2 bootstrapper exit code: %d', [ExitCode]));
  if not HasWebView2 then
    Result := 'WebView2 未安装成功，请检查网络连接。也可从微软官网下载 WebView2 Runtime 后重新安装。';
  if ExitCode = 3010 then
    NeedsRestart := True;
end;

function InitializeUninstall: Boolean;
begin
  Result := not AppIsRunning;
  if not Result then
    SuppressibleMsgBox('请先退出 BiliDownloader，再运行卸载程序。', mbInformation, MB_OK, IDOK);
end;

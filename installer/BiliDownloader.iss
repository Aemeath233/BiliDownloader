#ifndef MyAppName
#define MyAppName "BiliDownloader"
#endif
#ifndef MyAppVersion
#define MyAppVersion "3.0.0"
#endif
#ifndef MyAppFileVersion
#define MyAppFileVersion MyAppVersion
#endif
#ifndef MyAppId
#define MyAppId "{{A845D3E4-8D4E-48A2-9D1E-6B59E248947D}"
#endif
#ifndef AppSourceDir
#define AppSourceDir "..\dist\winui"
#endif
#ifndef AppOutputDir
#define AppOutputDir "..\dist\native-installer"
#endif
#define MyAppPublisher "BiliDownloader"
#define MyAppExeName "BiliDownloader.exe"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#AppOutputDir}
OutputBaseFilename=BiliDownloader-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
MinVersion=10.0.19041
LicenseFile=..\LICENSE
CloseApplications=no
RestartApplications=no
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage
VersionInfoVersion={#MyAppFileVersion}

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"; Check: FileExists(ExpandConstant('{app}\{#MyAppExeName}'))

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]

function AppIsRunning: Boolean;
begin
  Result := FindWindowByWindowName('BiliDownloader') <> 0;
end;

function InitializeSetup: Boolean;
begin
  Result := False;
  if AppIsRunning then begin
    SuppressibleMsgBox('请先退出 BiliDownloader（有下载任务时请先暂停或停止），再运行安装程序。', mbInformation, MB_OK, IDOK);
    Exit;
  end;
  Result := True;
end;


function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if AppIsRunning then begin
    Result := '请先退出 BiliDownloader，再继续安装。';
    Exit;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := not AppIsRunning;
  if not Result then
    SuppressibleMsgBox('请先退出 BiliDownloader，再运行卸载程序。', mbInformation, MB_OK, IDOK);
end;

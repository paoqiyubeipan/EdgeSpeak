; EdgeSpeak 安装包（Inno Setup 6）。先运行 build.cmd 生成 dist\EdgeSpeak，再运行 build_installer.cmd。
; 装到当前用户目录（%LOCALAPPDATA%\Programs\EdgeSpeak），不需要管理员权限。
; 卸载时保留 %APPDATA%\EdgeSpeak（配置和 Cohere 密钥），删除后台下载的词典 %LOCALAPPDATA%\EdgeSpeak\dict。
; 定义 Lite（build_installer.cmd 1.0.0 lite）时不带词典，程序第一次运行时在后台下载。

#define AppName "EdgeSpeak"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef DistDir
  #define DistDir "..\dist\EdgeSpeak"
#endif
#ifdef Lite
  #define Suffix "-lite"
  #define Excluded "\dict"
#else
  #define Suffix ""
  #define Excluded ""
#endif
#define AppExe "EdgeSpeak.exe"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
AppId={{6F1C2B7A-3E4D-4B8A-9C21-ED6E5A7B0C11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=EdgeSpeak-Setup-{#AppVersion}{#Suffix}
SetupIconFile=..\edgespeak.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
WizardStyle=modern
CloseApplications=no

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

#ifdef Lite
[Messages]
FinishedLabel=安装完成。首次启动后会在后台下载词典（约 1.4 GB），日英词典下载完就能查词，朗读不受影响。
#endif

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "其他："; Flags: unchecked
Name: "autostart"; Description: "开机自动启动"; GroupDescription: "其他："

[Files]
Source: "{#DistDir}\*"; Excludes: "{#Excluded}"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; 开机自启：和程序托盘菜单里的开关写的是同一个值，指向安装位置
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"""; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\EdgeSpeak\dict"

[Code]
procedure StopEdgeSpeak();
var
  Code: Integer;
begin
  { 程序在托盘里运行时文件被占用，先结束它 }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopEdgeSpeak();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { 没选开机自启时，去掉以前（比如文件夹版）留下的开机自启，免得指向旧位置 }
  if (CurStep = ssPostInstall) and not WizardIsTaskSelected('autostart') then
    RegDeleteValue(HKCU, '{#RunKey}', '{#AppName}');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopEdgeSpeak();
    { 托盘菜单里也能打开开机自启，所以卸载时不管是谁写的都删掉 }
    RegDeleteValue(HKCU, '{#RunKey}', '{#AppName}');
  end;
end;

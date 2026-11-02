#ifndef AppVersion
  #error AppVersion must be supplied by build_windows.py
#endif
#ifndef PayloadDir
  #error PayloadDir must be supplied by build_windows.py
#endif
#ifndef OutputDir
  #error OutputDir must be supplied by build_windows.py
#endif

[Setup]
AppId={{84C1B332-EF73-4B4B-98D5-CBF2BE719471}
AppName=EveryTensor
AppVersion={#AppVersion}
AppVerName=EveryTensor {#AppVersion}
AppPublisher=EveryTensor contributors
AppPublisherURL=https://github.com/SinsapZ/everytensor
DefaultDirName={localappdata}\Programs\EveryTensor
DefaultGroupName=EveryTensor
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=EveryTensor-{#AppVersion}-windows-x64-setup
SetupIconFile=..\..\build\windows-assets\EveryTensor.ico
UninstallDisplayIcon={app}\EveryTensor.exe
LicenseFile=..\..\LICENSE
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
LZMAUseSeparateProcess=yes
CloseApplications=yes
CloseApplicationsFilter=EveryTensor.exe,EveryTensor-console.exe
RestartApplications=no
Uninstallable=yes
CreateUninstallRegKey=not IsolatedInstall
UninstallDisplayName=EveryTensor

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\EveryTensor"; Filename: "{app}\EveryTensor.exe"; WorkingDir: "{app}"; Check: not IsolatedInstall
Name: "{autodesktop}\EveryTensor"; Filename: "{app}\EveryTensor.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Check: not IsolatedInstall

[Run]
Filename: "{app}\EveryTensor.exe"; Description: "{cm:LaunchProgram,EveryTensor}"; Flags: nowait postinstall skipifsilent

; User data is deliberately outside {app}. Uninstall removes only the files
; installed above; no wildcard deletion of indexes, models, or user documents.

[Code]
// For restricted build/test environments: install and uninstall only the
// application files. Normal interactive installs still register with Windows.
function IsolatedInstall: Boolean;
begin
  Result := ExpandConstant('{param:ISOLATED|0}') = '1';
end;

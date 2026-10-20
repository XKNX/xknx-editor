; Inno Setup script of the XKNX Editor installer for Windows. Built by build_installer.ps1 (next to
; this file), which passes the defines below:
;   AppVersion  the app version, e.g. 0.3.1
;   Arch        x64 or arm64
;   SourceDir   the PyInstaller output folder (dist\XKNX-Editor) without software OpenGL
;   MesaDir     optional: Mesa's software OpenGL DLLs, offered as the "Software rendering" task
;   OutputDir   where XKNX-Editor-windows-installer-<Arch>.exe is written

#ifndef AppVersion
  #error AppVersion is not defined
#endif
#ifndef Arch
  #error Arch is not defined
#endif
#ifndef SourceDir
  #error SourceDir is not defined
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

#define AppName "XKNX Editor"
#define AppExe "XKNX-Editor.exe"
#define ProgId "XKNXEditor.Project"
#define KnxprojProgId "XKNXEditor.KnxProject"

[Setup]
; Never change the AppId: Windows, upgrades and WinGet (ProductCode "<AppId>_is1") identify the
; installation by it.
AppId={{F249E002-17F2-4361-B61D-033E8DADE666}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=XKNX
AppPublisherURL=https://github.com/XKNX
AppSupportURL=https://github.com/XKNX/xknx-editor/issues
AppUpdatesURL=https://github.com/XKNX/xknx-editor/releases
AppCopyright=GPL-2.0-only
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
; Installs for the current user without administrator rights (in %LOCALAPPDATA%\Programs); the
; first page offers an installation for all users (Program Files, with a UAC prompt) instead.
; /CURRENTUSER and /ALLUSERS choose it on the command line.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
UsePreviousTasks=yes
#if Arch == "arm64"
ArchitecturesAllowed=arm64
ArchitecturesInstallIn64BitMode=arm64
#else
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#endif
MinVersion=10.0
ChangesAssociations=yes
; A running XKNX Editor is asked to close (Restart Manager) instead of being killed.
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=..\..\icon.ico
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
OutputDir={#OutputDir}
OutputBaseFilename=XKNX-Editor-windows-installer-{#Arch}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"

[CustomMessages]
english.StartMenuIcon=Create a Start menu entry
german.StartMenuIcon=Eintrag im Startmenü erstellen
dutch.StartMenuIcon=Snelkoppeling in het menu Start maken
english.FileAssocGroup=File association:
german.FileAssocGroup=Dateizuordnung:
dutch.FileAssocGroup=Bestandskoppeling:
english.FileAssoc=Open .xknx projects with XKNX Editor
german.FileAssoc=.xknx-Projekte mit XKNX Editor öffnen
dutch.FileAssoc=.xknx-projecten openen met XKNX Editor
english.FileTypeName=XKNX Editor Project
german.FileTypeName=XKNX Editor-Projekt
dutch.FileTypeName=XKNX Editor-project
english.FileAssocKnxproj=Open .knxproj projects with XKNX Editor (imported into a new .xknx project)
german.FileAssocKnxproj=.knxproj-Projekte mit XKNX Editor öffnen (Import in ein neues .xknx-Projekt)
dutch.FileAssocKnxproj=.knxproj-projecten openen met XKNX Editor (import in een nieuw .xknx-project)
english.KnxprojTypeName=KNX Project
german.KnxprojTypeName=KNX-Projekt
dutch.KnxprojTypeName=KNX-project
english.GraphicsGroup=Graphics:
german.GraphicsGroup=Grafik:
dutch.GraphicsGroup=Grafische weergave:
english.SoftwareGl=Software rendering (for virtual machines without a graphics driver)
german.SoftwareGl=Software-Rendering (für virtuelle Maschinen ohne Grafiktreiber)
dutch.SoftwareGl=Softwarematige weergave (voor virtuele machines zonder grafisch stuurprogramma)
english.RemoveOldInstall=An earlier installation of XKNX Editor without an uninstaller was found in:%n%n%1%n%nRemove it now? Settings, catalog and projects are kept.
german.RemoveOldInstall=Eine frühere Installation von XKNX Editor ohne Deinstallationsprogramm wurde gefunden in:%n%n%1%n%nJetzt entfernen? Einstellungen, Katalog und Projekte bleiben erhalten.
dutch.RemoveOldInstall=Er is een eerdere installatie van XKNX Editor zonder verwijderprogramma gevonden in:%n%n%1%n%nNu verwijderen? Instellingen, catalogus en projecten blijven behouden.
english.RemoveUserData=Also delete the settings, the product catalog and the caches of XKNX Editor?%n%nProjects and the local copies of projects on network drives are kept.
german.RemoveUserData=Auch die Einstellungen, den Produktkatalog und die Zwischenspeicher von XKNX Editor löschen?%n%nProjekte und die lokalen Kopien von Projekten auf Netzlaufwerken bleiben erhalten.
dutch.RemoveUserData=Ook de instellingen, de productcatalogus en de caches van XKNX Editor verwijderen?%n%nProjecten en de lokale kopieën van projecten op netwerkstations blijven behouden.

[Tasks]
Name: "startmenuicon"; Description: "{cm:StartMenuIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "fileassoc"; Description: "{cm:FileAssoc}"; GroupDescription: "{cm:FileAssocGroup}"
Name: "fileassocknxproj"; Description: "{cm:FileAssocKnxproj}"; GroupDescription: "{cm:FileAssocGroup}"; Flags: unchecked
#ifdef MesaDir
; Windows on ARM runs almost always in a virtual machine without OpenGL, so it is selected there.
#if Arch == "arm64"
Name: "softwaregl"; Description: "{cm:SoftwareGl}"; GroupDescription: "{cm:GraphicsGroup}"
#else
Name: "softwaregl"; Description: "{cm:SoftwareGl}"; GroupDescription: "{cm:GraphicsGroup}"; Flags: unchecked
#endif
#endif

[InstallDelete]
; An update replaces the whole bundle, so no file of an older version is left behind.
Type: filesandordirs; Name: "{app}\_internal"
; Choices that were deselected on an update.
Type: files; Name: "{autoprograms}\{#AppName}.lnk"; Tasks: not startmenuicon
Type: files; Name: "{autodesktop}\{#AppName}.lnk"; Tasks: not desktopicon
Type: files; Name: "{app}\opengl32.dll"
Type: files; Name: "{app}\libgallium_wgl.dll"
Type: files; Name: "{app}\software_gl.marker"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef MesaDir
Source: "{#MesaDir}\*.dll"; DestDir: "{app}"; Flags: ignoreversion; Tasks: softwaregl
Source: "software_gl.marker"; DestDir: "{app}"; Tasks: softwaregl
#endif

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startmenuicon
; The browser UI: the editor without a window, shown in the default browser.
Name: "{autoprograms}\{#AppName} (Browser)"; Filename: "{app}\{#AppExe}"; Parameters: "--web"; Tasks: startmenuicon
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.xknx"; ValueType: string; ValueName: ""; ValueData: "{#ProgId}"; Flags: uninsdeletevalue uninsdeletekeyifempty; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.xknx\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: ""; ValueData: "{cm:FileTypeName}"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: none; Flags: uninsdeletekeyifempty; Tasks: fileassoc or fileassocknxproj
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".xknx"; ValueData: ""; Flags: uninsdeletekey; Tasks: fileassoc
; .knxproj projects, opened as an import (not selected by default). The per-user or per-machine
; default set here wins over an association another app registered for all users; uninstalling
; or deselecting restores that one.
Root: HKA; Subkey: "Software\Classes\.knxproj"; ValueType: string; ValueName: ""; ValueData: "{#KnxprojProgId}"; Flags: uninsdeletevalue uninsdeletekeyifempty; Tasks: fileassocknxproj
Root: HKA; Subkey: "Software\Classes\.knxproj\OpenWithProgids"; ValueType: string; ValueName: "{#KnxprojProgId}"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty; Tasks: fileassocknxproj
Root: HKA; Subkey: "Software\Classes\{#KnxprojProgId}"; ValueType: string; ValueName: ""; ValueData: "{cm:KnxprojTypeName}"; Flags: uninsdeletekey; Tasks: fileassocknxproj
Root: HKA; Subkey: "Software\Classes\{#KnxprojProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: fileassocknxproj
Root: HKA; Subkey: "Software\Classes\{#KnxprojProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: fileassocknxproj
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".knxproj"; ValueData: ""; Flags: uninsdeletevalue; Tasks: fileassocknxproj
; An association deselected on an update. The extension's default is left alone when another app
; took it over meanwhile.
Root: HKA; Subkey: "Software\Classes\.xknx"; ValueType: none; ValueName: ""; Flags: deletevalue; Tasks: not fileassoc; Check: ExtensionOpensWithUs('.xknx', '{#ProgId}')
Root: HKA; Subkey: "Software\Classes\{#KnxprojProgId}"; ValueType: none; Flags: deletekey; Tasks: not fileassocknxproj
Root: HKA; Subkey: "Software\Classes\.knxproj\OpenWithProgids"; ValueType: none; ValueName: "{#KnxprojProgId}"; Flags: deletevalue; Tasks: not fileassocknxproj
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: none; ValueName: ".knxproj"; Flags: deletevalue; Tasks: not fileassocknxproj
Root: HKA; Subkey: "Software\Classes\.knxproj"; ValueType: none; ValueName: ""; Flags: deletevalue; Tasks: not fileassocknxproj; Check: ExtensionOpensWithUs('.knxproj', '{#KnxprojProgId}')
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: none; Flags: deletekey; Tasks: not fileassoc
Root: HKA; Subkey: "Software\Classes\.xknx\OpenWithProgids"; ValueType: none; ValueName: "{#ProgId}"; Flags: deletevalue; Tasks: not fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: none; ValueName: ".xknx"; Flags: deletevalue; Tasks: not fileassoc

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; A silent update started by the app (/RELAUNCH=1, see self_update.py) starts it again, with the
; project that was open (/OPEN=<path>).
Filename: "{app}\{#AppExe}"; Parameters: "{code:RelaunchParameters}"; Flags: nowait runasoriginaluser; Check: RelaunchAfterUpdate

[Code]
const
  // The folder the former install.bat copied the app to, always for all users.
  OldBatchDir = '{commonpf64}\XKNX-Editor';

// The former install.bat left no uninstaller: offer to remove its folder and desktop shortcut.
// Its folder is in Program Files, so a per-user installation asks for administrator rights once;
// a silent per-user installation cannot ask and leaves it alone.
procedure RemoveOldBatchInstall;
var
  Dir: string;
  ResultCode: Integer;
begin
  Dir := ExpandConstant(OldBatchDir);
  if not FileExists(Dir + '\{#AppExe}') then
    Exit;
  if CompareText(Dir, ExpandConstant('{app}')) = 0 then
    Exit;
  if not IsAdminInstallMode and WizardSilent then
    Exit;
  if SuppressibleMsgBox(FmtMessage(CustomMessage('RemoveOldInstall'), [Dir]), mbConfirmation,
    MB_YESNO, IDYES) <> IDYES then
    Exit;
  if IsAdminInstallMode then
    DelTree(Dir, True, True, True)
  else
    ShellExec('runas', ExpandConstant('{cmd}'), '/c rd /s /q "' + Dir + '"', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode);
  DeleteFile(ExpandConstant('{userdesktop}\XKNX-Editor.lnk'));
end;

function ExtensionOpensWithUs(Extension, OurProgId: string): Boolean;
var
  ProgId: string;
begin
  Result := RegQueryStringValue(HKA, 'Software\Classes\' + Extension, '', ProgId) and
    (CompareText(ProgId, OurProgId) = 0);
end;

function RelaunchAfterUpdate: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:RELAUNCH|0}') = '1');
end;

function RelaunchParameters(Param: string): string;
begin
  Result := ExpandConstant('{param:OPEN|}');
  if Result <> '' then
    Result := AddQuotes(Result);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RemoveOldBatchInstall;
end;

// Deletes the app's data folder except the local copies of projects on network drives.
procedure RemoveUserData;
var
  Dir: string;
  FindRec: TFindRec;
begin
  Dir := ExpandConstant('{localappdata}\xknx-editor\xknx-editor');
  if FindFirst(Dir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') and
          (CompareText(FindRec.Name, 'mirrors') <> 0) then
        begin
          if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
            DelTree(Dir + '\' + FindRec.Name, True, True, True)
          else
            DeleteFile(Dir + '\' + FindRec.Name);
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  // Only removed when they are empty now.
  RemoveDir(Dir);
  RemoveDir(ExpandConstant('{localappdata}\xknx-editor'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox(CustomMessage('RemoveUserData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      RemoveUserData;
end;

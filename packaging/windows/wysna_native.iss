#ifndef BundleDir
  #error BundleDir must point to a verified Windows Native SDK directory
#endif
#ifndef WysnaVersion
  #error WysnaVersion must identify the packaged compiler
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#ifndef WysnaCompression
  #define WysnaCompression "lzma2/fast"
#endif

[Setup]
AppId={{C4D65A38-F6F1-4D83-8C0B-5F5A21DE9012}
AppName=Wysna Language (Native SDK)
AppVersion={#WysnaVersion}
AppPublisher=WySofia S.A.S.
AppPublisherURL=https://wysna.wysofia.com/
DefaultDirName={localappdata}\Programs\Wysna
DefaultGroupName=Wysna Language
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableDirPage=no
DisableProgramGroupPage=yes
ChangesEnvironment=yes
Compression={#WysnaCompression}
#if WysnaCompression == "none"
SolidCompression=no
#else
SolidCompression=yes
#endif
OutputDir={#OutputDir}
#ifdef OutputBaseName
OutputBaseFilename={#OutputBaseName}
#else
OutputBaseFilename=wysna-{#WysnaVersion}-native-x86_64-windows-setup
#endif
UninstallDisplayIcon={app}\bin\wysna.exe
LicenseFile={#BundleDir}\LICENSE
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: english; MessagesFile: compiler:Default.isl
Name: spanish; MessagesFile: compiler:Languages\Spanish.isl

[CustomMessages]
english.AddToPath=Add Wysna (bin\) to my terminal PATH (recommended)
english.OpenTerminal=Open a terminal ready to use Wysna
spanish.AddToPath=Agregar Wysna (bin\) al PATH de mi terminal (recomendado)
spanish.OpenTerminal=Abrir una terminal lista para usar Wysna

[Tasks]
Name: addtopath; Description: {cm:AddToPath}

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Wysna Language"; Filename: "{cmd}"; Parameters: "/K """"{app}\open-terminal.cmd"""""; WorkingDir: "{userdocs}"
Name: "{group}\Uninstall Wysna Language"; Filename: "{uninstallexe}"

[Run]
Filename: "{cmd}"; Parameters: "/K """"{app}\open-terminal.cmd"""""; WorkingDir: "{userdocs}"; Description: "{cm:OpenTerminal}"; Flags: postinstall nowait skipifsilent

[Messages]
english.FinishedLabel=Wysna Native SDK is installed with its self-hosted compiler and embedded Zig C toolchain.%n%nOpen any terminal (PowerShell or cmd) and run: wysna --version%n%nNo Python or Visual Studio installation is required.
spanish.FinishedLabel=El SDK Nativo de Wysna quedó instalado con su compilador self-hosted y toolchain Zig integrado.%n%nAbre cualquier terminal (PowerShell o cmd) y ejecuta: wysna --version%n%nNo requiere Python ni Visual Studio.

[Code]
const
  InstallerKey = 'Software\Wysna\NativeInstaller';

function NormalizeEntry(Value: String): String;
begin
  Value := Trim(Value);
  if (Length(Value) > 1) and (Value[1] = '"') and (Value[Length(Value)] = '"') then
    Value := Copy(Value, 2, Length(Value) - 2);
  while (Length(Value) > 3) and (Value[Length(Value)] = '\') do
    Delete(Value, Length(Value), 1);
  Result := Lowercase(Value);
end;

function FilterPath(Value, Entry: String; Remove: Boolean; var Found: Boolean): String;
var
  Item, Delimiter: String;
  Separator: Integer;
begin
  Result := '';
  Delimiter := '';
  Found := False;
  repeat
    Separator := Pos(';', Value);
    if Separator = 0 then begin
      Item := Value;
      Value := '';
    end else begin
      Item := Copy(Value, 1, Separator - 1);
      Delete(Value, 1, Separator);
    end;
    if NormalizeEntry(Item) = NormalizeEntry(Entry) then begin
      Found := True;
      if not Remove then begin
        Result := Result + Delimiter + Item;
        Delimiter := ';';
      end;
    end else begin
      Result := Result + Delimiter + Item;
      Delimiter := ';';
    end;
  until Separator = 0;
end;

procedure RemoveOwnedPath;
var
  Owned, Current, Updated: String;
  Found: Boolean;
begin
  if RegQueryStringValue(HKCU, InstallerKey, 'PathEntry', Owned) then begin
    if RegQueryStringValue(HKCU, 'Environment', 'Path', Current) then begin
      Updated := FilterPath(Current, Owned, True, Found);
      if Found and not RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Updated) then
        Log('Could not remove the owned Wysna PATH entry.');
    end;
    RegDeleteValue(HKCU, InstallerKey, 'PathEntry');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Current, Entry, Unused: String;
  Found: Boolean;
begin
  if CurStep = ssPostInstall then begin
    RemoveOwnedPath;
    if WizardIsTaskSelected('addtopath') then begin
      Entry := ExpandConstant('{app}\bin');
      RegQueryStringValue(HKCU, 'Environment', 'Path', Current);
      Unused := FilterPath(Current, Entry, False, Found);
      if not Found then begin
        if Current <> '' then Current := Current + ';';
        if not RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Current + Entry) then
          RaiseException('Cannot update the user PATH.');
        if not RegWriteStringValue(HKCU, InstallerKey, 'PathEntry', Entry) then
          RaiseException('Cannot record the Wysna PATH entry.');
      end;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then RemoveOwnedPath;
end;

# Builds the Windows installer XKNX-Editor-windows-installer-<Arch>.exe from a PyInstaller output
# folder with Inno Setup (xknx-editor.iss next to this script). Used by the desktop-build workflow
# and for local builds:
#
#   powershell -File apps/editor-gui/packaging/windows/build_installer.ps1 -Arch x64 `
#     -SourceDir apps/editor-gui/dist/XKNX-Editor -MesaDir mesa -OutputDir .
#
# -MesaDir is optional: a folder with Mesa's software OpenGL DLLs (opengl32.dll, and
# libgallium_wgl.dll when the build has one), offered as the "Software rendering" task.
# Inno Setup's compiler is taken from $env:ISCC, the PATH or its default install folders; when none
# is found, the pinned version below is downloaded, verified and installed for the current user.
param(
  [Parameter(Mandatory = $true)][ValidateSet("x64", "arm64")][string]$Arch,
  [Parameter(Mandatory = $true)][string]$SourceDir,
  [string]$MesaDir = "",
  [string]$OutputDir = "."
)
$ErrorActionPreference = "Stop"

$InnoVersion = "6.7.3"
$InnoSha256 = "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732"
$InnoUrl = "https://github.com/jrsoftware/issrc/releases/download/is-$($InnoVersion -replace '\.', '_')/innosetup-$InnoVersion.exe"

function Find-Iscc {
  if ($env:ISCC -and (Test-Path $env:ISCC)) { return $env:ISCC }
  $onPath = Get-Command iscc.exe -ErrorAction SilentlyContinue
  if ($onPath) { return $onPath.Source }
  foreach ($dir in @("${env:ProgramFiles(x86)}\Inno Setup 6", "$env:ProgramFiles\Inno Setup 6",
      "$env:LOCALAPPDATA\Programs\Inno Setup 6")) {
    if ($dir -and (Test-Path "$dir\ISCC.exe")) { return "$dir\ISCC.exe" }
  }
  return $null
}

function Install-Inno {
  $download = Join-Path ([IO.Path]::GetTempPath()) "innosetup-$InnoVersion.exe"
  Write-Host "Downloading Inno Setup $InnoVersion"
  Invoke-WebRequest -Uri $InnoUrl -OutFile $download -UseBasicParsing
  $hash = (Get-FileHash -Algorithm SHA256 $download).Hash.ToLowerInvariant()
  if ($hash -ne $InnoSha256) { throw "Inno Setup download has SHA-256 $hash, expected $InnoSha256" }
  $target = "$env:LOCALAPPDATA\Programs\Inno Setup 6"
  $p = Start-Process -FilePath $download -Wait -PassThru -ArgumentList @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/NOICONS", "/DIR=`"$target`"")
  if ($p.ExitCode -ne 0) { throw "Inno Setup setup failed with exit code $($p.ExitCode)" }
  return "$target\ISCC.exe"
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$init = Join-Path $here "..\..\src\editor_gui\__init__.py"
$match = Select-String -Path $init -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $match) { throw "no __version__ in $init" }
$version = $match.Matches[0].Groups[1].Value

$source = (Resolve-Path $SourceDir).Path
if (-not (Test-Path (Join-Path $source "XKNX-Editor.exe"))) { throw "XKNX-Editor.exe not found in $source" }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$output = (Resolve-Path $OutputDir).Path

$iscc = Find-Iscc
if (-not $iscc) { $iscc = Install-Inno }

$defines = @("/DAppVersion=$version", "/DArch=$Arch", "/DSourceDir=$source", "/DOutputDir=$output")
if ($MesaDir) {
  $mesa = (Resolve-Path $MesaDir).Path
  if (-not (Test-Path (Join-Path $mesa "opengl32.dll"))) { throw "opengl32.dll not found in $mesa" }
  $defines += "/DMesaDir=$mesa"
}
Write-Host "Building the XKNX Editor $version installer ($Arch) with $iscc"
& $iscc /Qp @defines (Join-Path $here "xknx-editor.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }
Get-Item (Join-Path $output "XKNX-Editor-windows-installer-$Arch.exe") | Format-List Name, Length

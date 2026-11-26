# Copyright 2026 WySofia S.A.S. (Apache-2.0)
# Wysna one-line installer for Windows (PowerShell 5.1+ and 7+).
# Usage:
#   irm https://wysna.wysofia.com/install.ps1 | iex
# Optional overrides (env vars or parameters):
#   WYSNA_VERSION=v1.0.0   WYSNA_DIST=native|seed   WYSNA_INSTALL_DIR=...
#   WYSNA_NO_PATH=1        WYSNA_UNINSTALL=1

[CmdletBinding()]
param(
    [string]$Version = $(if ($env:WYSNA_VERSION) { $env:WYSNA_VERSION } else { "latest" }),
    [ValidateSet("native", "seed")]
    [string]$Distribution = $(if ($env:WYSNA_DIST) { $env:WYSNA_DIST } else { "native" }),
    [string]$InstallDir = $(if ($env:WYSNA_INSTALL_DIR) { $env:WYSNA_INSTALL_DIR } else { "" }),
    [switch]$NoPath = ($env:WYSNA_NO_PATH -eq "1"),
    [switch]$Uninstall = ($env:WYSNA_UNINSTALL -eq "1")
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = if ($env:WYSNA_REPO) { $env:WYSNA_REPO } else { "WashingtonYandun/Wysna" }
$BaseUrlOverride = if ($env:WYSNA_BASE_URL) { $env:WYSNA_BASE_URL.TrimEnd("/") } else { "" }

if (-not $InstallDir) {
    $LocalAppData = [Environment]::GetFolderPath("LocalApplicationData")
    if (-not $LocalAppData) { $LocalAppData = $env:LOCALAPPDATA }
    $InstallDir = Join-Path $LocalAppData "Programs\Wysna"
}

function Normalize-PathEntry {
    param([string]$Entry)
    return $Entry.Trim().Trim('"').TrimEnd('\').ToLowerInvariant()
}

function Update-UserPath {
    param(
        [string]$BinDir,
        [switch]$Remove
    )
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($null -eq $current) { $current = "" }
    $targetNorm = Normalize-PathEntry $BinDir
    $parts = @()
    $found = $false
    foreach ($item in ($current -split ";")) {
        $trimmed = $item.Trim()
        if (-not $trimmed) { continue }
        if ((Normalize-PathEntry $trimmed) -eq $targetNorm) {
            $found = $true
            if (-not $Remove) {
                $parts += $trimmed
            }
        } else {
            $parts += $trimmed
        }
    }
    if (-not $Remove -and -not $found) {
        $parts += $BinDir
    }
    $updated = ($parts -join ";")
    if ($updated -ne $current) {
        [Environment]::SetEnvironmentVariable("Path", $updated, "User")
    }
    if (-not $Remove) {
        $procNorms = ($env:Path -split ";") | ForEach-Object { Normalize-PathEntry $_ }
        if ($procNorms -notcontains $targetNorm) {
            $env:Path = "$BinDir;$env:Path"
        }
    }
}

function Resolve-LatestTag {
    $latestUrl = "https://github.com/$Repo/releases/latest"
    try {
        $req = [System.Net.HttpWebRequest]::Create($latestUrl)
        $req.Method = "HEAD"
        $req.AllowAutoRedirect = $false
        $req.Timeout = 15000
        $resp = $req.GetResponse()
        $loc = $resp.Headers["Location"]
        $resp.Close()
        if ($loc -match "/releases/tag/([^/?#]+)") {
            return $Matches[1]
        }
    } catch [System.Net.WebException] {
        $resp = $_.Exception.Response
        if ($resp -ne $null) {
            $loc = $resp.Headers["Location"]
            $resp.Close()
            if ($loc -match "/releases/tag/([^/?#]+)") {
                return $Matches[1]
            }
        }
    }
    $apiHeaders = @{ "User-Agent" = "wysna-installer/1.0" }
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers $apiHeaders -TimeoutSec 15
    if (-not $release.tag_name) {
        throw "Could not resolve latest Wysna release tag from GitHub."
    }
    return [string]$release.tag_name
}

function Get-ExpectedChecksum {
    param(
        [string]$SumsPath,
        [string]$AssetName
    )
    foreach ($line in (Get-Content -Path $SumsPath -Encoding UTF8)) {
        if ($line -match "^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$") {
            if ($Matches[2] -eq $AssetName) {
                return $Matches[1].ToLowerInvariant()
            }
        }
    }
    return ""
}

if ($Uninstall) {
    Write-Host "[Wysna] Uninstalling from $InstallDir..."
    Update-UserPath -BinDir (Join-Path $InstallDir "bin") -Remove
    Update-UserPath -BinDir $InstallDir -Remove
    if (Test-Path $InstallDir) {
        Remove-Item -Path $InstallDir -Recurse -Force
    }
    Write-Host "[Wysna] Uninstalled cleanly."
    exit 0
}

$arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
if ($arch -ne "X64" -and $env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
    throw "Unsupported Windows architecture ($arch). Wysna currently provides x86_64 builds."
}

$tag = if ($Version -eq "latest") { Resolve-LatestTag } elseif ($Version.StartsWith("v")) { $Version } else { "v$Version" }
$verNum = $tag.TrimStart("v")

if ($Distribution -eq "native") {
    $assetName = "wysna-$verNum-native-x86_64-windows.zip"
    $rootSubdir = "wysna-native"
    $binSubdir = "bin"
} else {
    $assetName = "wysna-windows-x64-portable.zip"
    $rootSubdir = ""
    $binSubdir = ""
}

$downloadBase = if ($BaseUrlOverride) { $BaseUrlOverride } else { "https://github.com/$Repo/releases/download/$tag" }
$assetUrl = "$downloadBase/$assetName"
$sumsUrl = "$downloadBase/SHA256SUMS"

$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("wysna-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

try {
    $archivePath = Join-Path $tmpDir $assetName
    $sumsPath = Join-Path $tmpDir "SHA256SUMS"
    $extractPath = Join-Path $tmpDir "extract"

    Write-Host "[Wysna] Installing $tag ($Distribution, windows-x64)..."
    Write-Host "[Wysna] Downloading $assetName..."
    Invoke-WebRequest -Uri $assetUrl -OutFile $archivePath -UseBasicParsing
    Invoke-WebRequest -Uri $sumsUrl -OutFile $sumsPath -UseBasicParsing

    $expectedHash = Get-ExpectedChecksum -SumsPath $sumsPath -AssetName $assetName
    if (-not $expectedHash) {
        throw "Checksum entry for '$assetName' not found in SHA256SUMS."
    }
    $actualHash = (Get-FileHash -Path $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "SHA-256 verification failed for $assetName (expected $expectedHash, got $actualHash)."
    }
    Write-Host "[Wysna] SHA-256 verified."

    Expand-Archive -Path $archivePath -DestinationPath $extractPath -Force
    $payloadDir = if ($rootSubdir -and (Test-Path (Join-Path $extractPath $rootSubdir))) {
        Join-Path $extractPath $rootSubdir
    } else {
        $extractPath
    }

    if (Test-Path $InstallDir) {
        Remove-Item -Path $InstallDir -Recurse -Force
    }
    $parentDir = Split-Path -Parent $InstallDir
    if ($parentDir) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }
    Move-Item -Path $payloadDir -Destination $InstallDir

    $binDir = if ($binSubdir) { Join-Path $InstallDir $binSubdir } else { $InstallDir }
    $exePath = Join-Path $binDir "wysna.exe"
    if (-not (Test-Path $exePath)) {
        throw "Installation incomplete: $exePath not found."
    }

    if (-not $NoPath) {
        Update-UserPath -BinDir $binDir
    }

    $verOut = & $exePath --version 2>&1
    Write-Host ""
    Write-Host "[OK] Installed $verOut"
    Write-Host "     Location : $InstallDir"
    Write-Host "     Binary   : $exePath"
    if (-not $NoPath) {
        Write-Host "     PATH     : Added $binDir to user PATH (open a new terminal if needed)."
    }
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  wysna project new hello"
    Write-Host "  cd hello && wysna project run"
} finally {
    if (Test-Path $tmpDir) {
        Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

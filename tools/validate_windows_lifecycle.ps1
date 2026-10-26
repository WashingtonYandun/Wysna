param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$ReportDirectory,
    [Parameter(Mandatory = $true)][string]$UpgradeFrom
)
$ErrorActionPreference = 'Stop'
$installerPath = (Resolve-Path $Installer).Path
$repoRoot = Split-Path $PSScriptRoot -Parent
$uninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{E7DFAD44-C0AF-447A-BD57-39A0498CA07A}_is1'
if ([Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($uninstallKey)) {
    throw 'This lifecycle test requires a disposable Windows account without an existing Wysna installer.'
}
New-Item -ItemType Directory -Force $ReportDirectory | Out-Null
$reportRoot = (Resolve-Path $ReportDirectory).Path
$workspace = Join-Path ([System.IO.Path]::GetTempPath()) ('wysna lifecycle á ' + [guid]::NewGuid())
$installRoot = Join-Path $workspace 'installed Wysna with spaces'
$unrelatedDirectory = Join-Path $workspace 'unrelated tools'
$project = Join-Path $workspace 'user project.wysna'
New-Item -ItemType Directory -Force $unrelatedDirectory | Out-Null
Set-Content -LiteralPath $project -Value 'print(42)' -Encoding utf8
$environmentKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
$originalPath = $environmentKey.GetValue('Path', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
$originalKind = if ($null -ne $originalPath) { $environmentKey.GetValueKind('Path') } else { $null }
$baseline = [string]$originalPath + ';' + $unrelatedDirectory + ';'
$environmentKey.SetValue('Path', $baseline, [Microsoft.Win32.RegistryValueKind]::ExpandString)
$installed = $false

function Run-Installer([string]$Name, [string[]]$ExtraArguments = @()) {
    $log = Join-Path $reportRoot ($Name + '.log')
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                   "/DIR=`"$installRoot`"", "/LOG=`"$log`"") + $ExtraArguments
    $process = Start-Process -FilePath $installerPath -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "$Name failed: $($process.ExitCode). See $log" }
}

function Get-UserPath {
    return $environmentKey.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
}

function Uninstall-Wysna([string]$Name) {
    $log = Join-Path $reportRoot ($Name + '.log')
    $process = Start-Process -FilePath (Join-Path $installRoot 'unins000.exe') `
        -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$log`"") -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "$Name failed: $($process.ExitCode). See $log" }
}

try {
    # A synthetic older installer tests the upgrade path; it is never published.
    $candidateInstaller = $installerPath
    $installerPath = (Resolve-Path $UpgradeFrom).Path
    Run-Installer 'previous-version-fixture' @('/TASKS=addtopath')
    $installed = $true
    $installerPath = $candidateInstaller
    Run-Installer 'install' @('/TASKS=addtopath')
    $installed = $true
    python (Join-Path $repoRoot 'tools/validate_windows_install.py') --installation $installRoot --report (Join-Path $reportRoot 'installed.json')
    if ($LASTEXITCODE -ne 0) { throw 'The installed compiler failed its isolated client checks.' }
    python (Join-Path $repoRoot 'tools/validate_distribution.py') --installation $installRoot --report (Join-Path $reportRoot 'complete-installed.json')
    if ($LASTEXITCODE -ne 0) { throw 'The installed product failed complete tool acceptance.' }
    python (Join-Path $repoRoot 'tools/validate_installed_application.py') --installation $installRoot --report (Join-Path $reportRoot 'application.json')
    if ($LASTEXITCODE -ne 0) { throw 'The installed product failed the reference application.' }
    $installedPath = Get-UserPath
    if ($installedPath -cne ($baseline + ';' + $installRoot)) { throw 'Installation changed unrelated PATH content.' }

    # Reinstall repairs missing files without duplicating PATH or damaging projects.
    Remove-Item -LiteralPath (Join-Path $installRoot 'stdlib/text.wysna')
    Run-Installer 'repair' @('/TASKS=addtopath')
    if ((Get-UserPath) -cne $installedPath) { throw 'Repair duplicated or changed PATH.' }
    & (Join-Path $installRoot 'wysna.exe') check $project
    if ($LASTEXITCODE -ne 0) { throw 'Repair did not preserve a working compiler.' }
    if (!(Test-Path (Join-Path $installRoot 'stdlib/text.wysna'))) { throw 'Repair did not restore the standard library.' }
    Uninstall-Wysna 'uninstall'
    $installed = $false
    if ((Get-UserPath) -cne $baseline) { throw 'Uninstall removed unrelated PATH entries.' }
    if (!(Test-Path $project)) { throw 'Uninstall removed a user project.' }
    if (Test-Path (Join-Path $installRoot 'wysna.exe')) { throw 'Uninstall left the installed command behind.' }

    # An entry added by the user must survive installation and uninstallation.
    $preexisting = $baseline + ';' + $installRoot
    $environmentKey.SetValue('Path', $preexisting, [Microsoft.Win32.RegistryValueKind]::ExpandString)
    Run-Installer 'preexisting-path-install' @('/TASKS=addtopath')
    $installed = $true
    if ((Get-UserPath) -cne $preexisting) { throw 'A preexisting PATH entry was duplicated.' }
    Uninstall-Wysna 'preexisting-path-uninstall'
    $installed = $false
    if ((Get-UserPath) -cne $preexisting) { throw 'An unowned PATH entry was removed.' }

    # Opting out must leave PATH untouched while direct execution still works.
    $environmentKey.SetValue('Path', $baseline, [Microsoft.Win32.RegistryValueKind]::ExpandString)
    Run-Installer 'no-path-install' @('/TASKS=')
    $installed = $true
    if ((Get-UserPath) -cne $baseline) { throw 'The PATH opt-out was ignored.' }
    & (Join-Path $installRoot 'wysna.exe') version
    if ($LASTEXITCODE -ne 0) { throw 'Direct execution failed without PATH registration.' }
    Uninstall-Wysna 'no-path-uninstall'
    $installed = $false
    if ((Get-UserPath) -cne $baseline) { throw 'Uninstall changed PATH after opting out.' }
    @{ passed = $true; install = $true; repair = $true; upgrade_from_synthetic_fixture = $true; uninstall = $true; projects_preserved = $true;
       owned_path_removed = $true; unowned_path_preserved = $true; path_opt_out = $true } |
        ConvertTo-Json | Set-Content (Join-Path $reportRoot 'lifecycle.json') -Encoding utf8
} finally {
    try {
        if ($installed -and (Test-Path (Join-Path $installRoot 'unins000.exe'))) {
            Uninstall-Wysna 'cleanup'
        }
    } finally {
        if ($null -eq $originalPath) {
            $environmentKey.DeleteValue('Path', $false)
        } else {
            $environmentKey.SetValue('Path', $originalPath, $originalKind)
        }
        $environmentKey.Close()
        Remove-Item -LiteralPath $workspace -Recurse -Force -ErrorAction SilentlyContinue
    }
}

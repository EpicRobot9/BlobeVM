#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Update a game in the EpicVM shared library with atomic swap + rollback.

.DESCRIPTION
    Safety model: the EpicVMGames$ share is read-only to guests, so VMs can
    never observe a half-written update. This script stages the new version
    beside the live one, validates it, then swaps atomically and updates
    catalog.json in one replace. One rollback generation is retained.

.PARAMETER GameId
    Catalog id of the game to update (e.g. openttd).

.PARAMETER SourcePath
    Directory containing the NEW version content (copied to staging).

.PARAMETER NewVersion
    Version string recorded in the catalog (e.g. 15.1).

.PARAMETER ExeRelativePath
    Relative path (from the version dir) of the launch executable, used for
    staging validation. Defaults to the current catalog entry's exe tail.

.PARAMETER Rollback
    Restore the retained .old generation instead of updating.

.EXAMPLE
    ./Update-EpicVMSharedGame.ps1 -GameId openttd -SourcePath D:\dl\openttd-15.1 -NewVersion 15.1
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $GameId,
    [string] $SourcePath,
    [string] $NewVersion,
    [string] $ExeRelativePath,
    [switch] $Rollback
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$library = 'E:\EpicVM\shared-games'
$catalogPath = Join-Path $library 'catalog.json'
$gamesRoot = Join-Path $library 'games'

$catalog = Get-Content $catalogPath -Raw | ConvertFrom-Json
$game = $catalog.games | Where-Object { $_.id -eq $GameId } | Select-Object -First 1
if (-not $game) { throw "GameId '$GameId' not found in catalog." }
$liveDir = Join-Path $gamesRoot $GameId
if (-not (Test-Path $liveDir)) { throw "Live dir missing: $liveDir" }
$liveVersionDir = Get-ChildItem $liveDir -Directory | Where-Object { $_.Name -notlike '.*' -and $_.Name -notlike '*-staging' } | Select-Object -First 1
if (-not $liveVersionDir) { throw "No live version directory under $liveDir" }

if ($Rollback) {
    $oldDir = Get-ChildItem $liveDir -Directory -Force | Where-Object { $_.Name -like '.old-*' } | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $oldDir) { throw "No rollback generation retained for '$GameId'." }
    $current = $liveVersionDir.Name
    Rename-Item $liveVersionDir.FullName -NewName ".retired-$(Get-Date -Format yyyyMMddHHmmss)"
    Rename-Item $oldDir.FullName -NewName $current
    $game.version = [string]($oldDir.Name -replace '^\.old-', '' -replace '-\d{14}$', '')
    $catalog.updated = (Get-Date).ToString('o')
    $tmp = "$catalogPath.tmp"
    $catalog | ConvertTo-Json -Depth 8 | Set-Content $tmp -Encoding UTF8
    Move-Item $tmp $catalogPath -Force
    Write-Output "ROLLBACK complete: '$GameId' restored to $($game.version) (retired dir kept)."
    return
}

if (-not $SourcePath) { throw 'SourcePath is required for updates.' }
if (-not (Test-Path $SourcePath)) { throw "SourcePath not found: $SourcePath" }
if (-not $NewVersion) { throw 'NewVersion is required for updates.' }

# 1. stage
$staging = Join-Path $liveDir "$NewVersion-staging"
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
robocopy $SourcePath $staging /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Staging copy failed (robocopy exit $LASTEXITCODE)." }

# 2. validate
if (-not $ExeRelativePath) {
    $oldExe = [string]$game.exe
    $ExeRelativePath = Split-Path $oldExe -Leaf
}
$stagedExe = Join-Path $staging $ExeRelativePath
if (-not (Test-Path $stagedExe)) { throw "Staged content invalid: $stagedExe missing." }

# 3. atomic swap (same volume => rename is atomic)
$newDirName = $liveVersionDir.Name -replace [regex]::Escape($game.version), $NewVersion
if ($newDirName -eq $liveVersionDir.Name) { $newDirName = "$($liveVersionDir.Name)-$NewVersion" }
Rename-Item $liveVersionDir.FullName -NewName ".old-$($game.version)-$(Get-Date -Format yyyyMMddHHmmss)"
Rename-Item $staging -NewName $newDirName
$newLive = Join-Path $liveDir $newDirName

# 4. catalog update (single replace)
$game.version = $NewVersion
$game.sharedLibraryPath = $newLive
$game.exe = Join-Path $newLive $ExeRelativePath
$catalog.updated = (Get-Date).ToString('o')
$tmp = "$catalogPath.tmp"
$catalog | ConvertTo-Json -Depth 8 | Set-Content $tmp -Encoding UTF8
Move-Item $tmp $catalogPath -Force

Write-Output "UPDATE complete: '$GameId' now $NewVersion at $newLive (rollback generation retained)."

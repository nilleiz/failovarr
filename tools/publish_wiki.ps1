[CmdletBinding()]
param(
    [string]$Repository = "nilleiz/failovarr"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param(
        [Parameter(Mandatory)] [string]$Command,
        [string[]]$Arguments = @()
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE."
    }
}

function Restore-EnvironmentValue {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [AllowNull()] [string]$Value
    )

    if ($null -eq $Value) {
        Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
    }
    else {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$wikiDirectory = $null
$locationDepth = 0
$oldConfigCount = $env:GIT_CONFIG_COUNT
$oldConfigKey = $env:GIT_CONFIG_KEY_0
$oldConfigValue = $env:GIT_CONFIG_VALUE_0

try {
    Push-Location $repositoryRoot
    $locationDepth++

    $branch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
        throw "Wiki publication requires a clean checkout on main."
    }
    $workingTree = & git status --porcelain
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read the Git working-tree status."
    }
    if ($null -ne $workingTree -and $workingTree.Trim()) {
        throw "Wiki publication requires a clean checkout."
    }

    Invoke-Checked git @("fetch", "origin", "main", "--quiet")
    $head = (& git rev-parse HEAD).Trim()
    $remoteMain = (& git rev-parse origin/main).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -ne $remoteMain) {
        throw "Wiki publication requires main to match origin/main."
    }
    $origin = (& git remote get-url origin).Trim()
    if ($LASTEXITCODE -ne 0 -or $origin -notmatch 'github\.com[:/]nilleiz/failovarr(?:\.git)?$') {
        throw "Wiki publication must run from the canonical nilleiz/failovarr checkout."
    }
    if ($Repository -ne "nilleiz/failovarr") {
        throw "Only the canonical nilleiz/failovarr Wiki can be published by this script."
    }

    & gh auth status --hostname github.com
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI authentication is required to publish the Wiki."
    }
    $token = (& gh auth token --hostname github.com).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
        throw "Could not obtain a local GitHub CLI token."
    }

    $managedPages = @(
        "Home", "Features", "Getting-Started", "Settings-Walkthrough",
        "Deployment-Modes", "Storage-Backends", "First-Sync-and-Initialization",
        "Operations-and-Planned-Handoff", "Troubleshooting",
        "Security-and-Limitations", "_Sidebar"
    )
    $wikiSource = Join-Path $repositoryRoot "docs/wiki"
    foreach ($page in $managedPages) {
        $source = Join-Path $wikiSource "$page.md"
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Required canonical Wiki page is missing: $page.md"
        }
    }

    $wikiDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("failovarr-wiki-" + [guid]::NewGuid().ToString("N"))
    $authorization = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("x-access-token:$token"))
    $env:GIT_CONFIG_COUNT = "1"
    $env:GIT_CONFIG_KEY_0 = "http.extraheader"
    $env:GIT_CONFIG_VALUE_0 = "AUTHORIZATION: basic $authorization"
    Invoke-Checked git @("clone", "--quiet", "https://github.com/${Repository}.wiki.git", $wikiDirectory)

    foreach ($page in $managedPages) {
        $target = Join-Path $wikiDirectory "$page.md"
        Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        Copy-Item -LiteralPath (Join-Path $wikiSource "$page.md") -Destination $target -Force
    }

    Push-Location $wikiDirectory
    $locationDepth++
    & git diff --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Output "Failovarr Wiki is already synchronized."
        return
    }
    if ($LASTEXITCODE -ne 1) {
        throw "Could not inspect the Wiki working-tree diff."
    }

    $managedFiles = $managedPages | ForEach-Object { "$_.md" }
    Invoke-Checked git (@("add", "--") + $managedFiles)
    Invoke-Checked git @("config", "user.name", "Failovarr Wiki Publisher")
    Invoke-Checked git @("config", "user.email", "failovarr-wiki@users.noreply.github.com")
    Invoke-Checked git @("commit", "-m", "docs: synchronize canonical Wiki pages")
    Invoke-Checked git @("push", "origin", "HEAD")
    Write-Output "Published canonical Failovarr Wiki pages."
}
finally {
    while ($locationDepth -gt 0) {
        Pop-Location
        $locationDepth--
    }
    Restore-EnvironmentValue -Name "GIT_CONFIG_COUNT" -Value $oldConfigCount
    Restore-EnvironmentValue -Name "GIT_CONFIG_KEY_0" -Value $oldConfigKey
    Restore-EnvironmentValue -Name "GIT_CONFIG_VALUE_0" -Value $oldConfigValue
    $token = $null
    $authorization = $null
    if ($wikiDirectory -and (Test-Path -LiteralPath $wikiDirectory)) {
        $temporaryRoot = [System.IO.Path]::GetTempPath()
        if ($wikiDirectory.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $wikiDirectory -Recurse -Force
        }
    }
}

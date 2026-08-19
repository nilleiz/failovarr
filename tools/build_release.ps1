param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$pluginDirectory = Join-Path $repoRoot "failovarr"
$manifest = Get-Content -Raw (Join-Path $pluginDirectory "plugin.json") | ConvertFrom-Json
if (-not $Version) {
    $Version = $manifest.version
}
if ($Version -ne $manifest.version) {
    throw "Requested version $Version does not match plugin.json version $($manifest.version)"
}

$vendorArchive = Join-Path (Join-Path $pluginDirectory "vendor") "remote_storage.zip"
$loaderPath = Join-Path $pluginDirectory "vendor_loader.py"
$actualVendorHash = (Get-FileHash -Algorithm SHA256 $vendorArchive).Hash.ToLowerInvariant()
$loader = Get-Content -Raw $loaderPath
if (-not $loader.Contains($actualVendorHash)) {
    throw "vendor_loader.py does not contain the current vendor archive SHA-256"
}

$distDirectory = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force $distDirectory | Out-Null
$archivePath = Join-Path $distDirectory "failovarr-$Version.zip"
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$sourceFiles = @(Get-ChildItem -LiteralPath $pluginDirectory -Recurse -File | Where-Object {
    $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -notin @('.pyc', '.pyo')
})
$output = [System.IO.Compression.ZipFile]::Open(
    $archivePath,
    [System.IO.Compression.ZipArchiveMode]::Create
)
try {
    foreach ($source in $sourceFiles) {
        # ZIP entry names are always POSIX-style. Using GetRelativePath avoids
        # the Windows-only URI separator logic that made this builder fail on
        # GitHub's Linux runner.
        $entryName = [IO.Path]::GetRelativePath($repoRoot, $source.FullName).Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $output,
            $source.FullName,
            $entryName,
            [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
} finally {
    $output.Dispose()
}

$archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $files = @($archive.Entries | Where-Object { $_.Name })
    $uncompressedBytes = ($files | Measure-Object -Property Length -Sum).Sum
    $hasManifest = $files.FullName -contains "failovarr/plugin.json"
    $hasEntryPoint = $files.FullName -contains "failovarr/__init__.py"
    if (-not $hasManifest -or -not $hasEntryPoint) {
        throw "Release ZIP does not contain the expected top-level plugin package"
    }
    if ($files.Count -gt 2000) {
        throw "Release ZIP exceeds Dispatcharr's 2,000-file import limit"
    }
    if ($uncompressedBytes -gt 200MB) {
        throw "Release ZIP exceeds Dispatcharr's 200 MB uncompressed import limit"
    }
} finally {
    $archive.Dispose()
}

$archiveHash = (Get-FileHash -Algorithm SHA256 $archivePath).Hash.ToLowerInvariant()
$checksumPath = "$archivePath.sha256"
Set-Content -LiteralPath $checksumPath -NoNewline -Encoding ascii -Value "$archiveHash  $(Split-Path -Leaf $archivePath)"
Write-Host "Release: $archivePath"
Write-Host "Checksum: $checksumPath"
Write-Host "Files: $($files.Count)"
Write-Host "Uncompressed bytes: $uncompressedBytes"
Write-Host "SHA-256: $archiveHash"

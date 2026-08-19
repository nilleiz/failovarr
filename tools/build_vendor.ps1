param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$buildPath = Join-Path $repoRoot ".vendor-build"
$archivePath = Join-Path $repoRoot "failovarr\vendor\remote_storage.zip"
$requirements = Join-Path $repoRoot "requirements-vendor.txt"

if (Test-Path -LiteralPath $buildPath) {
    $resolvedBuild = (Resolve-Path -LiteralPath $buildPath).Path
    $expectedBuild = [IO.Path]::GetFullPath($buildPath)
    if ($resolvedBuild -ne $expectedBuild -or -not $resolvedBuild.StartsWith($repoRoot)) {
        throw "Refusing to remove unexpected vendor build path: $resolvedBuild"
    }
    Remove-Item -LiteralPath $resolvedBuild -Recurse -Force
}

New-Item -ItemType Directory -Path $buildPath | Out-Null
& $Python -m pip install --no-deps --no-compile --target $buildPath -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Vendor dependency installation failed"
}

$binPath = Join-Path $buildPath "bin"
if (Test-Path -LiteralPath $binPath) {
    Remove-Item -LiteralPath $binPath -Recurse -Force
}
New-Item -ItemType Directory -Force (Split-Path -Parent $archivePath) | Out-Null
Compress-Archive -Path (Join-Path $buildPath "*") -DestinationPath $archivePath -CompressionLevel Optimal -Force

$hash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Vendor archive: $archivePath"
Write-Host "SHA-256: $hash"
Write-Host "Update VENDOR_ARCHIVE_SHA256 in failovarr/vendor_loader.py if it changed."

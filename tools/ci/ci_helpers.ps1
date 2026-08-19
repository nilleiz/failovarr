Set-StrictMode -Version Latest

function Get-CiRepositoryRoot {
    return Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

function Invoke-CiDocker {
    & docker @args
    if ($LASTEXITCODE -ne 0) {
        throw "docker command failed with exit code $LASTEXITCODE"
    }
}

function Assert-CiProjectName {
    param([string]$ProjectName)

    if ($ProjectName -notlike "failovarr-ci-*") {
        throw "Refusing a non-CI Compose project: $ProjectName"
    }
}

function Get-CiContainerId {
    param(
        [string]$ProjectName,
        [string]$ComposeFile,
        [string]$Service
    )

    $containerId = (& docker compose -p $ProjectName -f $ComposeFile ps -q $Service).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $containerId) {
        throw "Could not resolve CI service '$Service' in $ProjectName"
    }
    return $containerId
}

function Wait-CiDispatcharrReady {
    param([string]$ContainerId)

    for ($attempt = 1; $attempt -le 60; $attempt++) {
        & docker exec $ContainerId sh -lc "pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1"
        $postgresReady = $LASTEXITCODE -eq 0
        if ($postgresReady) {
            & docker exec -w /app $ContainerId /dispatcharrpy/bin/python manage.py shell -c `
                "from apps.plugins.models import PluginConfig; PluginConfig.objects.exists()" *> $null
            if ($LASTEXITCODE -eq 0) {
                $httpStatus = (& docker exec $ContainerId sh -lc "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9191/").Trim()
                if ($LASTEXITCODE -eq 0 -and $httpStatus -notin @("000", "502", "503")) {
                    Write-Host "FAILOVARR_CI_READY=$ContainerId"
                    return
                }
            }
        }
        Start-Sleep -Seconds 2
    }
    throw "Dispatcharr container $ContainerId did not become ready within 120 seconds"
}

function Get-CiPluginArchive {
    param([string]$RepositoryRoot)

    $manifestPath = Join-Path (Join-Path $RepositoryRoot "failovarr") "plugin.json"
    $manifest = Get-Content -Raw $manifestPath | ConvertFrom-Json
    $archive = Join-Path (Join-Path $RepositoryRoot "dist") "failovarr-$($manifest.version).zip"
    if (-not (Test-Path -LiteralPath $archive)) {
        throw "Missing CI release archive: $archive"
    }
    return $archive
}

function Install-CiPlugin {
    param(
        [string]$ContainerId,
        [string]$ArchivePath
    )

    Invoke-CiDocker cp $ArchivePath "${ContainerId}:/tmp/failovarr.zip"
    $installCommand = "from apps.plugins.api_views import _install_plugin_from_zip; from apps.plugins.loader import PluginManager; from apps.plugins.models import PluginConfig; f=open('/tmp/failovarr.zip','rb'); r=_install_plugin_from_zip(f,PluginManager.get().plugins_dir,file_name='failovarr.zip',allow_overwrite=True); f.close(); assert r.get('success'),r; pm=PluginManager.get(); pm.discover_plugins(force_reload=True); c=PluginConfig.objects.get(key='failovarr'); c.enabled=True; c.ever_enabled=True; c.settings={'setup_public_url':'http://127.0.0.1:9192','confirm':True}; c.save(); pm.discover_plugins(force_reload=True); print('FAILOVARR_CI_PLUGIN='+c.version)"
    Invoke-CiDocker exec -e "DJANGO_SECRET_KEY=ci-install-only-not-a-production-secret" `
        -w /app $ContainerId /dispatcharrpy/bin/python manage.py shell -c $installCommand
    Invoke-CiDocker exec $ContainerId chown -R dispatch:dispatch /data/plugins/failovarr
}

function Prepare-CiLegacyMigrationConfig {
    param([string]$ContainerId)

    # Seed a legacy 0.6.x-style file as the unprivileged Dispatcharr user.
    # The following ZIP install/discovery intentionally executes as root.
    $seedCommand = 'umask 077; printf ''%s\n'' ''{"node_id":"legacy-slave","role":"follower","state_path":"/data/legacy-state"}'' > /data/dispatcharr-redundancy-config.json'
    Invoke-CiDocker exec -u dispatch $ContainerId sh -lc `
        $seedCommand
}

function Prepare-CiPluginStorage {
    param([string]$ContainerId)

    Invoke-CiDocker exec $ContainerId sh -lc "mkdir -p /data/redundancy /data/failovarr-state; chown -R dispatch:dispatch /data/redundancy /data/failovarr-state"
}

function Protect-CiDiagnosticLine {
    param([string]$Line)

    $redacted = [regex]::Replace(
        $Line,
        '(?i)(["'']?[A-Za-z0-9_.-]*(?:password|secret|token|api[_-]?key)[A-Za-z0-9_.-]*["'']?\s*[:=]\s*)(?:"[^"]*"|''[^'']*''|[^,\s}\]]+)',
        '$1<redacted>'
    )
    return [regex]::Replace(
        $redacted,
        '(?i)([?&][^?&\s=]*(?:password|secret|token|api[_-]?key)[^?&\s=]*=)[^&#\s"'']+',
        '$1<redacted>'
    )
}

function Write-CiDiagnostics {
    param(
        [string]$ProjectName,
        [string]$ComposeFile,
        [string[]]$DiagnosticContainerIds = @()
    )

    Assert-CiProjectName $ProjectName
    Write-Host "FAILOVARR_CI_DIAGNOSTICS_BEGIN=$ProjectName"
    $lines = @(& docker compose -p $ProjectName -f $ComposeFile logs --no-color --timestamps 2>&1)
    foreach ($line in @($lines | Select-Object -Last 400)) {
        Write-Host (Protect-CiDiagnosticLine ([string]$line))
    }
    foreach ($containerId in $DiagnosticContainerIds) {
        $serviceLines = @(& docker exec $containerId sh -lc "test -f /data/failovarr-state/ci-background-service.json && cat /data/failovarr-state/ci-background-service.json" 2>&1)
        foreach ($line in $serviceLines) {
            Write-Host (Protect-CiDiagnosticLine ("CI_BACKGROUND_SERVICE[$containerId] " + [string]$line))
        }
    }
    Write-Host "FAILOVARR_CI_DIAGNOSTICS_END=$ProjectName"
}

function Start-CiLab {
    param(
        [string]$ProjectName,
        [string]$ComposeFile
    )

    Assert-CiProjectName $ProjectName
    Invoke-CiDocker compose -p $ProjectName -f $ComposeFile config --quiet
    Invoke-CiDocker compose -p $ProjectName -f $ComposeFile down -v --remove-orphans
    Invoke-CiDocker compose -p $ProjectName -f $ComposeFile up -d
}

function Stop-CiLab {
    param(
        [string]$ProjectName,
        [string]$ComposeFile
    )

    Assert-CiProjectName $ProjectName
    Invoke-CiDocker compose -p $ProjectName -f $ComposeFile down -v --remove-orphans
}

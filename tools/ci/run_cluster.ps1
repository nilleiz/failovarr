$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "ci_helpers.ps1")

$repoRoot = Get-CiRepositoryRoot
$composeFile = Join-Path (Join-Path $repoRoot "testbed") "ci-cluster-compose.yml"
$probeFile = Join-Path (Join-Path $repoRoot "testbed") "integration_probe.py"
$buildScript = Join-Path (Join-Path $repoRoot "tools") "build_release.ps1"
$projectName = "failovarr-ci-cluster"

function Invoke-Probe {
    param([string]$ContainerId, [string]$Action, [string]$Role = "")
    $environment = @("-e", "FAILOVARR_TEST_ACTION=$Action")
    if ($Role) { $environment += @("-e", "FAILOVARR_TEST_ROLE=$Role") }
    Invoke-CiDocker exec -u dispatch @environment -w /app $ContainerId `
        /dispatcharrpy/bin/python manage.py shell -c "exec(open('/tmp/integration_probe.py').read())"
}

function Get-CiReadinessResponse {
    param([string]$ContainerId)

    # The management server is a background thread. Query the real public
    # readiness endpoint. Retain a redacted, bounded response body for a
    # failed assertion rather than hiding the reason behind an HTTP code.
    $raw = @(& docker exec $ContainerId sh -lc "curl -sS --connect-timeout 2 -w '\n%{http_code}' http://127.0.0.1:9192/v1/readiness || true")
    $lines = @(([string]::Join("`n", $raw)).Trim() -split "`n")
    $code = if ($lines.Count) { $lines[-1].Trim() } else { "no-response" }
    $body = if ($lines.Count -gt 1) { ($lines[0..($lines.Count - 2)] -join "`n").Trim() } else { "" }
    $safeBody = Protect-CiDiagnosticLine $body
    [pscustomobject]@{
        Code = if ($code) { $code } else { "no-response" }
        Body = $safeBody.Substring(0, [Math]::Min(400, $safeBody.Length))
    }
}

try {
    & $buildScript
    $archive = Get-CiPluginArchive $repoRoot

    Start-CiLab $projectName $composeFile
    $main = Get-CiContainerId $projectName $composeFile "main"
    $slave = Get-CiContainerId $projectName $composeFile "slave"
    Wait-CiDispatcharrReady $main
    Wait-CiDispatcharrReady $slave
    Install-CiPlugin $main $archive
    Prepare-CiLegacyMigrationConfig $slave
    Install-CiPlugin $slave $archive
    Prepare-CiPluginStorage $main
    Prepare-CiPluginStorage $slave
    Invoke-CiDocker cp $probeFile "${main}:/tmp/integration_probe.py"
    Invoke-CiDocker cp $probeFile "${slave}:/tmp/integration_probe.py"
    Invoke-Probe $slave "legacy_config_migration_verify"

    # Autostart must be won by a durable uWSGI worker, never a short-lived
    # Celery child. Restart the real synthetic containers and wait beyond the
    # Redis lease TTL before inspecting owner command and startup sync state.
    Invoke-Probe $main "prepare_cold_autostart" "leader"
    Invoke-Probe $slave "prepare_cold_autostart" "follower"
    Invoke-CiDocker restart $main
    Invoke-CiDocker restart $slave
    Wait-CiDispatcharrReady $main
    Wait-CiDispatcharrReady $slave
    Start-Sleep -Seconds 42
    Invoke-Probe $main "cold_autostart_status" "leader"
    Invoke-Probe $slave "cold_autostart_status" "follower"
    Invoke-Probe $main "stop_current_service"
    Invoke-Probe $slave "stop_current_service"
    Invoke-Probe $slave "slow_cold_start_lease"

    Invoke-Probe $main "inspect"
    Invoke-Probe $slave "inspect"
    Invoke-Probe $main "prepare_main"
    Invoke-Probe $slave "prepare_slave"
    Invoke-Probe $main "export"
    Invoke-Probe $slave "tamper_rejected"
    Invoke-Probe $slave "preview_apply_verify"
    Invoke-Probe $main "prepare_graph_main"
    Invoke-Probe $slave "prepare_graph_slave"
    Invoke-Probe $main "export_graph"
    Invoke-Probe $slave "client_identity_mismatch"
    Invoke-Probe $slave "apply_graph_verify"
    # A Follower may intentionally keep DVR settings local. Initialization
    # must filter Main's complete Settings bundle before replacing selected
    # rows, otherwise PostgreSQL rejects the untouched unique dvr_settings key.
    Invoke-Probe $main "prepare_core_scope_main"
    Invoke-Probe $slave "initialize_core_scope_verify"

    Invoke-CiDocker exec -u dispatch -d -e FAILOVARR_TEST_ACTION=serve_direct -w /app $main `
        /dispatcharrpy/bin/python manage.py shell -c "exec(open('/tmp/integration_probe.py').read())"
    Start-Sleep -Seconds 2
    Invoke-Probe $slave "direct_apply_verify"
    Invoke-Probe $main "singleton_probe"
    $unauthenticated = (& docker exec $slave sh -lc "curl -s -o /dev/null -w '%{http_code}' http://main:9192/v1/latest").Trim()
    if ($LASTEXITCODE -ne 0 -or $unauthenticated -ne "401") {
        throw "Unauthenticated direct request was not rejected with HTTP 401"
    }
    Invoke-Probe $main "save_export_cross_worker"
    # The Assistant is a container-owned helper in 0.6.4 and deliberately
    # remains healthy after the replication loop stops.
    $assistantHealthy = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $directHealth = (& docker exec $main sh -lc "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 1 http://127.0.0.1:9192/v1/health || true").Trim()
        if ($directHealth -eq "200") {
            $assistantHealthy = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $assistantHealthy) {
        throw "Setup Assistant helper was not healthy after cross-worker service reload"
    }

    Invoke-Probe $main "prepare_handoff_main"
    Invoke-Probe $slave "prepare_handoff_slave"
    Invoke-CiDocker exec -u dispatch -d -e FAILOVARR_TEST_ACTION=serve_handoff_main -w /app $main `
        /dispatcharrpy/bin/python manage.py shell -c "exec(open('/tmp/integration_probe.py').read())"
    Invoke-CiDocker exec -u dispatch -d -e FAILOVARR_TEST_ACTION=serve_handoff_slave -w /app $slave `
        /dispatcharrpy/bin/python manage.py shell -c "exec(open('/tmp/integration_probe.py').read())"
    Start-Sleep -Seconds 2
    Invoke-Probe $main "request_handoff_verify"

    $mainReadiness = "no-response"
    $slaveReadiness = "no-response"
    $mainReadinessBody = ""
    $slaveReadinessBody = ""
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        $mainResponse = Get-CiReadinessResponse $main
        $slaveResponse = Get-CiReadinessResponse $slave
        $mainReadiness = $mainResponse.Code
        $slaveReadiness = $slaveResponse.Code
        $mainReadinessBody = $mainResponse.Body
        $slaveReadinessBody = $slaveResponse.Body
        if ($mainReadiness -eq "503" -and $slaveReadiness -eq "200") {
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if ($mainReadiness -ne "503" -or $slaveReadiness -ne "200") {
        throw "Readiness did not change to main=503 and slave=200 after handoff (observed main=$mainReadiness body=$mainReadinessBody; slave=$slaveReadiness body=$slaveReadinessBody)"
    }
    Write-Host "FAILOVARR_CI_RESULT=cluster:success"
}
catch {
    Write-Host "FAILOVARR_CI_RESULT=cluster:failed"
    Write-CiDiagnostics $projectName $composeFile @($main, $slave)
    throw
}
finally {
    Stop-CiLab $projectName $composeFile
}

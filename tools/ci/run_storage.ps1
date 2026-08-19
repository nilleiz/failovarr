$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "ci_helpers.ps1")

$repoRoot = Get-CiRepositoryRoot
$composeFile = Join-Path (Join-Path $repoRoot "testbed") "ci-storage-compose.yml"
$probeFile = Join-Path (Join-Path $repoRoot "testbed") "storage_probe.py"
$actionProbeFile = Join-Path (Join-Path $repoRoot "testbed") "storage_action_probe.py"
$buildScript = Join-Path (Join-Path $repoRoot "tools") "build_release.ps1"
$projectName = "failovarr-ci-storage"

try {
    & $buildScript
    $archive = Get-CiPluginArchive $repoRoot

    Start-CiLab $projectName $composeFile
    $node = Get-CiContainerId $projectName $composeFile "node"
    $sftp = Get-CiContainerId $projectName $composeFile "sftp"
    Wait-CiDispatcharrReady $node
    Install-CiPlugin $node $archive

    $hostKey = (& docker exec $sftp sh -c "cat /etc/ssh/ssh_host_ed25519_key.pub").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $hostKey.StartsWith("ssh-ed25519 ")) {
        throw "Could not read the disposable SFTP host key"
    }
    Invoke-CiDocker cp $probeFile "${node}:/tmp/storage_probe.py"
    Invoke-CiDocker exec -e "PYTHONPATH=/data/plugins" -e "LAB_SFTP_HOST_KEY=$hostKey" `
        -w /app $node /dispatcharrpy/bin/python /tmp/storage_probe.py
    Prepare-CiPluginStorage $node

    $accountCommand = "from django.contrib.auth import get_user_model; U=get_user_model(); u,_=U.objects.get_or_create(username='storagelab',defaults={'email':'storage-lab@example.invalid','is_staff':True,'is_superuser':True,'user_level':10}); u.is_staff=True; u.is_superuser=True; u.user_level=10; u.set_password('storage-lab-only-password'); u.save()"
    Invoke-CiDocker exec -w /app $node /dispatcharrpy/bin/python manage.py shell -c $accountCommand
    $pluginCommand = "from apps.plugins.loader import PluginManager; from apps.plugins.models import PluginConfig; pm=PluginManager.get(); pm.discover_plugins(force_reload=True); c=PluginConfig.objects.get(key='failovarr'); c.enabled=True; c.ever_enabled=True; c.save(); pm.discover_plugins(force_reload=True)"
    Invoke-CiDocker exec -w /app $node /dispatcharrpy/bin/python manage.py shell -c $pluginCommand
    Invoke-CiDocker restart $node
    Wait-CiDispatcharrReady $node

    Invoke-CiDocker cp $actionProbeFile "${node}:/tmp/storage_action_probe.py"
    Invoke-CiDocker exec -u dispatch -e "LAB_DISPATCHARR_URL=http://127.0.0.1:9191" `
        $node /dispatcharrpy/bin/python /tmp/storage_action_probe.py
    Write-Host "FAILOVARR_CI_RESULT=storage:success"
}
catch {
    Write-Host "FAILOVARR_CI_RESULT=storage:failed"
    Write-CiDiagnostics $projectName $composeFile
    throw
}
finally {
    Stop-CiLab $projectName $composeFile
}

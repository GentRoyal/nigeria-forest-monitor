param(
    [string]$OutputDirectory = "backups"
)

$ErrorActionPreference = "Stop"
$resolvedRoot = (Resolve-Path ".").Path
$targetDirectory = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($resolvedRoot, $OutputDirectory))
if (-not $targetDirectory.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Backup directory must remain inside the repository."
}

New-Item -ItemType Directory -Force -Path $targetDirectory | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$fileName = "forest-monitor-$stamp.dump"
$containerPath = "/tmp/$fileName"
$hostPath = Join-Path $targetDirectory $fileName

docker compose exec -T postgres pg_dump --username=postgres --dbname=forest_monitor --format=custom --no-owner --file=$containerPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose cp "postgres:$containerPath" $hostPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose exec -T postgres rm -f $containerPath

Write-Host "Backup created: $hostPath"

param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath
)

$ErrorActionPreference = "Stop"
$resolvedRoot = (Resolve-Path ".").Path
$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path
if (-not $resolvedBackup.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Backup must be a file inside the repository."
}
if ([System.IO.Path]::GetExtension($resolvedBackup) -ne ".dump") {
    throw "Expected a PostgreSQL .dump file."
}

$confirmation = Read-Host "This overwrites the LOCAL forest_monitor database. Type RESTORE LOCAL DATABASE"
if ($confirmation -cne "RESTORE LOCAL DATABASE") {
    throw "Restore cancelled."
}

$containerPath = "/tmp/forest-monitor-restore.dump"
docker compose cp $resolvedBackup "postgres:$containerPath"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose exec -T postgres pg_restore --username=postgres --dbname=forest_monitor --clean --if-exists --no-owner $containerPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose exec -T postgres rm -f $containerPath
.venv\Scripts\python.exe -m alembic -c apps\api\alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Local product database restored and migrated. Run scripts\check.ps1 next."

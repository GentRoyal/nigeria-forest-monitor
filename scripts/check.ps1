$ErrorActionPreference = "Stop"

.venv\Scripts\python.exe -m compileall -q packages\forest_monitor\src packages\forest_monitor\tests apps\api apps\tiles apps\orchestrator
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
.venv\Scripts\ruff.exe check apps packages\forest_monitor\src\forest_monitor\config.py packages\forest_monitor\src\forest_monitor\runtime.py packages\forest_monitor\tests\test_core.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
.venv\Scripts\ruff.exe format --check apps packages\forest_monitor\src\forest_monitor\config.py packages\forest_monitor\src\forest_monitor\runtime.py packages\forest_monitor\tests\test_core.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm run check:web
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

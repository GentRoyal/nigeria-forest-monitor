$ErrorActionPreference = "Stop"

if (-not (Test-Path .venv\Scripts\python.exe)) {
    python -m venv .venv
}

.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pip install -r apps\api\requirements-dev.txt
.venv\Scripts\python.exe -m pip install -r apps\tiles\requirements-dev.txt
npm install

# forest_monitor

Reusable geospatial analysis code for the Nigeria Forest Monitor platform.

Install it from the monorepo root during development:

```powershell
.venv\Scripts\python.exe -m pip install -e . --no-deps
```

The package exposes the legacy analytical CLI as `forest-monitor` and
`python -m forest_monitor.pipeline`. New platform workflows should call package
functions from Airflow tasks rather than import notebook state.

# Orchestrator

Airflow owns technical execution of batch workflows while FastAPI and the
product PostgreSQL database remain authoritative for user-visible state.

`nfm_system_smoke` remains a manual import check. `nfm_schedule_coordinator`
runs every 15 minutes and calls FastAPI's scheduler-only endpoint to create
idempotent discovery jobs for the configured local organisation. It does not
create one DAG per site and never writes product tables directly.

Do not create one DAG per site. Product schedules are database records and feed
parameterised DAG runs.

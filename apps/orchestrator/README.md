# Orchestrator

Airflow owns technical execution of batch workflows while FastAPI and the
product PostgreSQL database remain authoritative for user-visible state.

Phase 2 includes a manual smoke DAG proving the shared package is importable.
Imagery discovery and parameterised processing DAGs are introduced with their
authenticated API contracts in Phase 5.

Do not create one DAG per site. Product schedules are database records and feed
parameterised DAG runs.

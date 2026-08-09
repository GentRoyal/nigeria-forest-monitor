from __future__ import annotations

from datetime import UTC, datetime

from airflow.sdk import dag, task


@dag(
    dag_id="nfm_system_smoke",
    description="Verify that Airflow can load the shared monitoring package.",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    tags=["nfm", "system"],
)
def system_smoke():
    @task
    def verify_shared_package() -> dict[str, str]:
        from forest_monitor.config import load_config

        config = load_config()
        return {
            "project": str(config["project"]["name"]),
            "config_path": str(config["_meta"]["config_path"]),
        }

    verify_shared_package()


system_smoke()

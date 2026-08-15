from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from urllib.request import Request, urlopen

from airflow.sdk import dag, task


@dag(
    dag_id="nfm_schedule_coordinator",
    description="Create duplicate-safe scheduled discovery jobs through the product API.",
    schedule="*/15 * * * *",
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    tags=["nfm", "monitoring"],
)
def schedule_coordinator():
    @task
    def create_due_jobs() -> dict[str, int]:
        api_url = os.environ["NFM_API_INTERNAL_URL"].rstrip("/")
        request = Request(
            f"{api_url}/internal/v1/schedules/run-due",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Scheduler-Token": os.environ["NFM_SCHEDULER_SERVICE_TOKEN"],
                "X-Organisation-ID": os.environ["NFM_SCHEDULER_ORGANISATION_ID"],
            },
            method="POST",
        )
        with urlopen(request, timeout=30) as response:  # nosec B310: configured internal API URL
            return json.loads(response.read().decode("utf-8"))

    create_due_jobs()


schedule_coordinator()

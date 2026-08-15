from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from urllib.request import Request, urlopen

from airflow.sdk import dag, task


def _api(path: str, *, method: str = "GET", body: dict | None = None, headers: dict | None = None) -> dict:
    request = Request(
        f"{os.environ['NFM_API_INTERNAL_URL'].rstrip('/')}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


@dag(dag_id="nfm_stac_discovery", description="Discover Sentinel-2 candidates for one product job.", schedule=None, start_date=datetime(2026, 1, 1, tzinfo=UTC), catchup=False, tags=["nfm", "discovery", "stac"])
def stac_discovery():
    @task
    def discover(dag_run=None) -> dict[str, int]:
        job_id = (dag_run.conf if dag_run else {})["job_id"]
        service = {"X-Worker-Token": os.environ["NFM_WORKER_SERVICE_TOKEN"], "X-Organisation-ID": os.environ["NFM_SCHEDULER_ORGANISATION_ID"]}
        claim = _api(f"/internal/v1/jobs/{job_id}/claim", method="POST", body={"worker_identity": "airflow-stac-discovery"}, headers=service)["data"]
        headers = {**service, "X-Job-Lease-Token": claim["lease_token"]}
        inputs = _api(f"/internal/v1/jobs/{job_id}/input", headers=headers)
        end = datetime.now(UTC)
        cursor = inputs["site"].get("last_discovery_cursor")
        start = datetime.fromisoformat(cursor) if cursor else end - timedelta(days=60)
        search = Request(f"{os.environ['NFM_STAC_API_URL'].rstrip('/')}/search", data=json.dumps({"collections": ["sentinel-2-l2a"], "intersects": inputs["site"]["boundary"], "datetime": f"{start.isoformat()}/{end.isoformat()}", "limit": 20}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(search, timeout=90) as response:
            items = json.loads(response.read().decode()).get("features", [])
        for item in items:
            props = item.get("properties", {})
            _api("/internal/v1/catalogue-items/upsert", method="POST", headers=headers, body={"job_id": job_id, "provider": "planetary-computer", "collection": item["collection"], "source_identifier": item["id"], "acquired_at": props["datetime"], "footprint": item["geometry"], "assets": item.get("assets", {}), "licence": props.get("license", "provider licence"), "attribution": "Microsoft Planetary Computer STAC", "source_metadata": {"stac_id": item["id"]}})
        _api(f"/internal/v1/jobs/{job_id}/heartbeat", method="POST", headers=headers, body={"progress": 90, "stage": "discovery"})
        schedule_id = inputs["site"].get("schedule_id")
        if schedule_id:
            _api(f"/internal/v1/schedules/{schedule_id}/discovery-cursor", method="POST", headers=headers, body={"job_id": job_id, "cursor": end.isoformat()})
        return {"catalogue_items": len(items)}

    discover()


stac_discovery()

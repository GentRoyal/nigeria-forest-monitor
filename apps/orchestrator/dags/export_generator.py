from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
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


@dag(dag_id="nfm_export_generator", description="Generate one authorised local CSV or GeoJSON export.", schedule=None, start_date=datetime(2026, 1, 1, tzinfo=UTC), catchup=False, tags=["nfm", "exports"])
def export_generator():
    @task
    def generate(dag_run=None) -> dict[str, str]:
        conf = dag_run.conf if dag_run else {}
        job_id, export_id = conf["job_id"], conf["export_id"]
        service = {"X-Worker-Token": os.environ["NFM_WORKER_SERVICE_TOKEN"], "X-Organisation-ID": os.environ["NFM_SCHEDULER_ORGANISATION_ID"]}
        claim = _api(f"/internal/v1/jobs/{job_id}/claim", method="POST", body={"worker_identity": "airflow-export-generator"}, headers=service)["data"]
        headers = {**service, "X-Job-Lease-Token": claim["lease_token"]}
        export = _api(f"/internal/v1/exports/{export_id}/input?job_id={job_id}", headers=headers)
        suffix = "geojson" if export["export_type"] == "geojson" else "csv"
        relative_key = f"{export_id}.{suffix}"
        output = Path(os.environ["NFM_EXPORT_ROOT"]) / relative_key
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = export["rows"]
        if suffix == "geojson":
            features = [{"type": "Feature", "geometry": row.pop("geometry", None), "properties": row} for row in rows]
            output.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
        else:
            fields = sorted({key for row in rows for key in row})
            with output.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: json.dumps(value) if isinstance(value, dict | list) else value for key, value in row.items()})
        checksum = hashlib.sha256(output.read_bytes()).hexdigest()
        _api(f"/internal/v1/exports/{export_id}/complete", method="POST", headers=headers, body={"job_id": job_id, "result_object_key": relative_key, "checksum": checksum, "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat()})
        return {"export_id": export_id, "object_key": relative_key}

    generate()


export_generator()

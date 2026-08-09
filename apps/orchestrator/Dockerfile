FROM apache/airflow:3.3.0-python3.11

COPY --chown=airflow:root pyproject.toml README.md /opt/nfm/
COPY --chown=airflow:root packages/forest_monitor /opt/nfm/packages/forest_monitor

USER airflow
RUN pip install --no-cache-dir --no-deps -e /opt/nfm

COPY --chown=airflow:root apps/orchestrator/dags /opt/airflow/dags

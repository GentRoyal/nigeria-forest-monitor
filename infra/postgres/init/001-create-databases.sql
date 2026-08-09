CREATE USER forest_monitor WITH PASSWORD 'forest_monitor';
CREATE DATABASE forest_monitor OWNER forest_monitor;

CREATE USER airflow WITH PASSWORD 'airflow';
CREATE DATABASE airflow OWNER airflow;

\connect forest_monitor
CREATE EXTENSION IF NOT EXISTS postgis;

\connect airflow
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO airflow;

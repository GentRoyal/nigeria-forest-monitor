#!/bin/sh
set -eu

psql_base="psql --host=postgres --username=postgres --dbname=postgres --set=ON_ERROR_STOP=1"

if ! $psql_base --tuples-only --no-align --command "SELECT 1 FROM pg_roles WHERE rolname='forest_monitor'" | grep -q 1; then
  $psql_base --command "CREATE ROLE forest_monitor LOGIN PASSWORD 'forest_monitor'"
fi
if ! $psql_base --tuples-only --no-align --command "SELECT 1 FROM pg_database WHERE datname='forest_monitor'" | grep -q 1; then
  $psql_base --command "CREATE DATABASE forest_monitor OWNER forest_monitor"
fi
if ! $psql_base --tuples-only --no-align --command "SELECT 1 FROM pg_roles WHERE rolname='airflow'" | grep -q 1; then
  $psql_base --command "CREATE ROLE airflow LOGIN PASSWORD 'airflow'"
fi
if ! $psql_base --tuples-only --no-align --command "SELECT 1 FROM pg_database WHERE datname='airflow'" | grep -q 1; then
  $psql_base --command "CREATE DATABASE airflow OWNER airflow"
fi

psql --host=postgres --username=postgres --dbname=forest_monitor --set=ON_ERROR_STOP=1 --command "CREATE EXTENSION IF NOT EXISTS postgis"
psql --host=postgres --username=postgres --dbname=airflow --set=ON_ERROR_STOP=1 --command "GRANT ALL ON SCHEMA public TO airflow"

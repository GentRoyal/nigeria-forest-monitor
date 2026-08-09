"""Create the Phase 3 PostgreSQL/PostGIS foundation."""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_phase3"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_SQL = r"""
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE FUNCTION app_current_organisation_id() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT NULLIF(current_setting('app.current_organisation_id', true), '')::uuid
$$;
CREATE FUNCTION app_current_user_id() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid
$$;

CREATE TABLE workspace_templates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version integer NOT NULL UNIQUE CHECK (version > 0),
  name text NOT NULL,
  defaults jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE organisations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  slug text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','closed')),
  workspace_template_version integer NOT NULL REFERENCES workspace_templates(version),
  default_timezone text NOT NULL DEFAULT 'Africa/Lagos',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE departments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL REFERENCES organisations(id),
  name text NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,name)
);
CREATE TABLE user_profiles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL REFERENCES organisations(id),
  primary_department_id uuid NOT NULL,
  email citext NOT NULL,
  display_name text NOT NULL,
  role text NOT NULL CHECK (role IN ('owner','administrator','analyst','verification_officer','viewer')),
  status text NOT NULL DEFAULT 'invited' CHECK (status IN ('invited','active','suspended','disabled','expired')),
  timezone text NOT NULL DEFAULT 'Africa/Lagos',
  invited_at timestamptz,
  activated_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,email),
  FOREIGN KEY (organisation_id,primary_department_id) REFERENCES departments(organisation_id,id)
);
CREATE TABLE teams (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  department_id uuid NOT NULL,
  name text NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,department_id,name),
  FOREIGN KEY (organisation_id,department_id) REFERENCES departments(organisation_id,id)
);
CREATE TABLE team_memberships (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  team_id uuid NOT NULL,
  user_id uuid NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
  created_by uuid REFERENCES user_profiles(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,team_id,user_id),
  FOREIGN KEY (organisation_id,team_id) REFERENCES teams(organisation_id,id),
  FOREIGN KEY (organisation_id,user_id) REFERENCES user_profiles(organisation_id,id)
);
CREATE FUNCTION enforce_team_membership_department() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM teams t JOIN user_profiles u ON u.organisation_id=t.organisation_id
    WHERE t.id=NEW.team_id AND u.id=NEW.user_id
      AND t.organisation_id=NEW.organisation_id AND t.department_id=u.primary_department_id
  ) THEN RAISE EXCEPTION 'team membership must remain inside the user department'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER team_membership_department_guard BEFORE INSERT OR UPDATE ON team_memberships
FOR EACH ROW EXECUTE FUNCTION enforce_team_membership_department();

CREATE TABLE auth_credentials (
  user_id uuid PRIMARY KEY,
  organisation_id uuid NOT NULL,
  password_hash text NOT NULL,
  hash_version smallint NOT NULL DEFAULT 1 CHECK (hash_version > 0),
  password_changed_at timestamptz NOT NULL DEFAULT now(),
  failed_attempts integer NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
  locked_until timestamptz,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','locked','disabled')),
  FOREIGN KEY (organisation_id,user_id) REFERENCES user_profiles(organisation_id,id)
);
CREATE TABLE auth_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  user_id uuid NOT NULL,
  token_family_id uuid NOT NULL,
  refresh_token_hash text NOT NULL UNIQUE,
  replaced_by_session_id uuid REFERENCES auth_sessions(id),
  user_agent text,
  ip_address inet,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  last_activity_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  revoke_reason text,
  CHECK (expires_at > created_at),
  FOREIGN KEY (organisation_id,user_id) REFERENCES user_profiles(organisation_id,id)
);
CREATE TABLE invitations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  department_id uuid NOT NULL,
  email citext NOT NULL,
  role text NOT NULL CHECK (role IN ('owner','administrator','analyst','verification_officer','viewer')),
  token_hash text NOT NULL UNIQUE,
  invited_by uuid NOT NULL,
  expires_at timestamptz NOT NULL,
  accepted_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  FOREIGN KEY (organisation_id,department_id) REFERENCES departments(organisation_id,id),
  FOREIGN KEY (organisation_id,invited_by) REFERENCES user_profiles(organisation_id,id)
);
CREATE UNIQUE INDEX invitations_one_open_email ON invitations(organisation_id,email)
WHERE accepted_at IS NULL AND revoked_at IS NULL;
CREATE TABLE password_reset_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  user_id uuid NOT NULL,
  token_hash text NOT NULL UNIQUE,
  requested_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  invalidated_at timestamptz,
  request_ip inet,
  CHECK (expires_at > requested_at),
  FOREIGN KEY (organisation_id,user_id) REFERENCES user_profiles(organisation_id,id)
);

CREATE TABLE sites (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  managing_department_id uuid NOT NULL,
  name text NOT NULL,
  slug text NOT NULL,
  description text,
  origin text NOT NULL CHECK (origin IN ('predefined','custom')),
  sensitivity text NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal','sensitive')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','deleted')),
  monitoring_health text NOT NULL DEFAULT 'not_started'
    CHECK (monitoring_health IN ('not_started','healthy','delayed','failed','suspended')),
  current_boundary_version_id uuid,
  current_grid_version_id uuid,
  deleted_at timestamptz,
  recoverable_until timestamptz,
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,slug),
  FOREIGN KEY (organisation_id,managing_department_id) REFERENCES departments(organisation_id,id),
  FOREIGN KEY (organisation_id,created_by) REFERENCES user_profiles(organisation_id,id),
  CHECK ((status='deleted')=(deleted_at IS NOT NULL))
);
CREATE TABLE site_team_access (
  organisation_id uuid NOT NULL,
  site_id uuid NOT NULL,
  team_id uuid NOT NULL,
  granted_by uuid NOT NULL,
  granted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organisation_id,site_id,team_id),
  FOREIGN KEY (organisation_id,site_id) REFERENCES sites(organisation_id,id),
  FOREIGN KEY (organisation_id,team_id) REFERENCES teams(organisation_id,id),
  FOREIGN KEY (organisation_id,granted_by) REFERENCES user_profiles(organisation_id,id)
);
CREATE TABLE site_boundary_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  site_id uuid NOT NULL,
  version integer NOT NULL CHECK (version > 0),
  geometry geometry(MultiPolygon,4326) NOT NULL,
  source_authority text NOT NULL,
  source_identifier text NOT NULL,
  source_url text,
  licence text NOT NULL,
  attribution text NOT NULL,
  effective_date date,
  source_crs text NOT NULL,
  validation_result jsonb NOT NULL DEFAULT '{}'::jsonb,
  checksum text NOT NULL,
  superseded_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,site_id,version),
  FOREIGN KEY (organisation_id,site_id) REFERENCES sites(organisation_id,id),
  CHECK (ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry) AND ST_SRID(geometry)=4326)
);
CREATE UNIQUE INDEX boundary_one_current ON site_boundary_versions(organisation_id,site_id)
WHERE superseded_at IS NULL;
CREATE INDEX boundary_geometry_gix ON site_boundary_versions USING gist(geometry);
CREATE TABLE grid_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  site_id uuid NOT NULL,
  version integer NOT NULL CHECK (version > 0),
  method text NOT NULL,
  resolution_metres numeric(10,2) NOT NULL CHECK (resolution_metres > 0),
  parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
  creation_reason text NOT NULL,
  processing_compatibility text NOT NULL,
  superseded_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,site_id,version),
  FOREIGN KEY (organisation_id,site_id) REFERENCES sites(organisation_id,id)
);
CREATE UNIQUE INDEX grid_one_current ON grid_versions(organisation_id,site_id)
WHERE superseded_at IS NULL;
CREATE TABLE grid_cells (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  grid_version_id uuid NOT NULL,
  cell_key text NOT NULL,
  display_label text,
  geometry geometry(Polygon,4326) NOT NULL,
  area_sq_m numeric(18,2) NOT NULL CHECK (area_sq_m > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,grid_version_id,cell_key),
  FOREIGN KEY (organisation_id,grid_version_id) REFERENCES grid_versions(organisation_id,id),
  CHECK (ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry) AND ST_SRID(geometry)=4326)
);
CREATE INDEX grid_cells_geometry_gix ON grid_cells USING gist(geometry);
ALTER TABLE sites ADD CONSTRAINT sites_current_boundary_fk
  FOREIGN KEY (organisation_id,current_boundary_version_id)
  REFERENCES site_boundary_versions(organisation_id,id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE sites ADD CONSTRAINT sites_current_grid_fk
  FOREIGN KEY (organisation_id,current_grid_version_id)
  REFERENCES grid_versions(organisation_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE monitoring_schedules (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  site_id uuid NOT NULL,
  cadence text NOT NULL CHECK (cadence IN ('weekly','fortnightly','monthly')),
  sensor_settings jsonb NOT NULL DEFAULT '{}'::jsonb,
  quality_settings jsonb NOT NULL DEFAULT '{}'::jsonb,
  next_due_at timestamptz NOT NULL,
  last_discovery_cursor text,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','archived')),
  suspension_reason text,
  scheduling_version integer NOT NULL DEFAULT 1 CHECK (scheduling_version > 0),
  changed_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,site_id) REFERENCES sites(organisation_id,id),
  FOREIGN KEY (organisation_id,changed_by) REFERENCES user_profiles(organisation_id,id),
  CHECK ((status='suspended')=(suspension_reason IS NOT NULL))
);
CREATE UNIQUE INDEX schedule_one_current ON monitoring_schedules(organisation_id,site_id)
WHERE status <> 'archived';
CREATE INDEX monitoring_schedules_due_idx ON monitoring_schedules(next_due_at)
WHERE status='active';

CREATE TABLE catalogue_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL REFERENCES organisations(id),
  provider text NOT NULL,
  collection text NOT NULL,
  source_identifier text NOT NULL,
  acquired_at timestamptz NOT NULL,
  footprint geometry(MultiPolygon,4326) NOT NULL,
  assets jsonb NOT NULL,
  licence text NOT NULL,
  attribution text NOT NULL,
  source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,provider,collection,source_identifier),
  CHECK (ST_IsValid(footprint) AND NOT ST_IsEmpty(footprint) AND ST_SRID(footprint)=4326)
);
CREATE INDEX catalogue_footprint_gix ON catalogue_items USING gist(footprint);
CREATE INDEX catalogue_acquired_idx ON catalogue_items(organisation_id,acquired_at DESC);
CREATE TABLE observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  site_id uuid NOT NULL,
  catalogue_item_id uuid NOT NULL,
  grid_version_id uuid NOT NULL,
  baseline_observation_id uuid REFERENCES observations(id),
  coverage_ratio numeric(6,5) CHECK (coverage_ratio BETWEEN 0 AND 1),
  quality_assessment jsonb NOT NULL DEFAULT '{}'::jsonb,
  eligibility text NOT NULL DEFAULT 'pending' CHECK (eligibility IN ('pending','eligible','ineligible')),
  eligibility_reason text,
  discovery_method text NOT NULL CHECK (discovery_method IN ('scheduled','manual','backfill')),
  status text NOT NULL DEFAULT 'discovered' CHECK (status IN (
    'discovered','evaluating','eligible','ineligible','queued','processing','ready','failed','superseded'
  )),
  observed_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,site_id,catalogue_item_id,grid_version_id),
  FOREIGN KEY (organisation_id,site_id) REFERENCES sites(organisation_id,id),
  FOREIGN KEY (organisation_id,catalogue_item_id) REFERENCES catalogue_items(organisation_id,id),
  FOREIGN KEY (organisation_id,grid_version_id) REFERENCES grid_versions(organisation_id,id)
);
CREATE INDEX observations_site_time_idx ON observations(organisation_id,site_id,observed_at DESC);
CREATE INDEX observations_status_idx ON observations(organisation_id,status,observed_at DESC);
CREATE TABLE processing_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  site_id uuid NOT NULL,
  observation_id uuid,
  grid_version_id uuid,
  job_type text NOT NULL CHECK (job_type IN ('discovery','processing','reprocessing','export')),
  trigger_type text NOT NULL CHECK (trigger_type IN ('scheduled','manual','retry')),
  priority smallint NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 9),
  idempotency_key text NOT NULL,
  requested_configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
  requested_by uuid NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN (
    'queued','orchestrating','running','publishing','retry_wait','completed','failed','cancelled'
  )),
  progress smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  cancellation_reason text,
  retry_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,idempotency_key),
  FOREIGN KEY (organisation_id,site_id) REFERENCES sites(organisation_id,id),
  FOREIGN KEY (organisation_id,observation_id) REFERENCES observations(organisation_id,id),
  FOREIGN KEY (organisation_id,grid_version_id) REFERENCES grid_versions(organisation_id,id),
  FOREIGN KEY (organisation_id,requested_by) REFERENCES user_profiles(organisation_id,id)
);
CREATE INDEX processing_jobs_status_idx ON processing_jobs(organisation_id,status,created_at);
CREATE TABLE orchestration_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  processing_job_id uuid NOT NULL,
  orchestrator_run_identifier text NOT NULL,
  dag_id text NOT NULL,
  dag_version text NOT NULL,
  triggered_at timestamptz NOT NULL DEFAULT now(),
  current_stage text,
  last_callback_at timestamptz,
  output_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  terminal_result jsonb,
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,orchestrator_run_identifier),
  FOREIGN KEY (organisation_id,processing_job_id) REFERENCES processing_jobs(organisation_id,id)
);
CREATE TABLE processing_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  orchestration_run_id uuid NOT NULL,
  observation_id uuid,
  boundary_version_id uuid NOT NULL,
  grid_version_id uuid NOT NULL,
  input_assets jsonb NOT NULL,
  parameters jsonb NOT NULL,
  code_version text NOT NULL,
  model_version text,
  environment jsonb NOT NULL,
  started_at timestamptz NOT NULL,
  completed_at timestamptz,
  output_assets jsonb NOT NULL DEFAULT '[]'::jsonb,
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
  checksum text,
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,orchestration_run_id),
  FOREIGN KEY (organisation_id,orchestration_run_id) REFERENCES orchestration_runs(organisation_id,id),
  FOREIGN KEY (organisation_id,observation_id) REFERENCES observations(organisation_id,id),
  FOREIGN KEY (organisation_id,boundary_version_id) REFERENCES site_boundary_versions(organisation_id,id),
  FOREIGN KEY (organisation_id,grid_version_id) REFERENCES grid_versions(organisation_id,id),
  CHECK (completed_at IS NULL OR completed_at >= started_at)
);
CREATE TABLE raster_assets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  observation_id uuid,
  processing_run_id uuid,
  asset_type text NOT NULL CHECK (asset_type IN ('source_reference','derived_cog','thumbnail')),
  object_key text,
  source_href text,
  cog_valid boolean,
  bounds geometry(Polygon,4326),
  bands jsonb NOT NULL DEFAULT '[]'::jsonb,
  resolution_metres numeric(10,3),
  checksum text,
  size_bytes bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
  processing_version text,
  retention_deadline timestamptz,
  lineage jsonb NOT NULL DEFAULT '{}'::jsonb,
  superseded_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,object_key),
  FOREIGN KEY (organisation_id,observation_id) REFERENCES observations(organisation_id,id),
  FOREIGN KEY (organisation_id,processing_run_id) REFERENCES processing_runs(organisation_id,id),
  CHECK (bounds IS NULL OR (ST_IsValid(bounds) AND ST_SRID(bounds)=4326)),
  CHECK ((asset_type='source_reference' AND source_href IS NOT NULL)
      OR (asset_type<>'source_reference' AND object_key IS NOT NULL))
);
CREATE INDEX raster_assets_bounds_gix ON raster_assets USING gist(bounds);
CREATE INDEX raster_assets_retention_idx ON raster_assets(retention_deadline)
WHERE superseded_at IS NOT NULL;
CREATE TABLE grid_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  observation_id uuid NOT NULL,
  grid_cell_id uuid NOT NULL,
  processing_run_id uuid NOT NULL,
  quality jsonb NOT NULL DEFAULT '{}'::jsonb,
  measurements jsonb NOT NULL DEFAULT '{}'::jsonb,
  change_features jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,observation_id,grid_cell_id,processing_run_id),
  FOREIGN KEY (organisation_id,observation_id) REFERENCES observations(organisation_id,id),
  FOREIGN KEY (organisation_id,grid_cell_id) REFERENCES grid_cells(organisation_id,id),
  FOREIGN KEY (organisation_id,processing_run_id) REFERENCES processing_runs(organisation_id,id)
);
CREATE INDEX grid_observations_cell_idx
  ON grid_observations(organisation_id,grid_cell_id,created_at DESC);

CREATE TABLE change_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  site_id uuid NOT NULL,
  observation_id uuid NOT NULL,
  processing_run_id uuid NOT NULL,
  category text NOT NULL CHECK (category IN (
    'possible_vegetation_loss','possible_linear_clearing','possible_burn_signal',
    'possible_water_change','unknown_disturbance'
  )),
  geometry geometry(Geometry,4326) NOT NULL,
  affected_area_sq_m numeric(18,2) CHECK (affected_area_sq_m >= 0),
  signal_strength numeric(6,5) CHECK (signal_strength BETWEEN 0 AND 1),
  review_status text NOT NULL DEFAULT 'new' CHECK (review_status IN (
    'new','under_remote_review','awaiting_more_observations','remotely_corroborated',
    'referred_to_authority','institutionally_verified','inconclusive','dismissed','resolved'
  )),
  sensitivity text NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal','sensitive')),
  resolution text,
  resolved_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,site_id) REFERENCES sites(organisation_id,id),
  FOREIGN KEY (organisation_id,observation_id) REFERENCES observations(organisation_id,id),
  FOREIGN KEY (organisation_id,processing_run_id) REFERENCES processing_runs(organisation_id,id),
  CHECK (ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry) AND ST_SRID(geometry)=4326)
);
CREATE INDEX change_events_geometry_gix ON change_events USING gist(geometry);
CREATE INDEX change_events_queue_idx ON change_events(organisation_id,review_status,created_at DESC);
CREATE TABLE event_grid_cells (
  organisation_id uuid NOT NULL,
  event_id uuid NOT NULL,
  grid_cell_id uuid NOT NULL,
  measurements jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (organisation_id,event_id,grid_cell_id),
  FOREIGN KEY (organisation_id,event_id) REFERENCES change_events(organisation_id,id),
  FOREIGN KEY (organisation_id,grid_cell_id) REFERENCES grid_cells(organisation_id,id)
);
CREATE TABLE event_assignments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  event_id uuid NOT NULL,
  assignee_id uuid NOT NULL,
  assigned_by uuid NOT NULL,
  assignment_type text NOT NULL CHECK (assignment_type IN ('analyst_review','institutional_verification')),
  due_at timestamptz,
  accepted_at timestamptz,
  completed_at timestamptz,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','completed','declined','cancelled')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,event_id) REFERENCES change_events(organisation_id,id),
  FOREIGN KEY (organisation_id,assignee_id) REFERENCES user_profiles(organisation_id,id),
  FOREIGN KEY (organisation_id,assigned_by) REFERENCES user_profiles(organisation_id,id)
);
CREATE INDEX event_assignments_assignee_idx ON event_assignments(organisation_id,assignee_id,status);
CREATE TABLE event_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  event_id uuid NOT NULL,
  evidence_type text NOT NULL CHECK (evidence_type IN (
    'raster_comparison','analyst_note','authorised_report','authorised_media'
  )),
  source text NOT NULL,
  collected_by uuid NOT NULL,
  collected_at timestamptz NOT NULL,
  access_classification text NOT NULL CHECK (access_classification IN ('normal','sensitive','restricted')),
  checksum text,
  object_key text,
  provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,event_id) REFERENCES change_events(organisation_id,id),
  FOREIGN KEY (organisation_id,collected_by) REFERENCES user_profiles(organisation_id,id)
);
CREATE TABLE reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  event_id uuid NOT NULL,
  review_type text NOT NULL CHECK (review_type IN ('remote_analysis','institutional_verification')),
  decision text NOT NULL,
  rationale text NOT NULL,
  confidence_statement text NOT NULL,
  actor_id uuid NOT NULL,
  supporting_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
  supersedes_review_id uuid REFERENCES reviews(id),
  submitted_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,event_id) REFERENCES change_events(organisation_id,id),
  FOREIGN KEY (organisation_id,actor_id) REFERENCES user_profiles(organisation_id,id)
);
CREATE TABLE event_comments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  event_id uuid NOT NULL,
  author_id uuid NOT NULL,
  body text NOT NULL CHECK (length(body) BETWEEN 1 AND 10000),
  created_at timestamptz NOT NULL DEFAULT now(),
  edited_at timestamptz,
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,event_id) REFERENCES change_events(organisation_id,id),
  FOREIGN KEY (organisation_id,author_id) REFERENCES user_profiles(organisation_id,id)
);
CREATE TABLE subscriptions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  user_id uuid NOT NULL,
  site_id uuid,
  event_id uuid,
  channels jsonb NOT NULL DEFAULT '["in_app"]'::jsonb,
  digest_enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,user_id) REFERENCES user_profiles(organisation_id,id),
  FOREIGN KEY (organisation_id,site_id) REFERENCES sites(organisation_id,id),
  FOREIGN KEY (organisation_id,event_id) REFERENCES change_events(organisation_id,id),
  CHECK (site_id IS NOT NULL OR event_id IS NOT NULL)
);
CREATE TABLE notifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  recipient_id uuid NOT NULL,
  event_id uuid,
  notification_type text NOT NULL,
  safe_summary text NOT NULL,
  sensitivity text NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal','sensitive')),
  protected_path text NOT NULL,
  read_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,recipient_id) REFERENCES user_profiles(organisation_id,id),
  FOREIGN KEY (organisation_id,event_id) REFERENCES change_events(organisation_id,id)
);
CREATE INDEX notifications_recipient_idx ON notifications(organisation_id,recipient_id,created_at DESC);
CREATE TABLE notification_deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  notification_id uuid NOT NULL,
  channel text NOT NULL CHECK (channel IN ('in_app','email')),
  destination_reference text,
  provider_identifier text,
  attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN (
    'pending','sending','delivered','retry_wait','permanently_failed','suppressed'
  )),
  attempted_at timestamptz,
  delivered_at timestamptz,
  safe_error jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,notification_id) REFERENCES notifications(organisation_id,id)
);
CREATE TABLE retention_holds (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL REFERENCES organisations(id),
  authority_user_id uuid NOT NULL,
  scope_type text NOT NULL,
  scope_id uuid NOT NULL,
  reason text NOT NULL,
  expires_at timestamptz,
  review_at timestamptz NOT NULL,
  released_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,authority_user_id) REFERENCES user_profiles(organisation_id,id)
);
CREATE TABLE exports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  requested_by uuid NOT NULL,
  export_type text NOT NULL CHECK (export_type IN ('geojson','csv','report','catalogue','authorised_raster')),
  scope jsonb NOT NULL,
  filters jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed','expired')),
  expires_at timestamptz,
  result_object_key text,
  checksum text,
  sensitivity text NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal','sensitive','restricted')),
  download_count integer NOT NULL DEFAULT 0 CHECK (download_count >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,requested_by) REFERENCES user_profiles(organisation_id,id)
);
CREATE TABLE api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL,
  accountable_user_id uuid NOT NULL,
  name text NOT NULL,
  key_prefix text NOT NULL,
  secret_hash text NOT NULL UNIQUE,
  scopes jsonb NOT NULL,
  expires_at timestamptz,
  revoked_at timestamptz,
  last_used_at timestamptz,
  usage_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  UNIQUE (organisation_id,name),
  FOREIGN KEY (organisation_id,accountable_user_id) REFERENCES user_profiles(organisation_id,id)
);
CREATE TABLE audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organisation_id uuid NOT NULL REFERENCES organisations(id),
  actor_id uuid,
  action text NOT NULL,
  target_type text NOT NULL,
  target_id uuid,
  before_summary jsonb,
  after_summary jsonb,
  reason text,
  correlation_id uuid NOT NULL DEFAULT gen_random_uuid(),
  ip_address inet,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id,id),
  FOREIGN KEY (organisation_id,actor_id) REFERENCES user_profiles(organisation_id,id)
);
CREATE INDEX audit_events_timeline_idx ON audit_events(organisation_id,created_at DESC,action);
CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit events are immutable'; END $$;
CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();

ALTER TABLE organisations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organisations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON organisations
  USING (id=app_current_organisation_id())
  WITH CHECK (id=app_current_organisation_id());
DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'departments','user_profiles','teams','team_memberships','auth_credentials',
    'auth_sessions','invitations','password_reset_tokens','sites','site_team_access',
    'site_boundary_versions','grid_versions','grid_cells','monitoring_schedules',
    'catalogue_items','observations','processing_jobs','orchestration_runs',
    'processing_runs','raster_assets','grid_observations','change_events',
    'event_grid_cells','event_assignments','event_evidence','reviews','event_comments',
    'subscriptions','notifications','notification_deliveries','retention_holds',
    'exports','api_keys','audit_events'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',table_name);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (organisation_id=app_current_organisation_id()) WITH CHECK (organisation_id=app_current_organisation_id())',
      table_name
    );
  END LOOP;
END $$;

CREATE INDEX sites_status_idx ON sites(organisation_id,status,monitoring_health);
CREATE INDEX sessions_user_active_idx ON auth_sessions(organisation_id,user_id,expires_at)
WHERE revoked_at IS NULL;
CREATE INDEX invitations_expiry_idx ON invitations(expires_at)
WHERE accepted_at IS NULL AND revoked_at IS NULL;
CREATE INDEX reset_tokens_expiry_idx ON password_reset_tokens(expires_at)
WHERE consumed_at IS NULL AND invalidated_at IS NULL;
"""

TABLES = [
    "audit_events",
    "api_keys",
    "exports",
    "retention_holds",
    "notification_deliveries",
    "notifications",
    "subscriptions",
    "event_comments",
    "reviews",
    "event_evidence",
    "event_assignments",
    "event_grid_cells",
    "change_events",
    "grid_observations",
    "raster_assets",
    "processing_runs",
    "orchestration_runs",
    "processing_jobs",
    "observations",
    "catalogue_items",
    "monitoring_schedules",
    "site_team_access",
    "grid_cells",
    "grid_versions",
    "site_boundary_versions",
    "sites",
    "password_reset_tokens",
    "invitations",
    "auth_sessions",
    "auth_credentials",
    "team_memberships",
    "teams",
    "user_profiles",
    "departments",
    "organisations",
    "workspace_templates",
]


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_mutation()")
    op.execute("DROP TRIGGER IF EXISTS team_membership_department_guard ON team_memberships")
    op.execute("DROP FUNCTION IF EXISTS enforce_team_membership_department()")
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS app_current_user_id()")
    op.execute("DROP FUNCTION IF EXISTS app_current_organisation_id()")

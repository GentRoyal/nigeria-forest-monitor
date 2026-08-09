# Product specification

Status: Phase 1 draft — confirmed decisions are recorded below; unresolved
decisions remain explicitly open.

## Product statement

Nigeria Forest Monitor is a private, multi-organisation geospatial monitoring
platform. It helps authorised institutions register forest sites, monitor them
on configurable schedules, inspect satellite observations, triage possible
changes, collaborate on findings, and retain an auditable history.

The operational product is the monitoring and review workflow. Remote-sensing,
rules, and machine-learning models are replaceable detection capabilities; they
do not define the product and their output is never treated as proof.

## Users and market

### Primary MVP customer

Nigerian government forestry, environmental, protected-area, and related public
institutions.

### Supported future customers

- Conservation NGOs
- Research institutions
- Responsible land managers

The platform remains institution-neutral: every customer uses the same tenant
model and receives an isolated private workspace.

The initial deployment serves one government institution or entity. Additional
entities can later be provisioned from the same default workspace template
without sharing users or operational data.

## Confirmed roles

- **Organisation administrator:** manages workspace membership, sites,
  monitoring policies, and organisation configuration; can inspect all content
  belonging to the organisation.
- **Analyst:** remotely investigates imagery, grid observations, and change
  events for sites they are authorised to access.
- **Authorised verification officer:** receives only institutionally assigned
  verification cases and sees sensitive details only when required.
- **Executive viewer:** reads approved summaries and resolved findings without
  modifying analysis.
- **Platform operator:** operates the service but has no routine access to
  customer content. Any exceptional support access must be approved, temporary,
  least-privileged, and fully audited.

Analysts can access all sites in their organisation by default. Organisation
administrators can restrict designated sensitive sites to explicitly authorised
users.

Each user belongs to exactly one organisation. Cross-organisation membership,
workspace switching, and cross-tenant collaboration are outside the MVP.
Authorised verification officers are invited organisation members; the MVP has
no external guest or public case-access mechanism.

## MVP user stories

- As an organisation administrator, I can invite users, assign roles, select a
  predefined forest, add an authorised custom site, restrict sensitive sites,
  and configure monitoring so my institution controls its workspace.
- As an analyst, I can see monitoring freshness, inspect dated raster layers and
  grid observations, compare before/after imagery, triage a possible change,
  document remote evidence, and reach a review decision.
- As an authorised verification officer, I can access only assigned referrals,
  record institutionally obtained evidence, and return a verification outcome
  without the platform directing field activity.
- As an executive viewer, I can inspect approved summaries, resolved findings,
  trends, and provenance without modifying analytical records.
- As a platform operator, I can diagnose service and job failures without routine
  access to customer content; approved exceptional access is time-bound and
  audited.

## Workspace model

- The MVP is private and invitation-only.
- Customer data is isolated by organisation.
- Every organisation contains departments and teams.
- A new organisation receives the standard workspace, roles, workflow, and
  configuration template; its users and operational data remain isolated.
- Public exploration is outside the initial release.
- Organisations may manage predefined sites and add authorised custom sites.
- Access to a custom site does not imply authority to enter or inspect it
  physically.

## Authentication

- Accounts are invitation-only and backed by the local PostgreSQL database.
- Passwords use Argon2id hashing; the system never stores plaintext or
  recoverable passwords.
- Short-lived access tokens and rotating, hashed refresh tokens provide browser
  sessions.
- Invitations and password-reset tokens are single-use, expiring, and hashed.
- Account/session security events are audited.
- Social login is outside the MVP.
- MFA and government OIDC/SAML integration are deferred behind an identity-
  provider interface.

## Primary user journey

```text
Administrator invites team
  -> selects a predefined forest or adds an authorised custom site
  -> configures monitoring frequency
  -> system discovers a usable satellite observation or user triggers a run
  -> worker processes the site and grid
  -> possible changes enter the analyst queue
  -> analyst performs remote corroboration
  -> event is dismissed, retained for more observations, or referred through an
     authorised institutional workflow
  -> approved users review the outcome and audit history
```

## Monitoring controls

- Monitoring frequency is selected per site by an authorised user from weekly,
  fortnightly, or monthly schedules.
- An authorised user can manually trigger processing.
- A scheduled run processes only when a new eligible observation exists.
- Automatic and manual triggers must be idempotent and must not create duplicate
  work for the same site, observation, and processing version.
- An organisation administrator can suspend monitoring for a site.
- Suspension requires a reason, is timestamped, and creates an audit record.
- Suspension prevents new scheduled jobs but preserves the site, imagery,
  reviews, events, and history for authorised inspection.
- Suspension does not silently cancel a running job. Running work continues
  unless an administrator explicitly cancels it through the separate job-control
  workflow.
- Manual processing of a suspended site requires an explicit administrator
  override and warning acknowledgement.
- Resuming monitoring creates an audit record and calculates the next run from
  the current schedule; it does not automatically backfill missed observations.
- Changing a schedule does not alter a running job. The running job continues,
  the new schedule applies afterwards, and the next run is calculated from the
  time the schedule was changed. Cancellation remains a separate explicit
  action.

## Provisional MVP service targets

- API read requests: p95 latency below 500 milliseconds.
- API mutations: p95 latency below one second.
- Monitoring map usable within five seconds on a mid-range device over typical
  4G connectivity.
- First raster tiles visible within three seconds.
- New eligible satellite imagery discovered within 24 hours of catalogue
  availability.
- At least 95% of claimed pilot-site jobs complete within two hours.
- At least 95% job success, excluding upstream data-provider outages.
- 99.5% monthly web/API availability for private beta.

Processing targets must be benchmarked on the actual worker hardware and may be
revised transparently before private beta. The monthly hosted-infrastructure
budget is intentionally deferred; no USD 100/month cap has been approved.

## Internal-alpha demonstration

The internal alpha contains two complementary datasets:

1. A deterministic synthetic change over the Old Oyo–Kainji grid for automated,
   reproducible end-to-end tests.
2. A real historical Sentinel observation pair showing an observable change in
   the primary corridor, selected only after imagery quality, acquisition
   compatibility, licence, and provenance are verified.

The real example is described only as an observed change. It does not assert
illegal activity and does not require physical verification to demonstrate the
remote review workflow.

## Detection and context policy

Initial event categories describe observable signals rather than intent:

- Possible vegetation loss
- Possible linear clearing or track
- Possible burn signal
- Possible water or flood change
- Unknown disturbance

Categories remain hypotheses until reviewed. The system must not label an event
as illegal logging, encroachment, hostile activity, or another inferred cause
without separately supplied and authorised evidence.

ACLED conflict proximity is removed from the forest-change score. If retained,
ACLED appears only as an optional, access-controlled contextual layer and never
increases detection confidence or environmental severity.

## Notifications

- The MVP provides in-app and email notifications.
- A new possible change creates an in-app notification for analysts.
- Analysts receive a daily email digest of unreviewed changes.
- A remotely corroborated event immediately emails organisation administrators
  and subscribed analysts.
- A verification referral immediately notifies only the assigned authorised
  verification officer.
- A failed processing job notifies the initiating user and organisation
  administrators.
- Resolving an event notifies its subscribers.
- Email messages never contain location-sensitive details; recipients must
  authenticate to view them.
- Signed outgoing webhooks remain part of a later product increment.

## Retention and deletion

- Source imagery remains in its source catalogue; the platform stores references
  and provenance rather than duplicating source TIFFs.
- Derived COGs are retained for two years. Assets linked to unresolved events are
  retained until those events are resolved and the normal retention condition
  can be evaluated again.
- Events, reviews, evidence metadata, and processing provenance are retained for
  seven years.
- Audit records are immutable and retained for seven years.
- Notification delivery logs are retained for one year.
- A deleted site is recoverable for 30 days. After that period, its deletable
  content is removed while the minimum audit tombstone required to explain the
  deletion is retained.
- Deletion must respect legal holds and organisation-authorised retention
  overrides when those capabilities are introduced.

## Evidence and validation policy

### Validation levels

1. **Automated detection:** the system identifies a possible change.
2. **Remote analyst corroboration:** an analyst reviews time-series imagery and
   contextual evidence.
3. **Authorised institutional verification:** a government institution decides
   whether, when, and how verification can safely occur.

Remote corroboration may use Sentinel-1 persistence, optical imagery,
historical trends, fire or flood context, authorised reports, or authorised
aerial evidence. It must remain distinguishable from physical confirmation.

### Safety constraints

- The platform never instructs civilians to visit a detected location.
- It never generates patrol routes or automatically dispatches personnel.
- Sensitive event coordinates are private and permission-controlled.
- Physical verification is available only through an authorised institutional
  workflow after the responsible institution performs its own security review.
- An event may remain unverified indefinitely when verification is unsafe.
- Model output is decision-support information, not evidence of wrongdoing.

## Event lifecycle

```text
new
  -> under_remote_review
      -> dismissed
      -> awaiting_more_observations
      -> remotely_corroborated
          -> referred_to_authority
              -> institutionally_verified
              -> inconclusive
              -> dismissed
  -> resolved
```

Every transition requires an actor, timestamp, reason, and immutable audit
record. `institutionally_verified` cannot be assigned by an automated process.

## Initial geographic rollout

### Primary pilot

- Old Oyo National Park
- Kainji Lake National Park
- The broader Old Oyo–Kwara–Kainji monitoring corridor

Old Oyo and Kainji remain independently manageable sites even when presented in
the corridor view.

### Secondary predefined sites

- Omo Biosphere Reserve
- Cross River National Park, preserving its distinct divisions
- Okomu National Park

### Later scale test

- Gashaka-Gumti National Park

No approximate webpage-derived boundary may be shipped. Each seed geometry must
have a verified authority, version/date, licence, attribution, coordinate
reference system, and validation report before import.

## Confirmed initial non-goals

- A public monitoring portal
- Cross-organisation user membership or collaboration
- External guest access to verification cases
- Unsupervised or crowdsourced field deployment
- Civilian patrol coordination or route planning
- Automatic declaration of illegal, hostile, or criminal activity
- Fully autonomous resolution of detected events
- Nationwide high-frequency processing at launch
- Treating a confidence score as ground truth

## Open product decisions

The following require product-owner confirmation before this specification is
complete:

1. Monthly hosted-infrastructure budget and cost-alert thresholds.
2. The exact real historical observation pair for the primary-corridor demo.

## Reference authorities for predefined areas

- Nigeria Park Service national park overview:
  https://nigeriaparkservice.gov.ng/about-us/
- Nigeria Park Service safety guidance:
  https://nigeriaparkservice.gov.ng/attractions/
- UNESCO Omo Biosphere Reserve:
  https://www.unesco.org/mab/50anniversary/en/omo

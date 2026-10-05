# Deterministic reconciliation contract

The graph and snapshots are authoritative deterministic inputs for later
renderers. Collection and normalization do not render documents, publish to
Drive, query secret values, invoke Codex, or schedule nightly work.

## Explicit declarations

Approve `architecture.toml` in the repository registry before collecting it.
`version=1` admits only `nodes` and `edges`. Nodes admit `kind`, `name`, and
`attributes`; edges admit `kind`, `source`, and `target`. Unknown fields fail
collection for that source. Attribute values are bounded identifiers or paths;
free prose, URI credentials, and commands with arguments are excluded. The
schema contains no secret-value field. Secret references describe names and
location/usage metadata, never values.

```toml
version = 1

[[nodes]]
kind = "service"
name = "api"
attributes = { deployment_path = "compose.yml" }

[[nodes]]
kind = "secret_reference"
name = "API_TOKEN"
attributes = { project = "application", environment = "dev", path = "/api", injection = "infisical", required = "true", scope = "runtime" }

[[edges]]
kind = "consumes_secret"
source = { kind = "service", name = "api" }
target = { kind = "secret_reference", name = "API_TOKEN" }

[[edges]]
kind = "deployment_owner"
source = { kind = "service", name = "api" }
target = { kind = "repository", name = "owner/infrastructure" }
```

Entity names are scoped by kind and owning source repository. Endpoint references
default to the source repository, except repository nodes whose names identify
their repository. Other cross-repository endpoints specify `repository`.
Repository declarations must name their own source repository. Node and edge
identities escape all components to prevent delimiter collisions.

| Entity kinds | Typical declared metadata |
| --- | --- |
| repository, system, service | purpose identifier, technology, lifecycle, deployment_path |
| environment, host | declared names and ownership |
| database, storage | role, schema, backup path |
| release_contract | workflow, checks, publish, rollback path |
| orchestration, interface | workflow, endpoint identifier, protocol, port |
| secret_reference | project, environment, path, scope, injection, required |
| owner, procedure | script, prerequisite, verification, rollback, recovery |
| package | collected package and dependency names |

Typed edges are `contains`, `depends_on`, `deployment_owner`, `deploys_to`,
`consumes_workflow`, `hosted_by`, `reads_database`, `uses_storage`, `notifies`,
`consumes_secret`, `orchestrates`, `exposes`, `owned_by`, and `operated_by`.
Declaration containment connects entities to their source repository. References
create minimal nodes marked `declared=False` and `reference_only` gaps until
their own declaration arrives. Documentation hyperlinks provide contextual
references; they do not establish dependency edges. Environment variable names
alone do not establish secret consumption. Missing secret location metadata is
reported as gaps rather than guessed. Procedures retain script/workflow pointers
and prerequisite/verification/recovery metadata; command rendering is #474.

All properties preserve candidate values and their evidence. Configuration has
precedence over executable contracts, documentation, and supplemental GitHub
metadata. One highest-authority candidate is preferred but any disagreement is
still drift. Multiple highest-authority candidates are ambiguous. Missing or
stale high-authority evidence is not replaced by fresh lower-authority evidence.
Set membership (dependencies, checks, images) does not create scalar conflicts;
singular hosting and ownership edges also expose property-level resolution.
All edges remain visible, so consumers must inspect conflicts before rendering.

## Reconciliation and removal

`SourceCoverage` certifies a complete collector/source read, including an empty
successful read. Coverage replaces that source's previous observations only
when there is no applicable failure. Endpoint coverage covers paginated GitHub
objects under that endpoint. `RepositoryInventory` certifies a complete pinned
approved tree and permits removal of missing files only within current approved
paths. Narrowed approvals, skipped repositories, disabled collectors, failed
reads, truncated trees, and missing coverage never imply deletion.

Unavailable sources preserve prior observations with their last verified
revision and blob, marked stale. Healthy sources continue to update. Snapshots
also retain current collection failures and input coverage/inventory for diagnosis.
Explicit versioned policy tombstones can retire a whole repository or a source
prefix even when collection is unavailable. Removing a registry entry alone
does not retire it. Review `config/reconciliation.toml` before adding tombstones.

`publication_blocked` is true for collection failure, retained stale evidence,
or ambiguous properties. Later publishers must honor this flag and preserve
known-good documents; this batch provides the deterministic signal only.
Graph gaps and drift remain available for focused uncertainty explanations.

## Snapshot ledger

Use `SnapshotStore()` with `ARCHITECTURE_DOCS_DATABASE_URL` injected from Infisical,
or pass an explicit PostgreSQL URL in application code. Schema
`architecture_snapshot` stores history, head and diffs. Application initialization
applies versioned transactional DDL under a database migration lock. Reconciliation
uses a transaction advisory lock, and commits the snapshot, run and head atomically.
No SQLite, memory, filesystem path or legacy setting fallback exists.
Database provisioning and backups remain infrastructure responsibilities.
See [PostgreSQL setup and cutover](postgresql.md).

Store schema 1 retains content-addressed JSON snapshots, an atomic head, and
run records with transition diffs. Snapshot and graph schemas are versioned.
Each snapshot contains sorted evidence with source provenance, graph candidates,
gaps, verification states, current collection metadata, and reconciliation policy.
Changes to provenance can create a new auditable snapshot while producing an
empty material diff. Identical complete inputs reuse the existing snapshot;
repeated partial inputs likewise settle to the same stale snapshot. Runs retain
their own IDs and diffs, including A→B→A transitions.

`BEGIN IMMEDIATE` locks reading the prior head and writing the new snapshot/diff
as one transaction. A crash or write failure rolls back the entire update;
normalization failure leaves the head unchanged and records a normalized error
and source provenance without exception text or rejected values. Hash/schema
checks reject corrupt or future records. `latest()`, `get(id)`, and
`recent_runs(limit=20)` provide inspection; the latter permits at most 100 records.
Snapshot JSON and diff JSON can be exported from these methods for diagnosis.
No pruning policy is applied in R1, so history remains available.

The classifier compares node identities, declared state, resolved properties,
candidate conflicts, and typed edges. It ignores revision/blob provenance and
verification changes; configured transient properties are compared out on both
sides. Script/workflow digests deliberately remain semantic because the collector
excludes raw commands and cannot prove an executable body edit harmless. This
can conservatively flag comment-only edits. A deployment_path/host/environment
property edit is classified as deployment; procedure edits as runbook; dependency,
database/storage, secret topology, and release changes retain their categories.

## Acceptance evidence

| #472 acceptance criterion | Implementation and offline evidence |
| --- | --- |
| 1–2: R1 types and graph views | declarations.py, graph.py; representative collector fixture covers every node/edge kind, typed manifests, cross-repository references |
| 3: conflicts and precedence | candidate resolution; tests for configuration/prose drift, equal-authority ambiguity, conflicting hosts |
| 4: positive removal | coverage/inventory/tombstones; tests for omission, scope narrowing, missing files, endpoint absence, and failure overriding absence |
| 5–6: versioned snapshots and prior comparison | reconciliation.py, codec.py; deterministic ordering/deduplication, revision churn, no-change and round-trip tests |
| 7: material classification | categories and transient policy; tests for each required family plus opaque script edits |
| 8: partial failure preservation | source-specific stale evidence and publication block; healthy peer update and recovery tests |
| 9: durable diagnosis/restart | store.py; restart, duplicate snapshots, A→B→A, rollback, concurrent writers, corrupt records, normalized failure diagnosis |
| 10: validation | offline pytest fixtures, strict mypy, Ruff/format, coverage, pre-commit through Wood Tools; manual Dagster reconciliation and restart test |

The manual `architecture_reconciliation_job` connects the #471 collection asset
to this store, reporting snapshot/previous IDs, ledger run ID, material/change
counts, failure counts, schema version, and publication block in Dagster metadata.
No deployment/runtime-health claim follows from these offline checks. Final
OpenProject delivery evidence requires an approved commit, PR, and passed CI;
the local Wood validation record is prepared before that approval boundary.

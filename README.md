# Architecture Docs

Deterministic, read-only GitHub repository evidence collection (#471) and
architecture graph/snapshot reconciliation (#472), and deterministic architecture
documents (#473). Python 3.14, typed `src/architecture_docs`, Hatchling,
strict mypy, Ruff, pytest with 90% branch coverage, pre-commit, and centralized
semantic release follow the published `SpencerRWood/template-python-dagster`
foundation at `2b7d901e63095def72171397e3f5b99d457497d3`.

## Scope

Collectors emit versioned observations with repository, path/GitHub object,
commit (where applicable), blob SHA, source authority, and collector identity.
Each observation is an explicit source declaration or source index entry. This
collection layer feeds a separate deterministic graph and reconciliation layer.
Six modular renderers consume that normalized snapshot. Secrets/runbooks (#474),
Drive publication (#475), nightly scheduling, and Codex invocation (#476) belong
to later Stories.
`codex-runtime` owns capacity inspection; collection imports neither that library
nor any Codex provider. Optional narrative integration belongs to #476.

## Repository approval

`config/repositories.toml` explicitly includes this repository as an example;
it does not enumerate an account's repositories. Review scope before adding entries.
Each entry declares `name`, approved `paths` (case-sensitive shell globs),
`archived` (`exclude`, `include`, or `only`), and optional `ref` (otherwise the
default branch). Archived repositories default to exclusion. Limits bound files,
file bytes, metadata pages, and API response bytes. An empty registry deliberately
performs no network access. Sensitive path classes, environment files, private
keys, and Terraform state are denied even under a broad glob.

## Collector contract

`collection.collect(registry, github, collectors=None)` returns a
`CollectionResult`. Plugins implement `name` and `collect(Context)` and all use
the same orchestration-neutral boundary. Content approvals apply before blob
retrieval. One commit is resolved per repository; its tree and immutable blobs
are read, with blob hashes verified. No source script or workflow is executed.
The transport sends only GET to api.github.com and never follows redirects.
Use a fine-grained token with Contents, Metadata, and Actions read permissions.
No write API, secret-management API, or GitHub checkout mutation is exposed.

Content collectors extract a constrained metadata vocabulary:

- Configuration: package/dependency names, Compose services/storage/network
  identifiers and declared dependencies, environment variable names, release
  flags/checks, Terraform resource/data identifiers, Ansible role/module/variable
  names, package.json dependency names, and Dockerfile base images.
- Executable: workflow jobs, literal reusable-workflow/action references,
  declared secret names, shell executable names (without arguments), and Python
  function/class declarations for script/CLI source inventory.
- Documentation: approved document inventory and explicit GitHub repository
  references. Prose cannot establish a higher-priority fact.
- GitHub: repository identity/archive flag, workflow object paths, and release
  objects/tags. Metadata is supplemental and may change independently of a commit.

Raw source text is processed in memory but is not returned, persisted, logged, or
sent to another service. Literal environment/secret values, workflow `run` bodies,
release descriptions, and arbitrary prose are not copied. This foundation is a
metadata extractor, not an arbitrary source archive; extend the plugin vocabulary
with reviewed fixture coverage when more architecture metadata is needed.

Authority is configuration > executable > documentation > GitHub. `precedence`
orders all evidence without discarding competing observations. Results sort and
deduplicate deterministically and contain no collection timestamps. File parse,
API, plugin, pagination, size, integrity, and truncated-tree failures carry
normalized reasons and provenance without exception or server-message text.
`complete=False` means incomplete evidence, never a tombstone or empty successful
replacement. Downstream reconciliation must preserve prior facts on failure.
Successful collectors also emit per-source coverage; a complete pinned approved
tree emits an inventory. Reconciliation uses these as positive absence evidence.
Legacy/plugin results without coverage are accepted conservatively and cannot
delete prior observations by omission.

## Graph and snapshots

[`reconcile`](src/architecture_docs/reconciliation.py) normalizes collected
evidence into a versioned typed graph, retaining every competing candidate and
its provenance. Scalar properties mark lower-priority disagreements as drift;
equal highest-priority candidates are ambiguous with no preferred value.
Typed edges, including conflicting hosting/ownership edges, remain inspectable.
`Graph.manifest(NodeKind)` and `Graph.cross_repository_edges()` provide stable
inputs for architecture documents and later secrets/runbook renderers.

Existing collectors map packages, Compose services/storage/dependencies,
workflows and shared workflow consumption, release contracts, Terraform
declarations, and script contracts. An optional, approved `architecture.toml`
provides explicit declarations for otherwise unavailable topology. The
[declaration contract](docs/reconciliation.md) covers all R1 entity and edge
types without inferring runtime state from names, prose links, or variable names.

`SnapshotStore(Path(...)).reconcile(collection, policy)` atomically persists
content-addressed snapshots, prior/current diffs, and a successful head in an
SQLite ledger. Repeated identical inputs reuse the same snapshot. Each run has
a ledger identifier, so a return to an older snapshot still records the correct
transition. Partial runs preserve known-good facts with `verification="stale"`
and expose `publication_blocked`; failed normalization never advances the head.
`latest()`, `get(snapshot_id)`, and bounded `recent_runs()` support diagnosis.

The store requires an explicit file in an existing persistent directory. It has
no implicit `/tmp`, working-directory, or memory fallback. Deployments must mount
durable local state and back up the ledger, following the platform's filesystem
state convention; this Story does not provision a runtime or storage mount.
SQLite writer transactions serialize concurrent reconciliations, with full
synchronous commits. Use a local filesystem with SQLite locking support.

Material diffs exclude source revisions, general blob churn, verification state,
and configured transient properties. Script/workflow content digests remain
semantic because opaque executable changes can affect operations. No raw script
body or secret value is retained. Categories identify repository/system changes,
dependencies, deployment paths, data/storage, secret topology, release contracts,
runbooks, ownership, interfaces, and orchestration.

## Development and Dagster

```sh
uv sync --frozen --group dev
wood repo validate --json
uv build
uv run dagster dev -m architecture_docs.dagster.definitions
```

`defs` registers `repository_observations`, `repository_collection_job`, and
`runtime_smoke_job`, plus `architecture_snapshot`, `architecture_documents`,
`architecture_reconciliation_job`, and `architecture_rendering_job`.
Collection, reconciliation, and rendering are manual;
there are no new schedules or sensors. Reconciliation configuration:

```yaml
ops:
  repository_observations:
    config:
      registry_path: /absolute/path/to/repositories.toml
  architecture_snapshot:
    config:
      snapshot_path: /persistent/local/state/snapshots.sqlite3
      policy_path: /absolute/path/to/reconciliation.toml # Optional
```

Inject `ARCHITECTURE_DOCS_GITHUB_TOKEN` at runtime; omit it only for public sources.
The asset returns stable JSON and reports completeness/counts in Dagster metadata.
Consumers must inspect completeness before using it. The smoke job needs no
credentials, network, or application configuration. The Dockerfile serves the
code location using required `DAGSTER_GRPC_PORT`; deployment host and registration
belong to infrastructure. This batch declares no infrastructure deployment or
container promotion contract. The published template's `workflows@v1` Python
validation/release callers remain intact; semantic-release owns tags and versions.

## Requirements and validation

[Architecture Docs requirements](https://docs.google.com/document/d/1MFw9S34__hR2zclkw4G4j4DIix-Ac7OEQpTziA1EflI/edit)
FR-001–005 and QR-001–004/011 map to explicit approvals, GET-only reads, the
plugin boundary, provenance, authority ordering, deterministic fixture tests,
isolated failures, and constrained metadata extraction. Tests exercise multiple
source families, archived policies, conflicting evidence, source bounds,
partial failures, sensitive-content exclusion, HTTP redirects/errors, and offline
Dagster execution. They attest fixture behavior, not live repository completeness
or deployed runtime health.

For #472, FR-005–012 and FR-049–054 / QR-001–004,009–010 map to normalized
provenance, drift/ambiguity, positive deletion evidence, deterministic snapshots
and material diffs, durable history, stale-source preservation, a downstream
publication block, and sanitized diagnostic records. See the
[acceptance and test mapping](docs/reconciliation.md#acceptance-evidence).

## Deterministic documents

`renderers.render_documents(snapshot, RenderConfig(...))` generates the six
required architecture documents from the normalized graph and source provenance.
Artifacts preserve stable document and section identities, candidate conflicts,
stale verification states, missing-contract gaps, and source repository/path/
revision/blob/authority. An Architecture Overview Mermaid diagram derives major
boundaries and cross-repository links directly from the typed graph. The catalog
separates included repositories from reference-only placeholders.

`Document.to_json()` and `Document.to_markdown(config)` expose structured and
human-readable views. Semantic `content_hash` excludes provenance-only churn;
snapshot/configuration/model/renderer identifiers remain explicit. Rendering a
blocked snapshot produces inspectable **blocked** artifacts; it does not authorize
publication or replace known-good documents.

Export a saved, verified snapshot locally:

```sh
uv run python -m architecture_docs.renderers snapshot.json /absolute/output/path
```

The exporter writes six stable `.md` filenames and `document-set.json`, and
returns bounded JSON metadata. Optional `--title-prefix` and `--no-source-links`
affect presentation; provenance remains in the artifact. No GitHub collection,
Drive access, or Codex runtime is involved in this command.

See [document contracts and acceptance evidence](docs/rendering.md) for the
FR-013/016/020 and QR-001/002/004/012 mapping and explicit declaration vocabulary.

# Architecture Docs

Deterministic, read-only GitHub repository evidence collection for OpenProject
Story #471 (AD-R1-02). Python 3.14, typed `src/architecture_docs`, Hatchling,
strict mypy, Ruff, pytest with 90% branch coverage, pre-commit, and centralized
semantic release follow the published `SpencerRWood/template-python-dagster`
foundation at `2b7d901e63095def72171397e3f5b99d457497d3`.

## Scope

Collectors emit versioned observations with repository, path/GitHub object,
commit (where applicable), blob SHA, source authority, and collector identity.
Each observation is an explicit source declaration or source index entry. This
layer does not infer graph relationships, reconcile drift, render documents,
publish to Drive, schedule nightly runs, or invoke Codex. Those are later Stories.
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

## Development and Dagster

```sh
uv sync --frozen --group dev
wood repo validate --json
uv build
uv run dagster dev -m architecture_docs.dagster.definitions
```

`defs` registers `repository_observations`, `repository_collection_job`, and
`runtime_smoke_job`. Collection is manual with explicit run configuration:

```yaml
ops:
  repository_observations:
    config:
      registry_path: /absolute/path/to/repositories.toml
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

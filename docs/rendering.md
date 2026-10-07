# Deterministic architecture documents

Story #473 implements six architecture views over the #472 normalized snapshot.
The graph and deterministic evidence remain authoritative. Rendered prose and
local exports never become architecture input.

## Artifact contract

`render_documents(snapshot, config)` returns a versioned `DocumentSet`. Its six
stable document IDs are `architecture-overview`, `repository-dependency-catalog`,
`deployment-release-architecture`, `runtime-infrastructure-architecture`,
`data-storage-architecture`, and `automation-orchestration-architecture`.
No Secrets Manifest or operational runbook is generated here (#474).

Each document carries its snapshot ID, model/graph/schema/renderer versions,
configuration ID, publication block, generation state, and semantic content hash.
Sections and rows retain stable identities and affected entity IDs. Every
evidenced entity, candidate property, or relationship row retains machine-readable
source references: repository, source path/object, revision, blob, collector,
authority, and verification state. Source references omit original observation
payloads, raw source text, commands with arguments, and secret values.

JSON is the publication-neutral artifact model and retains full machine identifiers.
Normal Markdown across all architecture views and runbooks uses numbered citations
and named provenance: source system, repository/project, path or object name,
environment/location and verification status. Snapshot hashes, blob IDs, inventory
digests, provider UUIDs and internal source/graph IDs are not document headers or
normal prose. Infisical locations use project names rather than workspace UUIDs;
secret object UUIDs stay in typed evidence and semantic inputs.

Pinned GitHub links and workflow references use short commit SHAs. Stale evidence
can identify the last verified short commit; otherwise indistinguishable ambiguous
Infisical locations use the shortest distinguishing project/object prefix in their
diagnostic rows. Full references, precedence, revisions and blobs remain available
internally. Mermaid node aliases are short presentation-local numbers. Missing
human labels are not invented. All source-derived display text is escaped for
Markdown and HTML.
Source links may be disabled while preserving provenance. Titles admit a bounded
prefix. Configuration mismatches and unsupported model versions fail clearly.

Renderer version 2 participates in semantic publication hashes so this global
presentation change replaces existing documents through the normal ledger even
when the underlying architecture snapshot has not changed. Metadata-aware material
inputs, stale/partial blocks, known-good preservation and idempotency remain intact.

`content_hash` excludes citation/commit/blob churn, configuration-only source-link
changes, and the snapshot identifier. It includes displayed facts, diagnostics,
generation state, and diagram content. Configured transient properties are absent
from rendered facts and conflict diagnostics. Provenance refreshes still change
artifact metadata and source references. Later publication logic decides whether
and how to refresh these details; this Story performs no remote update.

## Views and evidence boundaries

| Document | Deterministic view |
| --- | --- |
| Architecture Overview | Declared major systems, runtime boundaries, databases/storage and orchestration; significant typed interactions and cross-repository dependencies; individual Actions, workflow jobs, packages, volumes and reference-only dependencies excluded |
| Repository & Dependency Catalog | Repository summary followed by comprehensive entity and dependency evidence, including Actions, workflows, packages and volumes; separate reference-only repositories |
| Deployment & Release Architecture | Eight lifecycle stages: validation, candidate artifacts, runtime gates, release, promotion, deployment, rollback, verification; release contracts and typed deployment ownership/target/workflow edges |
| Runtime & Infrastructure Architecture | Declared environments/hosts/services, interfaces, explicit networking/reverse-proxy contracts, orchestration/code locations, persistence, shared data access and ownership |
| Data & Storage Architecture | Databases with explicit role/schema metadata, storage/NAS declarations, consumers, ownership/hosting and encoded backup/recovery references |
| Automation & Orchestration Architecture | Jobs, code locations, release/workflow consumption, operational contract inventory, notification/event interfaces, dependencies, and explicit capability coverage |

The catalog means included repositories **represented as declared nodes in the
normalized model**, including retained stale repositories. A failed first-ever
collection is reported as failure evidence, not fabricated as an empty catalog
entry. A repository link alone is a reference, not proof it was collected.
Incoming and outgoing typed edges show both upstream and downstream relationships.

Each document begins with a deterministic summary derived from its selected graph
architecture categories and agreed technology identifiers. The Overview summarizes
repository classifications, major persistence boundaries, cross-repository
relationships, estate coverage and material unresolved gaps without enumerating
arbitrary featured entities. Its graph selects declared system/runtime boundaries
and semantically significant services: explicit major/shared roles, shared
persistence, external interfaces, cross-repository relationships or deployment
and orchestration topology. Compose discovery alone does not qualify a leaf
service. Detailed service, package, action, job and volume evidence remains in
specialized views and the catalog; filtering never changes the underlying graph.
Summaries do not resolve drift or establish missing purpose declarations. The
catalog shows all successful estate repository/domain evidence; specialized views
retain estate status and every missing repository/domain without repeating the
entire successful inventory. Source failures remain visible in every document.

Reference-only and missing-secret-field diagnostics are grouped by architecture
domain and reason, retaining all affected entity IDs and source references.
Secrets Manifest leads with consumer-to-Infisical mapping tables. Unresolved
references and ambiguous candidates remain separate; optional metadata uses `—`.
Approved inventory with no known consumer and unverified repository declarations
are secondary sections. Matching names do not establish synchronized values or a
shared distribution/rotation policy. Each mapping retains consumer and secret-manager
provenance with independent revision semantics. See [metadata collection](infisical-metadata.md).

Runbooks show supporting typed contracts separately from explicit procedure
steps. Supporting script/workflow references retain source provenance and identify
the missing `runbook`, `phase`, `order`, and approved source-reference contract.
They never establish executable instructions or successful recovery. Runtime and
automation views select relevant fields; package dependency details remain in the
catalog, and detailed data recovery contracts remain in the data view.
When procedure declarations are absent, runbooks also group current, completely
parsed first-party workflows, scripts and evidenced Ansible sources using explicit
path hints. Generic external Actions are excluded from procedure candidates;
explicit operational procedure references can retain external sources. Exact
shared workflow references aggregate consumers and all consumer-specific citations
into one row. A common requirements row identifies the missing procedure contract.
These are
discovery candidates with undeclared applicability, remain gaps, and cannot
supply procedure steps. Failed, stale, unapproved or unmatched-revision sources
cannot become current supporting candidates.

Explicit graph fields are projected as named declarations. For example,
`build.python_package=true` states a package-build contract flag; it does not say
an artifact was built or passed a runtime gate. Renderers never inspect repository
files, scripts, collector inputs, Drive documents, or provider output to infer
more details. They never execute declared commands or query live hosts.

Existing normalized attributes (`checks`, `workflow`, `deployment_path`,
`rollback`, `verification`, `role`, `schema`, `backup`, etc.) remain supported.
The explicit architecture declaration allowlist also admits bounded identifiers
for `candidate_artifact`, `artifact`, `runtime_gate`, `promotion`,
`promotion_workflow`, `deployment`, `network`, `reverse_proxy`, and `code_location`.
These additions let repository authors declare missing metadata without the
renderer guessing it or bypassing the collector/normalizer contract.

Automation coverage uses explicit `technology` or `purpose` identifiers
`dagster`, `renovate`, `codex`, `openproject`, `reporting`, `repair`, and `events`.
Entities without those exact tags remain in the general automation inventory;
named capability coverage stays a gap. Merely naming an entity `renovate` does
not establish Renovate behavior. DAGSTER code locations similarly need a
`code_location` attribute. No missing runtime or integration is invented.

## Uncertainty and publication

Candidate values remain individually visible. Drift labels the preferred value
and alternatives; ambiguity retains every candidate and has no preferred value.
Conflicting relationship edges remain visible and labeled in tables and diagrams.
Reference-only nodes, missing purpose/contracts, and normalization gaps are
explicit documentation gaps, with no synthetic facts or procedures.

Partial snapshots preserve prior facts with stale source revisions and verification
states. All documents carry the snapshot's conservative publication block. A
blocked snapshot is rendered for diagnosis, and its Markdown/JSON state is
`blocked`. Future publishers must honor that flag and preserve known-good
published documents. Rendering alone is not permission to publish. Unblocked
documents with missing declarations are `with_gaps`, otherwise `complete`.

## Local use and orchestration

```python
from architecture_docs.renderers import render_documents
from architecture_docs.store import SnapshotStore

snapshot = SnapshotStore().latest()
if snapshot is not None:
    artifacts = render_documents(snapshot)
    markdown = artifacts.documents[0].to_markdown()
```

The local CLI validates a saved snapshot through the #472 codec before writing
fifteen Markdown files and `document-set.json` after #474 adds the Secrets
Manifest and eight runbooks (see [operational contracts](operations.md)).
It accepts an explicit output
directory, validates/renders all artifacts before writing, and emits bounded
result metadata. Local export writes individual files; it is not a transactional
publication store and does not claim Drive lifecycle guarantees.

`architecture_documents` depends on the normalized `architecture_snapshot` asset.
`architecture_rendering_job` manually connects collection, reconciliation, and
rendering; it reports snapshot ID, document count, schema, and publication block.
No schedules, sensors, Drive publication, or Codex invocation are added.

## Acceptance evidence

| #473 criterion | Automated evidence |
| --- | --- |
| 1: graph-derived overview | Typed cross-repository ownership/workflow/data/notification assertions and Mermaid diagram checks |
| 2: repository catalog | Exactly one entry per included repository, purpose and incoming/outgoing relations, separate contextual references |
| 3: delivery architecture | All eight stage declarations asserted, absent stages tested as gaps; no execution-success claims |
| 4–5: runtime and data | Explicit host/environment, reverse proxy/network, code location, NAS, role/schema, backup/recovery and consumer relationships |
| 6: automation | Declared capability tags, centralized workflow consumption, operations inventory and event/notification edges; names alone tested as gaps |
| 7: normalized input only | Test disables collection, normalization and file reads; rejected collector payload never enters artifacts |
| 8: uncertainty | Precedence drift, ambiguity, conflicting host edges, reference-only entities, empty/sparse snapshots, stale-source blocks |
| 9: deterministic output | Input order/restart equality, provenance/transient churn semantic hashes, config-specific deterministic local exports |
| 10: full types and cross-repository coverage | Six document types, all required stage families, provenance links, escaping, JSON artifacts, manual Dagster rendering and local export tests |

Tests use representative offline fixtures. They establish deterministic rendering
behavior, not live-estate completeness or deployed health. Drive document identity,
sharing, publication idempotency and narrative quality remain outside #473.

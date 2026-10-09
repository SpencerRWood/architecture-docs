# Repository classification and evidence-driven architecture

The classification model extends the existing collectors, graph/reconciliation,
deterministic documents and approved estate coverage. Collection scope remains
an explicit registry approval, independent of classification.

## Minimal metadata

The root `pyproject.toml` may declare:

```toml
[tool.wood.architecture]
domain = "engineering"
capability = "developer-tooling"
kind = "cli"
solution = "synthetic-website-analytics-platform" # optional
```

The only required fields within this opt-in contract are `domain`, `capability`,
and `kind`. `solution` groups components without merging repository identities.
Do not declare schema_version, summary, relationships, deployable, dev, or prod.
Unknown fields and vocabulary values fail with sanitized diagnostics. No
relationships or deployments are fabricated to fill missing metadata.

For a repository without a suitable root pyproject, approve the exact
`architecture.toml` path and use the same fields under `[architecture]`.
An optional `[project]` table supplies `name` and `description` using the same
contract as pyproject. Both sources are explicit supported inputs; if both are
approved and present, both contribute evidence. Equal-authority disagreement is
ambiguous and blocks publication. Invalid metadata does not silently select the
other source. Nested pyprojects can provide existing package evidence but do not
classify the whole repository.

Reuse `[project].name` as component/package identity and `[project].description`
as purpose when available. Descriptions are normalized bounded prose, limited
to 300 characters, with URLs, assignments, control characters, long opaque
tokens, and obvious credential-shaped prose excluded. Source bodies and arbitrary
configuration values are never stored. Authors must still keep credentials out
of metadata: syntax validation cannot identify every possible sensitive string.

### Controlled vocabularies

The merged estate uses domains `engineering`, `infrastructure`,
`platform-services`, `reference-solutions`, `reporting`, `templates`, and
`applications`. The vocabulary and accepted component kinds are defined in
`classification.py` and validated before observations enter the model.

Component kinds: `cli`, `library`, `service`, `web-app`, `pipeline`,
`orchestrator`, `infrastructure`, `configuration`, `workflow`, `report`,
`template`, `data-model`, `agent-runtime`, `data-generator`, `repository`.

| Architecture group | Capabilities |
| --- | --- |
| Infrastructure & Hosting | hosting-deployment |
| Shared Platform Services | event-messaging, knowledge-retrieval |
| Developer Tooling | developer-tooling |
| Agent Configuration & Runtime | agent-configuration, agent-runtime |
| Release & Promotion Control | release-control, release-promotion, reliability-recovery |
| Reporting & Documentation | architecture-documentation, document-rendering, operational-reporting, visualization |
| Applications & Reference Solutions | web-application, analytics-lifecycle |
| Engineering Templates | project-scaffolding, engineering-template |

These values live in `classification.py`; changing a vocabulary requires
documentation and fixture changes, not an alias or a deprecated-setting fallback.
Known repository-role mappings are explicitly **inferred**: wood-tools is
Developer Tooling, workflows is centralized Release & Promotion Control,
template repositories are Engineering Templates, and synthetic website
repositories are reference-solution candidates. They do not fill required metadata
or infer relationships. The four reviewed synthetic website data/dbt/analytics/
platform repository identities receive inferred membership in the shared
`synthetic-website-analytics-platform` solution when no explicit solution is present.
That policy groups existing approved components and never authorizes discovery.
Explicit solution metadata takes precedence; conflicting declarations remain
ambiguous. Explicit valid metadata establishes
the declared capability. Archived repositories retain evidence/history but are
excluded from active group membership and overview boundaries.

## Relationships and provenance

`architecture_evidence` implements the existing collector interface. It reads
only files already delivered by the approved, revision-pinned collection pipeline.
It adds no repository/path approvals and executes no source code. Evidence includes:

- Python dependencies, uv/Poetry lockfiles and explicit GitHub package sources.
  A unique normalized `[project].name` match supports an inferred repository edge;
  multiple matches remain unresolved. A lock entry is not automatically a direct
  application dependency.
- GitHub Actions reusable workflows, environment dispatch choices, and literal
  workflow/promotion references in `.github/release.toml`.
- Ansible role usage/definition paths, Compose dependencies, volumes/image pins,
  environment service selectors, and existing infrastructure runtime-service
  contracts with explicit Compose file/service mappings.
- Dagster workspace locations and statically imported Dagster API references.
  Dynamic expressions remain unsupported evidence rather than executing Python.
- dbt manifest node/source identities and lineage, YAML source/table definitions,
  and literal SQL `ref`/`source` calls. Comments do not create lineage.
- Existing application configuration containing explicit integration repository
  identifiers. Endpoint strings alone do not prove cross-repository identity.

Relationship confidence is distinct from successful source collection:

| State | Meaning |
| --- | --- |
| declared | A literal authoritative configuration names the relation and its endpoints are represented. |
| inferred | Identity matching or an explicitly marked inference supports a candidate; it is not verified. |
| verified | Current pinned evidence from independent source families corroborates the same declared endpoints, or the approved deployment API attests the deployment. Static corroboration does not prove runtime integration or health. |
| unresolved | An endpoint lacks collected definition evidence, or identity cannot be established. |

Every relationship retains source repository/path/revision/blob and authority.
Stale evidence, conflict, and ambiguity remain visible. Reference-only nodes never
authorize collection. Known-good facts retained after a failure are explicitly
stale, not silently promoted to current verified facts.

## Environments and deployments

Supported environments come from existing deployment/dispatch configuration.
Configured environments and selected services come from environment manifests
(including infrastructure's `environment_name` and `services` contract), and
runtime topology/image pins from Compose and infrastructure evidence. Selected
services lacking Compose definitions remain references with coverage gaps.
Configured image versions are labelled configured, never deployed.

An optional, explicitly approved `deployment-receipts.json` supplies bounded
selectors for **existing** GitHub Deployments in its own approved repository:

```json
{"deployments": [{"id": 42, "service": "api"}]}
```

The collector performs GET requests to that deployment and its latest status.
The API payload must identify the same `service`, the deployment must have a valid
revision and environment identifier, and the latest status must be `success`.
No selector authorizes another repository. Each file permits at most 50 selectors.
The source selector and API record have separate provenance. Only typed deployment
identifiers, environment, service, and attested revision survive parsing; arbitrary
payload fields and credential values are discarded.

This establishes a deployment observation at a revision, not continuous live state.
Missing/pending/failed receipts preserve prior observations as stale and block
publication. Deployment success establishes neither image digest nor runtime health.
Those facts remain unavailable without their own verified authority. No production
deployment is inferred from a package, application capability, template, or manifest.
Homelab and wood-data-platform retain their current live exclusions. Offline
fixtures can exercise those configuration shapes without expanding collection.

## Canonical documents

The existing fifteen-document set and stable document identities are retained.
The Architecture Overview adds the eight group hierarchy, domain/capability
groupings, component metadata, and logical reference solutions. Each reference
solution has a dependency/data-flow view that preserves distinct component names
and confidence/freshness labels. Membership alone never creates a flow edge.

The Repository & Dependency Catalog adds a component dependency diagram and a
coverage report for missing/conflicting/stale metadata, inferred/unresolved
relationships, and incomplete sources. Deployment & Release Architecture adds
per-environment topology, selected services, configured images, attested deployed
revisions, and independent health gaps. Existing delivery and orchestration views
retain engineering and centralized release-control contracts. Data & Storage
Architecture includes reference-solution flows.

All views consume normalized graph snapshots and their provenance. Canonical
Markdown retains deterministic Mermaid definitions, stable identity, component
relationships, uncertainty, and readable source references for wood-reports and
RAG. #523 remains New: this Story uses the existing Document/Section/Row interfaces
and introduces no adapter/profile contract. #528 owns the Wood Reports adapter,
#529 owns paired publication/parity, and #476 owns nightly orchestration/narrative.

## Change detection and persistence

Existing material changes gain `reclassification`, `archival`, and
`deployment_topology` categories; dependency and deployment-path edge changes keep
their existing categories. Diffs point to immutable before/after snapshots carrying
the original evidence. Metadata disappearance is a gap, not reclassification;
replacing a validated field or an explicit tombstone remains authoritative change.
When a complete current tree proves an approved metadata file was deleted, its
old evidence is retired. An omitted field in a surviving file, a failed read,
or a file outside current approvals still retains stale evidence and blocks
publication.

The PostgreSQL diagnostic head still records partial reconciliation. A separately
maintained `successful_head` advances only after complete collection with current
evidence for active repositories (archived historical evidence is retained).
Architecture changes compare consecutive successful baselines; partial runs can
report estate coverage diagnostics but defer architecture changes. Recovery compares
against the last successful baseline so a healthy peer update during an outage is
not lost. Initialization explicitly creates/bootstrap this additive baseline table
under the existing migration lock. No production migration is run during Story
development.

Snapshot decoding reads the **stored** normalized graph, validates identities,
candidate resolution and evidence references, and preserves its content hash.
It does not apply today's normalization rules to immutable historical snapshots.
This preserves old evidence/history and permits meaningful before/after comparisons
as extraction rules evolve. Future schema versions and corrupt records fail closed.

## Validation and delivery

Offline mocked fixtures exercise all evidence families, metadata conflicts,
ambiguous package names, confidence, archival, reference-solution rendering,
configured versus attested deployment, unavailable health, historical snapshots,
and incomplete collection followed by recovery. Disposable PostgreSQL tests verify
successful-baseline persistence and restart/concurrency behavior.

Run `wood repo validate --json` for repository-required formatting, typing,
tests/coverage and pre-commit checks. Preserve its record/logs; application
verification applies only if `[tool.wood.verify]` is declared. Development is local:
no image publication, infrastructure deployment, production mutation or external
document publication. Commit/push/PR creation requires the workflow review boundary.

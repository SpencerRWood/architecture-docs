# Estate coverage

The reviewed registry accounts for 29 active SpencerRWood repositories as of
2026-10-09. Authenticated discovery found 30 active repositories; the existing
Homelab restriction leaves 29 approved active repositories. The reviewed registry
adds pi-config and recovery-verification, removes unavailable wood-agents, and
excludes archived sql-control-cli, woodanalytics-site, wood-data-platform, and
website-marketing-simulation. Archived repositories, homelab, wood-data-platform, and website-marketing-simulation
are outside this supported estate. The registry declares exact approved paths;
reviewers must approve new repositories and paths explicitly. Paths exclude
test fixtures, environment files, credential directories, private keys and state.
Templates are optional supporting repositories; other active repositories are
required. Documentation-only repositories remain supporting repositories.

Homelab has neither approved paths nor an estate expectation and never contributes
to completeness. Explicit references from included repositories remain external,
reference-only dependencies rather than authorizing collection. Use the reviewed
`config/reconciliation.toml` policy when reconciling against previous snapshots:
its Homelab tombstone removes historical Homelab-owned evidence while preserving
included-repository references. A withdrawn approval is explicit removal, not a
failed or partial source read; failures in the supported estate still preserve
stale evidence and block publication.

## Expectations

`[estate]` has `repositories` and `domains` arrays. Every approved repository must
have a matching expectation; an expectation may deliberately name an unapproved
repository, which remains missing until paths are approved. Unknown fields,
duplicate identities, empty expectations, malformed predicates and contradictory
optional core classifications are rejected. Omitting estate configuration permits
bounded collection but cannot establish publication readiness.

```toml
[[estate.repositories]]
name = "SpencerRWood/architecture-docs"
classification = "core"
required = true

[[estate.domains]]
name = "data_storage"
node_kinds = ["database", "storage"]
fact_prefixes = ["compose.volumes"]
```

Core and service repositories need current configuration or executable facts beyond
a parser's source-kind marker. Supporting repositories may be represented by parsed
documentation. These expectations never change source authority or resolve drift.
Required repositories cannot be replaced by evidence from optional repositories.
Optional omissions remain explicit diagnostics. A failed source still blocks
publication under the existing evidence policy, even when its repository is optional.

The supported estate requires deployment/release, runtime/infrastructure,
data/storage, automation/orchestration, secrets and runbooks evidence. A domain
matches configured typed node kinds or fact prefixes in a successfully covered
source of an expected repository. Repository names, GitHub metadata and references
to other repositories do not establish those domains. Evidence shows declarations
and executable structure, never deployed health or complete procedure grounding.
Ordinary document gaps and source conflicts remain separate diagnostics.

## Persistence and publication

`estate.contract` is deterministic local configuration evidence serialized with
the collection; graph normalization does not turn it into a node. The contract is
replaced on each run, never rescued from prior stale configuration. Source coverage
must match the observation's repository, source, collector and revision; when a
pinned inventory exists, its revision and paths must also match. A repository with
source failures cannot satisfy coverage. Stale retained graph nodes do not count.

Snapshots expose `estate_coverage` and `estate_state`. Missing required repositories,
missing domains or missing expectations yield `incomplete_estate`, even when
`CollectionResult.complete` is true. Every rendered document carries the repository
and domain diagnostics. Coverage changes create material history entries independently
of ordinary topology changes; serialized snapshot evidence reproduces coverage after
restart. Incomplete candidates make zero Drive requests and leave file IDs, committed
publication metadata, pending intents and existing document contents unchanged.

Live collection is read-only, uses pinned revisions and bounded GET requests, and
retains normalized metadata only. Refresh the approved registry after a repository
review; do not add wildcard discovery, infer hosting, or write source bodies to logs.

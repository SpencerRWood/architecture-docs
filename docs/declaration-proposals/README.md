# Explicit declaration proposals

These are additive draft fragments based on sources pinned at the revisions recorded in [provenance.json](provenance.json). The supported estate now contains 29 repositories with 81 proposed declarations. The historical Homelab fragment (seven declarations) and its provenance are retained only as withdrawn review material: Homelab is outside collection and coverage scope and its declarations must not be adopted for this estate. `website-marketing-simulation` and `wood-data-platform` are also intentionally excluded.

Source-derived declarations include their supporting source URL and blob identifier. Ownership is an explicit operator declaration: the default owner is `SpencerRWood`, with no exceptions supplied. The sidecar records the reason for the declaration, outstanding decisions, and separate operational metadata collected during [the follow-up research](research.md). Identifiers summarize existing evidence; narrative does not supply missing policy. Protected configuration was parsed on the deployment host to return only project identifiers and provider-configuration booleans. Infisical inventory requests explicitly disabled value retrieval. No credential values were output or retained.

## Review and adoption

Review the fragment for each repository and answer [the remaining decisions](decisions.md). Merge nodes by `kind` and `name` into that repository's root `architecture.toml`; merge only proposed attributes and preserve existing nodes and edges. A fragment is not a replacement file.

Before adoption, recheck the pinned source revision against the target repository. Add an exact `architecture.toml` approval to that repository's entry in the collector registry where absent. Recollect at the new revision so declarations and procedure references have current, consistent provenance. This folder is outside the source allowlist and is not an evidence overlay or a runtime configuration loader.

Repository identities use the full GitHub name. The portfolio purpose is operator-declared: exhibit portfolio projects and write about analytics engineering. Template purposes describe templates, not deployed applications. The automerge repair purpose describes its bounded foundation, not a complete deployed recovery workflow.

GitHub Actions secret references receive only supported CI injection metadata. Follow-up metadata establishes repository scope for three promotion-token copies, and reusable-workflow contracts declare requiredness. A same-named Infisical CI key is confirmed separately; synchronization between that key and the GitHub copies remains unverified. Workflow secret-store entries may contain non-sensitive settings such as DBT_HOST. The local-development DBT_PASSWORD usage has a separate identity because its documented location cannot be applied to the CI consumer without evidence.

The rollback phase declarations reference the documented configuration rollback contract. They attest to neither a successful recovery exercise nor database restoration. The Events deployment document also supplies all four deployment phases. A homelab database-recovery reference is available, but historical recovery inputs do not establish periodic backup guarantees or numeric recovery targets. The remaining runbook categories still need sufficient authoritative procedures before their phases can be declared.

## Repository index

| Repository | Fragment | Proposed nodes | Decisions |
| --- | --- | ---: | --- |
| architecture-docs | [fragment](architecture-docs.toml) | 1 | none |
| automerge-repair | [fragment](automerge-repair.toml) | 1 | none |
| codex-config | [fragment](codex-config.toml) | 1 | none |
| codex-runtime | [fragment](codex-runtime.toml) | 1 | none |
| events-service | [fragment](events-service.toml) | 12 | ci-secret-mappings |
| homelab (withdrawn, out of scope) | [historical fragment](homelab.toml) | 7 | do not adopt |
| infrastructure | [fragment](infrastructure.toml) | 13 | recovery-authority |
| openproject-reports | [fragment](openproject-reports.toml) | 4 | ci-secret-mappings |
| portfolio-website | [fragment](portfolio-website.toml) | 2 | ci-secret-mappings |
| rag-service | [fragment](rag-service.toml) | 2 | ci-secret-mappings |
| sql-control-cli | [fragment](sql-control-cli.toml) | 1 | none |
| synthetic-website-analytics | [fragment](synthetic-website-analytics.toml) | 1 | none |
| synthetic-website-analytics-platform | [fragment](synthetic-website-analytics-platform.toml) | 1 | none |
| synthetic-website-data | [fragment](synthetic-website-data.toml) | 1 | none |
| synthetic-website-dbt | [fragment](synthetic-website-dbt.toml) | 9 | ci-secret-mappings |
| template-dbt | [fragment](template-dbt.toml) | 7 | ci-secret-mappings |
| template-fastapi-react | [fragment](template-fastapi-react.toml) | 2 | ci-secret-mappings |
| template-fastapi-service | [fragment](template-fastapi-service.toml) | 2 | ci-secret-mappings |
| template-python-analytics | [fragment](template-python-analytics.toml) | 1 | none |
| template-python-api-client | [fragment](template-python-api-client.toml) | 1 | none |
| template-python-cli | [fragment](template-python-cli.toml) | 1 | none |
| template-python-dagster | [fragment](template-python-dagster.toml) | 1 | none |
| template-python-library | [fragment](template-python-library.toml) | 1 | none |
| template-synthetic-data | [fragment](template-synthetic-data.toml) | 1 | none |
| template-web-automation | [fragment](template-web-automation.toml) | 1 | none |
| wood-agents | [fragment](wood-agents.toml) | 1 | none |
| wood-charts | [fragment](wood-charts.toml) | 1 | none |
| wood-reports | [fragment](wood-reports.toml) | 1 | none |
| wood-tools | [fragment](wood-tools.toml) | 1 | none |
| workflows | [fragment](workflows.toml) | 9 | ci-secret-mappings |

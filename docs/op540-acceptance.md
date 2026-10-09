# Story #540: classification and evidence implementation

This record maps [Story #540](https://projects.woodhost.cloud/work_packages/540)
to the implementation already merged into this repository. The classification,
collection, graph and document work landed in [PR #9](https://github.com/SpencerRWood/architecture-docs/pull/9),
following the repository metadata normalization in [PR #8](https://github.com/SpencerRWood/architecture-docs/pull/8).
[PR #10](https://github.com/SpencerRWood/architecture-docs/pull/10) subsequently
fixed authoritative metadata-file deletion handling. The Story delivery branch
retains those changes and records their acceptance mapping; it does not replace
them with the earlier uncommitted prototype.

The contract and evidence rules are documented in
[architecture-classification.md](architecture-classification.md). The following
matrix maps the Story's numbered acceptance criteria to concrete implementation
and offline regression coverage. Test presence is not a passed validation result;
closure requires the saved Wood validation record and successful CI for the
delivery revision.

| Acceptance | Implementation | Representative regression coverage |
| --- | --- | --- |
| 1. Minimal metadata, equivalent file, vocabulary and conflicts | `classification.py`; `collectors/architecture.py`; `collectors/content.py` | `test_metadata_minimal_vocabularies_and_project_reuse`, `test_classification_only_architecture_file_without_schema_version`, `test_equivalent_metadata_conflicts_and_malformed_sources`, `test_merged_estate_classification_vocabulary` |
| 2. Eight groups, repository roles and archival | `classification.py`; `architecture_graph.py`; `renderers/architecture.py` | `test_relationship_states_static_corroboration_and_group_roles`, `test_approved_collection_archive_and_security_boundaries` |
| 3. Reference-solution identity and evidenced flow | `architecture_graph.py`; `renderers/architecture.py` | `test_all_configuration_evidence_families_and_solution_flow`, `test_solution_without_flow_and_empty_dependency_views` |
| 4. All specified relationship evidence families | `collectors/architecture.py`; `architecture_records.py`; `architecture_graph.py` | `test_python_dependencies_lockfiles_and_confidence`, `test_all_configuration_evidence_families_and_solution_flow`, `test_supported_workflow_environments_and_infrastructure_contracts`, `test_relationship_states_static_corroboration_and_group_roles` |
| 5. Configured versus attested deployments and independent health | `collection.py`; `collectors/architecture.py`; `renderers/architecture.py` | `test_deployment_configuration_is_not_actual_deployment_or_health`, `test_deployment_receipts_verify_revision_and_preserve_unavailable_evidence` |
| 6. Existing deterministic document views and evidence coverage | `renderers/overview.py`, `catalog.py`, `deployment.py`, `data.py`, `architecture.py` | Classification tests exercise Overview, Catalog, Deployment and Data views; existing rendering tests cover all fifteen document identities and deterministic output. |
| 7. Successful-baseline architecture changes | `reconciliation.py`; `model.py`; `store.py` | `test_snapshot_changes_and_missing_metadata_do_not_reclassify`, `test_deleted_metadata_source_retires_only_verified_absent_evidence`, `test_deployment_changes_are_detected_only_after_complete_collection`; snapshot-store tests cover successful-head persistence and recovery. |
| 8. Scope, sanitization, conflicts and known-good preservation | Existing registry and collectors; `estate.py`; `graph.py`; `reconciliation.py` | `test_approved_collection_archive_and_security_boundaries`, `test_description_excludes_sensitive_or_unbounded_prose`, `test_record_boundary_rejects_unsupported_claims_without_values`, `test_historical_projection_is_not_reinterpreted_and_corruption_fails`; existing estate/reconciliation tests. |
| 9. Required development checks and honest runtime limits | Existing `[tool.wood.validation]` checks; offline and disposable PostgreSQL tests | `wood repo validate --json` supplies the revision-bound result and full log paths. No `[tool.wood.verify]` application contract is declared, so no application runtime verification is claimed. |
| 10. Contract, rules, outputs and coordination documentation | `docs/architecture-classification.md`; this acceptance mapping | Existing #471/#472/#473/#521 foundations are reused. #523 owns the formal dual-rendering contract, #528 its adapter, #529 paired publication, and #476 scheduling. |

## Limits and next work

Static configuration and a successful collection do not prove a live deployment,
image digest or runtime health. Missing attestation, reference-only entities,
unresolved relationships and classification conflicts remain visible. Live
homelab collection remains excluded. Fixtures demonstrate evidence-family support
without authorizing additional repositories, paths or external services.

The existing Overview and other graph-derived views satisfy #540's evidence and
classification scope, but their dense presentation requires further composition
work. [Story #541](https://projects.woodhost.cloud/work_packages/541) owns that
editorial redesign, reusable semantic projections, complete evidence retention,
actual Wood Reports PDF rendering and every-page visual acceptance. This closure
does not assert that #541's PDF or design acceptance is complete.

No image publication, infrastructure deployment or production document write is
part of this delivery reconciliation. The earlier local prototype is preserved
in Git's stash with the message `op540 superseded local draft preserved before
delivery reconciliation 2026-10-09`; it must not be reapplied over the newer fixes.

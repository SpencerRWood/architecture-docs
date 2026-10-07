# Remaining operator decisions

Confirmed: Spencer Wood (`SpencerRWood`) is the default owner. The portfolio site exhibits portfolio projects and hosts writing about analytics engineering. `website-marketing-simulation` is excluded from the estate. These decisions were explicitly declared by the operator.

Follow-up [research](research.md) found the Infrastructure Dev project identifier, current Events provider selection, CI storage scopes and contracts, and documented recovery references. These no longer need operator lookup.

## Decisions not yet confirmed

The operator is unsure whether these decisions have been made. The reviewed sources do not establish them. Record each as unconfirmed; absence from those sources does not prove that no decision exists. No new policy is required to describe the current architecture accurately.

| Topic | Current documentation status | Future decision or evidence needed |
| --- | --- | --- |
| Acceptable data loss and downtime | Targets not confirmed. | Recovery point/time objectives per critical service or database, or an explicit decision to leave targets undefined. |
| Recurring backups and retention | Schedule and retention guarantees not confirmed. Historical migration backups are documented. | Agreed scope, frequency, retention, and restore verification, followed by evidence of implementation and freshness. |
| Promotion-token distribution | Both stores contain the same key name; the distribution and rotation policy is not confirmed. | Decide or locate the authoritative source and distribution process, then verify how the GitHub copies are maintained. |

These may be undecided policies, undocumented decisions, or unverified implementations. Keep them as open decisions until an authoritative source resolves their status. Do not invent defaults or treat the existing historical backups as periodic guarantees.

## Unresolved implementation and evidence

- GitHub holds `INFRASTRUCTURE_PR_TOKEN` in three repository secret stores. Infisical also holds that name in the CI project. Neither store metadata nor the reviewed resolver establishes synchronization, rotation, or distribution between these copies. Record the authoritative distribution process when it is available; do not infer equality from matching names.
- Several template/dbt references have no stored GitHub secret, and reusable dbt inputs are optional at the contract boundary. Determine requirements when a concrete consumer enables the relevant job. The Events caller uses a separately named conditional promotion credential that is absent from its repository store.
- The reviewed documents provide configuration rollback, Events deployment/recovery boundaries, and homelab logical database recovery inputs. A full host rebuild, service restoration exercise, periodic backup policy, release troubleshooting procedure, and Dagster operations procedure remain incomplete evidence. Declared recovery exercises require their own approved target and execution evidence.
- The 29 reference-only workflow identities still require normalization and source resolution. The declaration schema also needs a documented convention for GitHub secret-store locations before provider-specific gaps can be treated as complete.

No credential values are needed. The draft records known facts and explicit limitations; it does not attest to successful provider delivery or a completed recovery exercise.

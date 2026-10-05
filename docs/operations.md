# Secrets Manifest and operational runbooks

Story #474 implements FR-016 (local artifacts), FR-021–025 and
QR-004/005/010/011/012 from the
[Architecture Docs requirements](https://docs.google.com/document/d/1MFw9S34__hR2zclkw4G4j4DIix-Ac7OEQpTziA1EflI/edit).
The existing manual rendering job and local exporter now produce fifteen
documents: six architecture views, Secrets Manifest, and eight runbooks.
Drive publication remains #475; scheduling and narrative remain #476.

## Secret metadata boundary

Approved `architecture.toml` declarations and literal GitHub Actions
`${{ secrets.NAME }}` references supply secret metadata. No Infisical or GitHub
secret-value endpoint is implemented or called. Repository allowlists, pinned
GET-only reads, integrity checks and sensitive-path denial remain unchanged.
Source parsing reads approved repository content in memory and discards bodies;
it never emits literal environment values, workflow commands or exception text.

`secret_reference` nodes accept only `project`, `environment`, `path`, `scope`,
`injection`, `required` and `owner`; all are identifiers. `path` must be absolute;
`required` is `true` or `false`, otherwise omit it for unknown. The owning system
must be explicitly declared with `owner` or an ownership relationship. Repository
namespace and variable names cannot establish ownership, location or secret use.
No value, password, credential, arbitrary description or command field exists.
As with every metadata contract, a publisher must not mislabel a credential as
an identifier; these checks enforce schema and syntax rather than identify every
possible sensitive string. Unknown fields fail closed without echoing values.

```toml
[[nodes]]
kind = "secret_reference"
name = "API_TOKEN"
attributes = { project = "platform", environment = "dev", path = "/api", scope = "runtime", injection = "infisical", required = "true", owner = "api-system" }

[[edges]]
kind = "consumes_secret"
source = { kind = "service", name = "api" }
target = { kind = "secret_reference", name = "API_TOKEN" }
```

This is an illustrative contract, not a claim that this secret or service exists.
Consumers are typed edges and may cross repositories. Secret names are distinct
within repository namespaces; the renderer never merges matching names across
projects or environments. Workflow input aliases and literal inputs are not
secret names. Inherited secrets, indexed expressions, and excluded literal inputs
produce explicit discovery gaps. The collector does not resolve dynamic secret
expressions or infer Infisical locations from workflow environment names.

Every metadata candidate retains source repository/path, revision/blob,
collector, authority and verification. The source revision is the last verified
commit for that evidence, including when retained as stale. Different candidates
retain their own revisions; no timestamp or guessed latest commit is introduced.
Missing fields/consumers/ownership and conflicting location candidates are gaps.
All drift and ambiguity candidates remain visible. Partial collection retains
stale evidence and blocks publication; ambiguity also blocks publication.

## Ordered procedure references

An opt-in `procedure` declaration describes one ordered, reference-only step:

```toml
[[nodes]]
kind = "procedure"
name = "deploy-api"
attributes = { runbook = "deployment-redeployment", phase = "steps", order = "1", script = "scripts/deploy.sh" }
```

This example describes how to encode an existing operational contract; it is not
a new deployment instruction. The procedure declaration establishes assignment
to a runbook and phase. The referenced file establishes the actual invocation,
inputs, conditions and behavior. Existing unclassified procedures remain visible
in architecture views; their names do not establish runbook applicability.

Allowed `runbook` values and stable document filenames:

| Runbook | Filename |
| --- | --- |
| Deployment / redeployment | `runbook-deployment-redeployment.md` |
| Rollback / known-good recovery | `runbook-rollback-known-good-recovery.md` |
| Linux host rebuild / recovery | `runbook-linux-host-rebuild-recovery.md` |
| Secret-reference management | `runbook-secret-reference-management.md` |
| Database provisioning / onboarding | `runbook-database-provisioning-onboarding.md` |
| Release / promotion troubleshooting | `runbook-release-promotion-troubleshooting.md` |
| Service restoration | `runbook-service-restoration.md` |
| Dagster operations | `runbook-dagster-operations.md` |

Allowed `phase` values are `prerequisites`, `steps`, `verification` and
`rollback-recovery`. `order` is a positive decimal string from 1 to 9999.
Specify exactly one `script`, `workflow` or `reference` source path, relative to
the declaring repository. Scripts must be under `scripts/`; workflows must be
under `.github/workflows/`. References may point to approved configuration or
documented contracts. Whitespace, shell syntax, absolute paths, traversal and
wildcards are rejected. Commands, arguments, credentials and arbitrary prose
cannot be represented. Separate declarations encode separate steps; numeric
ordering is deterministic within repository and phase. Duplicate orders are
reported as gaps and withheld. Cross-repository execution paths are not inferred;
declare procedures in the repository that owns the contract.

A grounded step requires verified, successfully parsed approved source evidence
at the same non-null revision as the procedure declaration. Scripts/workflows
require the executable collector's source evidence; generic references may use
configuration or documentation evidence. The artifact cites both sources with
pinned commit/blob metadata. It links to the repository contract instead of
inventing a shell invocation, argument defaults or workflow dispatch conditions.
No script is run and no operational health or recovery success is asserted.

Missing, conflicting, stale or revision-mismatched evidence withholds steps and
identifies the gap. Every family is generated even with insufficient evidence;
missing phases are explicit, including gaps for each participating repository.
Operators must use the linked contract and resolve prerequisites, verification
and recovery gaps before acting. A source reference is not proof that the host
is provisioned or that execution is safe in the current live environment.

Runbook rendering reads only normalized graph/evidence; it never scans files or
calls a service. Stable IDs, Markdown/JSON exports, model versions, semantic
hashes and existing publication blocks carry over from #473. This Story adds
no autonomous processing, secret-management integration, publication or Codex.

## Acceptance evidence

| #474 criterion | Implementation / offline validation |
| --- | --- |
| 1: no secret-value field | Kind-specific `SECRET_FIELDS`; unknown-field, malformed metadata and malicious snapshot tests |
| 2: metadata manifest | `renderers/secrets.py`; metadata fields, ownership gaps, consumers and pinned provenance tests |
| 3: approved discovery | Existing registry and GET-only transport; reference-only workflow extraction; no secret-management client |
| 4: missing/conflicting locations | Manifest location gaps and all graph candidates; drift/ambiguity fixture tests |
| 5: eight separate runbooks | `RunbookKind`, stable `DocumentKind` IDs and integrated exporter / manual Dagster rendering |
| 6: traceable steps | Procedure plus current source grounding, revision match and pinned citations |
| 7: distinct phases | Four stable phase sections with numeric step ordering |
| 8: insufficient evidence | Sparse snapshots, unavailable/unapproved sources, stale evidence and conflicting targets withhold execution |
| 9: exclusion and grounding tests | Offline collector/render/export fixtures, sanitized errors, path/command rejection and restart determinism |

Full repository validation is performed through `wood repo validate --json`.
Tests attest contract and fixture behavior, not live repository completeness or
runtime health. Final `wood story evidence` requires an approved commit/PR and
passed CI, so the saved validation record is prepared at the pre-commit review
boundary and reused after delivery approval.

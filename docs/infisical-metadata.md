# Infisical metadata integration (Story 521)

The repository registry explicitly names `infisical_scope = "infisical.toml"`.
That approval file contains HTTPS origin, exact project/environment/path tuples,
bounds and consumer associations. The checked-in dev approval covers only
Infrastructure Dev (`7ea10433-2eeb-4c57-95a9-b793dd40c7a4`), environment `dev`,
folders `/architecture-docs`, `/rag-service`, `/events-service`,
`/synthetic-website-data` and `/synthetic-website-dbt`. The last two are distinct
consumers with distinct existing folders; collection never renames folders. A root path
approval lists that exact folder only; children require separate approval.
Homelab and wood-data-platform remain outside repository and Infisical scope.
Unknown fields, credentials in configuration, traversal, wildcard scopes and
bindings to unapproved consumers/locations are rejected.

The collector uses GET `/api/v1/workspace/{approved-project}` for project identity
and GET `/api/v4/secrets` with fixed `viewSecretValue=false`,
`expandSecretReferences=false`, `recursive=false`, `includeImports=false` and
`includePersonalOverrides=false`. These switches cannot be configured or
overridden. It has no retrieve-by-key, export, mutation, account/project discovery,
or folder-crawling interface. Responses, item counts and number of exact scopes
are bounded; overflow fails the scope rather than reporting a truncated success.
See the [Infisical listing contract](https://infisical.com/docs/api-reference/endpoints/secrets/list).

Inject a short-lived machine identity access token as
`ARCHITECTURE_DOCS_INFISICAL_METADATA_TOKEN` into the collection process only.
An external runtime credential broker handles authentication/renewal; Architecture
Docs performs GETs only and does not store Universal Auth credentials. Story 521
explicitly permits the existing dedicated `architecture-docs-metadata` machine
identity with the built-in project Viewer role because custom roles are unavailable.
Viewer can read secret values; this is an accepted credential-permission exception,
not a server-enforced metadata-only identity. No custom role or additional access
is required. Keep organization access at `no-access` and project membership limited
to the approved project. The collector remains restricted to the approved
environment/exact paths and fixed server-side value masking. Do not grant Member
or Admin, substitute a deployment identity or a human CLI session, or request
secret values. Missing/expired credentials are collection failures, never fallback
to a value-capable SDK or CLI export. Token expiry does not trigger blind retries.

Infisical masks values server-side in this mode. Before JSON scalar decoding, the
transport boundary scans bytes and projects only metadata identifiers. Excluded
comments, reminders, actor data, tags and arbitrary metadata are never decoded
into Python values. A value-shaped field must be the fixed Infisical mask, empty
or null; any other token fails closed before deserialization. The mask itself is
discarded. `secretValueHidden=true` and exact response project/environment/path
are required for every entry. Even nonempty generic custom `value` metadata is
rejected conservatively. No raw body or exception text crosses the boundary.
Application `Location` accepts only project/name, environment, path, key, object
identifier and numeric object version. Unknown fields are rejected again when
loading persisted observations. **Secret values are never part of the architecture
model, PostgreSQL observations, logs, Markdown, Drive publication, or Codex input.**
Runtime authentication tokens are used only in the transport authorization header.

Classify providers before Infisical lookup. GitHub's `GITHUB_TOKEN` is
GitHub-provided; `${{ secrets.NAME }}` in an ordinary workflow establishes a
GitHub repository/organization secret reference, not an Infisical key. This
documents the reference, not secret existence or accessibility. Reusable workflow
secret aliases and `inherit` follow the caller chain, including nested calls and
secrets forwarded through workflow inputs. Ordinary non-secret inputs are excluded.
Caller names and all forwarding provenance are retained; the centralized workflows
repository does not own the caller's secrets. Cycles, missing bindings/callers,
dynamic references and mismatched explicit commit pins remain uncertainty.
Declared optional secret parameters with no forwarding callers are shown separately
as unbound workflow interfaces, rather than unresolved consumer locations. This
classification retains the parameter's declared required/optional metadata.
See [GitHub reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).

Runtime location precedence is explicit per-secret alias/location, then service
location, repository location, and finally an exact repository-name folder match
only when one project/environment/path is available in approved metadata. There
is no fuzzy, account-wide or similarly named folder matching. Explicit metadata
bindings remain supported in this registry's approval contract. Per-repository
declarations can instead use the named project in `architecture.toml`:

```toml
[secrets]
project = "Infrastructure Dev"
environment = "dev"
path = "/events-service"

[[secret_locations]]
service = "worker"
project = "Infrastructure Dev"
environment = "dev"
path = "/worker"
```

Declarations select intended locations; they do not grant collection permission.
Every collected folder still requires an explicit approval scope. Infisical key
names default to the runtime consumer's secret name only after its location has
been determined. `infisical_key` or a per-secret binding provides an explicit alias.
`mapped` requires matching key metadata; `location-declared` means location is
known without key evidence, and `missing-key` means a complete approved listing
did not contain that key. Neither means unknown provider/location. `unresolved`
means provider/location or caller chain cannot be determined; ambiguous evidence
and stale collection continue to block publication. Explicit alias example:

```toml
[[bindings]]
repository = "SpencerRWood/architecture-docs"
project = "7ea10433-2eeb-4c57-95a9-b793dd40c7a4"
environment = "dev"
path = "/architecture-docs"
consumer = "DATABASE_URL"
key = "ARCHITECTURE_DOCS_DATABASE_URL"
injection = "runtime"
```

This illustrates an alias contract; it does not declare that the current application
consumes `DATABASE_URL`. Bindings select existing consumer references and do not
invent consumers for every key in an Infisical folder.

The Secrets Manifest leads with consumer-to-location tables, followed by unresolved
references, ambiguous candidates, approved metadata without known consumers,
discovery limitations, unverified repository declarations and source provenance.
Source evidence includes the exact metadata scope, object/key identifier and
version, collector identity and verification state. An inventory
`metadata-sha256:` digest is a repeatable metadata observation revision, **not an
immutable Infisical source revision or Git commit**. Repository evidence retains
its real commit/blob; local integration contracts retain null revision and a
content digest. No immutable source version is fabricated.

Infisical coverage and failures are separate from GitHub inventories.
Secret-manager evidence uses its own `infisical/metadata` source namespace;
an Infisical outage does not invalidate successful GitHub repository coverage.
Only a successful complete listing of the same Infisical scope can prove location
absence. Failed, missing or oversized listings retain prior metadata as stale,
surface normalized failures and block publication with zero remote writes.
A successful GitHub scan cannot delete those locations. Explicit approval
withdrawal requires the existing reviewed reconciliation removal policy.

Offline CI uses mocked transports for masks, aliases, exact/ambiguous/unresolved
matches, unused inventory, environment/path separation, approvals, read-only
requests, value-shaped persisted input, logs/rendering and outage preservation.
Run `wood repo validate --json`. For live integration, inject the approved token,
collect the approved dev inventory and regenerate the saved Story 521 candidate;
inspect the local Secrets Manifest before publishing. Never obtain runtime data
by exporting values to inspect the document.

To regenerate the existing Story 521 integration corpus with fresh dev metadata:

```sh
uv run python -m architecture_docs.integration \
  /private/tmp/op521-homelab-corpus/snapshot.json \
  /private/tmp/op521-infisical-corpus \
  --worktree-contract architecture.toml
```

The runtime broker must inject the metadata token before this command starts.
Add `--refresh-workflows` and inject `ARCHITECTURE_DOCS_GITHUB_TOKEN` to re-extract
approved workflow blobs at retained immutable revisions. This does not advance
repository heads or load unapproved sources; failures retain known-good evidence
and block publication. Credentials remain process-only.
It reports bounded counts and normalized failures and writes fifteen local
documents, a snapshot and `review.json`. Exit 2 means live mappings were not
verified, including missing credentials; a failed candidate is never published.
Inspect `secrets-manifest.md` manually. Retained pinned GitHub observations are
identified honestly; the optional local contract has null Git revision and a
worktree content digest. This command never retrieves application credentials,
writes PostgreSQL, or publishes Drive documents unless publication is explicitly
requested with `--publish-parent`.

Local regeneration is not publication. `review.json` records material categories,
affected documents relative to the previous local snapshot, and whether publication
was requested. `diff.json` retains the deterministic change record. Secret location
identifiers/versions, approval bindings, mapping status and resolved consumer
metadata contribute to secret-topology changes; Secrets Manifest also hashes its
typed Infisical semantic inputs independently of ordinary Git citation revisions.

After inspecting the local candidate, inject the Google access token and PostgreSQL
URL into the process and rerun with `--publish-parent <approved-parent-id>`. This
explicit option uses the normal revision-checked, durable publication ledger.
It compares each rendered artifact against what is actually committed in Drive,
even when the previous local snapshot is identical and `affected_documents` is
empty. A successful result records per-artifact publication states and readback;
missing live mappings, incomplete estate or stale/ambiguous evidence prevents
all remote writes. Failed publication exits nonzero. Inspect the actual published
Secrets Manifest before claiming the Drive corpus is refreshed.

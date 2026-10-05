# PostgreSQL persistence and cutover

Inject `ARCHITECTURE_DOCS_DATABASE_URL` from Infisical. For dev it is stored in
Infrastructure Dev, `dev:/architecture-docs`. Ansible's selected application
entry provisions the `architecture_docs` login role and database on the existing
Infrastructure PostgreSQL 16 endpoint. The application role has no superuser,
database/role creation, inheritance, replication or RLS-bypass privileges.
Application-owned initialization creates the `architecture_snapshot` and
`architecture_publication` schemas, each with a version record. Unknown versions
fail closed. No credentials are retained in application ledgers or diagnostics.

`SnapshotStore()` reads the injected URL. Its head/history/run transaction uses
a transaction advisory lock, so concurrent processes compare with the last
committed head. Publication uses a session advisory lock that survives its
intent commits and remote API calls. All publishers for one remote scope must
use the same database, OAuth application, parent ID and namespace. Use direct
PostgreSQL connections, not a transaction-pooling proxy. Losing database access
stops publishing at its next database operation; in-flight remote writes may
have completed. Persisted intents and revision guards support recovery from
that uncertainty without blindly retrying creates or conflicting updates.

For a new installation, provision the database through the reviewed Ansible dev
manifest, inject the URL, and initialize `SnapshotStore()` or run reconciliation.
The first publication initializes its separate schema. No local state directory
or SQLite path is required. Keep database backup and recovery under infrastructure
ownership. This implementation does not apply Ansible, deploy application code,
refresh Google access tokens, or schedule publishing.

## Existing SQLite ledgers

Stop every collector/reconciler/publisher using the old ledgers before cutover.
Preserve consistent backups of both SQLite databases and any WAL files; use
SQLite's backup API or an attended backup after all connections are closed.
Record the current snapshot ID, history/run counts, publication scope, all
Drive identities and pending intents. Back up the PostgreSQL target as well.

The explicit one-time importer reads schema-1 SQLite files in read-only mode:

```sh
uv run python -m architecture_docs.migration \
  --snapshots /backup/snapshots.sqlite3 \
  --publication /backup/publication.sqlite3
```

Inject the destination URL into that process from Infisical. The target history,
identities and events must be empty; populated targets are rejected. The importer
verifies snapshot hashes, preserves run/event IDs and pending/committed metadata,
and imports all data in one transaction under both writer locks. Failed data
imports roll back; sources are never modified. Schema initialization can remain
after failure. A successful import refuses a second import into populated state.
SQLite is supported only by this explicit migration command, never by runtime
stores, Dagster settings or the publication CLI.

Compare the returned counts, latest snapshot ID and Drive identities with the
recorded source state before enabling the new runtime. Keep the same parent and
namespace; the imported scope deliberately rejects drift. Verify uncertain
creates/updates using the publisher's recovery procedure before accepting new
writes. Never initialize an empty publication ledger against an existing managed
Drive hierarchy and assume it is equivalent to an imported ledger.

Rollback before any new accepted writes can return to the frozen legacy
application and ledgers. Once PostgreSQL has accepted new history or Drive
publication intents, do not run the old SQLite writer or restore stale copies:
stop writers, preserve both states, and reconcile an attended recovery first.
No automated destructive downgrade is supplied.

## Validation

Storage tests launch a private disposable PostgreSQL 16 Docker container and
use fresh databases owned by a restricted application role. They never read
the runtime URL. Docker must be available locally and in validation CI; database
tests fail rather than silently skip when it is unavailable. Google API requests
remain mocked. The suite exercises rollback, restart persistence, competing
connections, session locks across commits, schema rejection and explicit import
with preserved remote identities and pending write recovery.

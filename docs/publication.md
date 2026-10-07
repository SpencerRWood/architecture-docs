# Stable architecture publication

Story #475 / AD-R1-06 implements publication of the fifteen deterministic
artifacts from #473/#474. The publication surface is native Google Docs using
the existing renderer's human-readable Markdown text, including evidence tables,
Mermaid source and pinned provenance. It does not convert Markdown into rich
tables or rendered diagrams. Native file identity preserves the collaboration
surface across updates.

## Configuration and hierarchy

The caller supplies an approved parent Drive folder ID, a PostgreSQL URL
injected from Infisical, and an OAuth access token.
The parent contains one managed `Architecture` folder. Seven architecture
documents live there; the eight separate runbooks live in its `Runbooks` folder.
Both folders and every document have stable managed identities.

The OAuth application needs `drive.file` access to the approved parent and
managed files; both Drive and Docs APIs must be enabled. Acquire/refresh the token
outside this library. A personal OAuth authorization or shared-drive-aware
deployment credential must supply appropriate access; this adapter currently uses
the standard My Drive files surface and does not configure shared-drive flags.
Never store a token in the repository or ledger. The current implementation does
not establish production credentials, deploy storage, or schedule a run.

Estate completeness is a publication prerequisite independent of source success.
`incomplete_estate` candidates make zero remote requests and retain committed file
identities, content metadata and pending intents. Every artifact reports the reason
`incomplete_estate`. See [estate coverage](estate-coverage.md).

Each scope is derived from the parent ID and explicit namespace (default
`architecture-docs`). Drive private `appProperties` identify that scope and the
stable artifact kind. Searches are restricted to the exact parent and both
properties. Names alone never identify or adopt a file. Existing unrelated files,
sharing and folders are not changed. Duplicate matches, pagination, missing or
moved bound files, foreign MIME types and metadata disagreement fail closed.
Use the same OAuth application because private properties are application scoped.

## Durable state and retry boundaries

Use one database and publisher authority per publication scope. Schema
`architecture_publication` retains publication state separately from
`architecture_snapshot`. A PostgreSQL session advisory lock excludes other
publishers connected to the same database, across commits and remote requests,
and is released when the connection closes. Transactional schema initialization
refuses unsupported versions. Back up both schemas together.
Never use two distinct databases for the same remote scope: their locks cannot
coordinate with each other. Use a direct PostgreSQL connection; session locks
are incompatible with transaction-pooling proxies.
Changing parent or namespace requires a separately reviewed scope and ledger;
the existing ledger rejects configuration drift.

The ledger stores file IDs, creation intents, committed/pending hashes, snapshot
IDs, source provenance and bounded per-artifact event records. It stores no raw
source text, document bodies, API responses, credentials or exception messages.
The normalized snapshots and diffs remain in `SnapshotStore`; Drive stores only
current human-readable artifacts. Per-artifact states are `published`,
`unchanged`, `recovered`, `blocked` or `failed`. A successful run requires every
artifact to be published, unchanged or recovered. CLI exit codes are 0 for complete,
1 for incomplete publication and 2 for invalid input/configuration or store failure.

A creation intent is committed **before** the request. On retry the publisher
searches for its managed identity and binds an existing file. Native Docs do not
support pre-generated Drive IDs, so an uncertain creation with no visible result
becomes `creation_unconfirmed` and never issues another blind create. Definitive
API rejections permit a later retry. When an uncertain request did create the file,
search/readback recovers its identity automatically without duplication.

If the request never reached Google or the process stopped before sending it,
the intent stays blocked. An operator must establish the request's outcome before
repairing that intent; absence from one listing is insufficient. Do not delete
the ledger or clear pending state just to force a retry. Switching OAuth
applications or losing the ledger requires inspected identity/state recovery;
existing nonempty documents cannot be adopted as writable without their committed
content hashes. A restored ledger must be reconciled with the remote state.

Before updating a document, the publisher compares its current body hash with
the committed local hash. Unexpected body edits or unsupported structure block
that document. The update deletes/inserts the body in one atomic Docs batch with
`requiredRevisionId`; a concurrent collaborator edit rejects the whole batch.
An intent is committed before the write, and the new body is read back before
advancing committed state. An uncertain write is recovered when remote text
matches the pending hash; if the prior body remains, the update can be retried.
Different text is a conflict requiring human resolution. Native file IDs remain
unchanged and no file is deleted, recreated, or blindly overwritten.

## Material change and evidence safety

The whole document set is rendered and validated before any Drive request.
Malformed models, incomplete sets or rendering failure cannot partially overwrite
known-good output. Snapshot publication blocks apply conservatively to all
artifacts because the current normalized model exposes a global block. No Drive
requests occur for a fully blocked run. Remote failures are isolated per artifact,
with successful artifacts reused on the next run.

Document semantic hashes exclude provenance-only revisions and blob churn.
An unaffected document retains its actual published snapshot and source citations;
the ledger does not relabel old text as a newly published snapshot. The local
event still records which input snapshot was evaluated. Changed documents update
their whole body, including the new snapshot and citations. Targeting is at the
document level: the Markdown renderer's shared footnote/provenance area and
document-wide snapshot marker are not independent section update units. A changed
presentation alone (for example enabling source links) does not force an otherwise
semantically unchanged document to be rewritten.

Generated document bodies are managed output. Human commentary belongs in native
comments or a separate document. Human body edits require reconciliation before
the next update. The publisher does not preserve rich formatting inside a changed
generated body or merge arbitrary edited sections. Unsupported tables, headers,
footers, inline objects, suggestions and tabbed API responses block writes.

## Acceptance evidence

Tests with disposable PostgreSQL and mocked Google APIs in `tests/test_publication.py` cover:

- Native hierarchy and all fifteen artifacts, stable identities across restarts,
  and per-artifact source snapshot/provenance (AC 1–3; FR-013–016/020).
- No-change and provenance-only churn without writes, material updates in place,
  and UTF-16 indexing (AC 4; FR-017–018).
- Partial evidence with zero remote writes, preflight render failure, human-edit
  conflicts, revision rejection and readback protection (AC 5–6; FR-019/053).
- Duplicate suppression, missing identities, scope guards, single-writer locks,
  ambiguous creation and update recovery (AC 7/9; QR-006/008).
- Database publication metadata/events, separate snapshot history, bounded
  responses and sanitized failures (AC 8; FR-050–051; QR-009–010).

Tests use mocked HTTP transport and representative normalized snapshots. They
do not attest live Drive permissions, native API interoperability, deployed database
configuration, or a production publication. A live smoke publication needs an approved
parent and runtime credentials in a separate delivery/deployment step.

API authorities: [Drive create](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/create),
[private properties](https://developers.google.com/workspace/drive/api/guides/properties),
[native-file ID restrictions](https://developers.google.com/workspace/drive/api/guides/create-file),
and [atomic Docs updates and revision control](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate).

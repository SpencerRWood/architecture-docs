# Repository guidance

- Use Wood Tools for Story, validation, evidence, and delivery operations.
- Collect only explicitly approved repositories and paths, using GET requests.
- Emit deterministic metadata with provenance; never retain raw source text,
  secret values, response bodies, or exception messages in observations or logs.
- Treat partial and failed collection as incomplete evidence, never deletion.
- Keep collection independent of Codex. codex-runtime owns provider capacity.
- Graph reconciliation owns evidence, conflicts, snapshots, and material changes.
- Render documents only from normalized snapshots and their provenance.
- Secrets and runbooks retain only typed metadata and repository-contract references.
- Drive publication consumes deterministic artifacts and requires an explicitly
  approved parent folder and durable publication state. Keep failed or partial
  runs from replacing known-good documents; never blindly retry uncertain creates.
- Nightly scheduling and narrative belong to later Stories.
- Tests use offline representative repositories and mocked HTTP transports.
- Preserve the published Dagster template's validation, packaging, and release.

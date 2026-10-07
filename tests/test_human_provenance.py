"""Human documents keep named provenance; machine artifacts retain exact evidence."""

import re
from dataclasses import replace

from architecture_docs.infisical_scope import Binding, Scope
from architecture_docs.model import CollectionResult, Failure
from architecture_docs.reconciliation import reconcile
from architecture_docs.renderers import render_documents
from architecture_docs.renderers.artifacts import DocumentKind, human_text
from test_infisical import contract, location, reference
from test_reconciliation import complete, node, observation
from test_rendering import document, representative

PROJECT = "7ea10433-2eeb-4c57-95a9-b793dd40c7a4"
OBJECT = "c4cc9c18-b5a7-448b-9a2a-38ae6d3dff44"
OPAQUE = re.compile(
    r"\b[0-9a-f]{32,}\b|\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b"
)


def test_all_human_documents_keep_named_sources_and_internal_evidence() -> None:
    scope = Scope(PROJECT, "dev", "/api")
    baseline = representative()
    facts = (
        reference(),
        location(scope, object_id=OBJECT),
        contract((Binding("fixture/api", PROJECT, "dev", "/api"),), (scope,)),
        observation(
            "workflow.uses",
            "fixture/workflows/.github/workflows/release.yml@" + "c" * 40,
        ),
        observation(
            "github.path",
            ".github/workflows/release.yml",
            "github:actions/workflows/374935068",
        ),
        observation("github.tag_name", "v1.2.3", "github:releases/123456789"),
    )
    snapshot = reconcile(
        replace(
            baseline.collection,
            observations=(*baseline.collection.observations, *facts),
        )
    )[0]
    before = snapshot.to_json()
    bundle = render_documents(snapshot)
    assert len(bundle.documents) == 15
    for doc in bundle.documents:
        markdown = doc.to_markdown()
        assert not OPAQUE.search(markdown), doc.id
        assert "metadata-sha256:" not in markdown
        assert "Deterministic snapshot" not in markdown
        assert "[^s-" not in markdown
        assert "374935068" not in markdown
        assert "123456789" not in markdown
        assert "Generation state:" in markdown
        assert "Publication blocked:" in markdown
        assert "Estate coverage" in markdown
        assert markdown == doc.to_markdown()
        for source in doc.sources:
            assert source.id not in markdown
            if source.blob:
                assert source.blob not in markdown
            assert source.id in doc.to_json()
        if doc.sources:
            assert re.search(
                r"\[\^\d+\]: (?:\[)?(?:GitHub|Infisical|Local worktree|Configuration):",
                markdown,
            )
    manifest = document(snapshot, DocumentKind.SECRETS)
    markdown = manifest.to_markdown()
    assert "Infisical: Infrastructure Dev / dev / /api / TOKEN" in markdown
    assert "| fixture/api |" in markdown
    assert "| mapped |" in markdown
    assert PROJECT in manifest.to_json()
    assert OBJECT in manifest.to_json()
    assert snapshot.id in bundle.to_json()
    assert any(source.blob and source.revision for source in manifest.sources)
    assert snapshot.to_json() == before
    assert "github:actions/workflows/374935068" in before
    assert "github:releases/123456789" in before
    catalog = document(snapshot, DocumentKind.CATALOG).to_markdown()
    assert "release v1.2.3" in catalog
    assert ".github/workflows/release.yml \\(workflow metadata\\)" in catalog
    assert bundle.to_json() == render_documents(snapshot).to_json()


def test_stale_provenance_keeps_status_and_short_last_verified_commit() -> None:
    fact = observation("architecture.node", node("api", host="linux"))
    first = reconcile(complete(fact))[0]
    stale = reconcile(
        CollectionResult(
            failures=(Failure(fact.collector, fact.provenance, "access_denied"),)
        ),
        first,
    )[0]
    markdown = document(stale, DocumentKind.RUNTIME).to_markdown()
    assert stale.publication_blocked
    assert "Prior known-good evidence retained" in markdown
    assert "stale; commit " + "a" * 12 in markdown
    assert "a" * 40 in stale.to_json()
    assert not OPAQUE.search(markdown)
    assert "GitHub: fixture/service/architecture.toml" in markdown


def test_ambiguous_identical_locations_use_minimal_distinguishing_object_ids() -> None:
    scope = Scope(PROJECT, "dev", "/api")
    other = "c4cc9c18-aaaa-448b-9a2a-38ae6d3dff44"
    snapshot = reconcile(
        CollectionResult(
            (
                reference(),
                contract((Binding("fixture/api", PROJECT, "dev", "/api"),), (scope,)),
                location(scope, object_id=OBJECT),
                location(scope, object_id=other),
            )
        )
    )[0]
    markdown = document(snapshot, DocumentKind.SECRETS).to_markdown()
    assert snapshot.publication_blocked
    assert "ambiguous" in markdown
    assert "object c4cc9c18-b5a" in markdown
    assert "object c4cc9c18-aaa" in markdown
    assert not OPAQUE.search(markdown)
    assert OBJECT in snapshot.to_json()
    assert other in snapshot.to_json()


def test_opaque_conflict_candidates_stay_distinguishable_without_full_uuids() -> None:
    first = observation("architecture.node", node("api", host=PROJECT))
    second = observation("architecture.node", node("api", host=OBJECT), "other.toml")
    snapshot = reconcile(complete(first, second))[0]
    markdown = document(snapshot, DocumentKind.RUNTIME).to_markdown()
    assert snapshot.publication_blocked
    assert "Competing candidates" in markdown
    assert "no preferred value" in markdown
    assert PROJECT[:8] in markdown
    assert OBJECT[:8] in markdown
    assert not OPAQUE.search(markdown)
    assert PROJECT in snapshot.to_json()
    assert OBJECT in snapshot.to_json()


def test_provider_ids_and_pinned_workflow_refs_are_presentation_only() -> None:
    identifier = "1GqKMTL6kZ9gtjmPRxaAnTz4Ur-IHw0iOwcbCXrMtCbg"
    assert identifier not in human_text("drive_id: " + identifier)
    assert human_text("release.yml@" + "c" * 40) == "release.yml@" + "c" * 12
    assert (
        human_text("ARCHITECTURE_DOCS_DATABASE_URL") == "ARCHITECTURE_DOCS_DATABASE_URL"
    )

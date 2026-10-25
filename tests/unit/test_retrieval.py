"""Retrieval tests: relevance, tenant isolation and fusion."""
from __future__ import annotations

import pytest
from django.contrib.postgres.search import SearchVector
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.ai_gateway.mock import MockProvider
from apps.knowledge.models import DocumentChunk
from apps.knowledge.retrievers import FullTextRetriever, HybridRetriever, VectorRetriever
from apps.knowledge.services import create_document, create_knowledge_base
from tests.conftest import HOTEL_HANDBOOK


@pytest.fixture
def populated(organization, knowledge_base, ready_document):
    return organization, knowledge_base, ready_document


@pytest.mark.django_db
class TestFullTextRetriever:
    def test_finds_relevant_chunk(self, populated):
        organization, knowledge_base, _ = populated
        results = FullTextRetriever().retrieve(
            organization_id=organization.id,
            knowledge_base_ids=[knowledge_base.id],
            query="check-in time",
        )
        assert results
        assert any("Check-in time is 3:00 PM" in chunk.content for chunk in results)
        assert results[0].document_title == "Guest Handbook"

    def test_requires_knowledge_base_ids(self, populated):
        organization, _, _ = populated
        assert FullTextRetriever().retrieve(
            organization_id=organization.id, knowledge_base_ids=[], query="check-in"
        ) == []

    def test_empty_query_returns_nothing(self, populated):
        organization, knowledge_base, _ = populated
        assert FullTextRetriever().retrieve(
            organization_id=organization.id, knowledge_base_ids=[knowledge_base.id], query="  "
        ) == []

    def test_other_organization_cannot_retrieve(self, populated, other_organization):
        _, knowledge_base, _ = populated
        results = FullTextRetriever().retrieve(
            organization_id=other_organization.id,
            knowledge_base_ids=[knowledge_base.id],
            query="check-in time",
        )
        assert results == []

    def test_unauthorised_knowledge_base_is_excluded(self, populated, organization):
        _, _, _ = populated
        other_kb = create_knowledge_base(organization=organization, name="Other KB")
        results = FullTextRetriever().retrieve(
            organization_id=organization.id,
            knowledge_base_ids=[other_kb.id],
            query="check-in time",
        )
        assert results == []

    def test_deleted_document_is_not_retrieved(self, populated, organization, knowledge_base):
        from django.utils import timezone

        _, _, document = populated
        document.deleted_at = timezone.now()
        document.save(update_fields=["deleted_at"])
        assert (
            FullTextRetriever().retrieve(
                organization_id=organization.id,
                knowledge_base_ids=[knowledge_base.id],
                query="check-in time",
            )
            == []
        )

    def test_result_shape_is_citable(self, populated):
        organization, knowledge_base, _ = populated
        chunk = FullTextRetriever().retrieve(
            organization_id=organization.id,
            knowledge_base_ids=[knowledge_base.id],
            query="room service",
        )[0]
        citation = chunk.as_citation()
        assert citation["document_title"]
        assert citation["chunk_index"] >= 0
        assert 0.0 <= citation["score"] <= 1.0


@pytest.mark.django_db
class TestVectorRetriever:
    def test_similarity_search(self, populated, settings):
        settings.EMBEDDING_PROVIDER = "mock"
        organization, knowledge_base, document = populated
        text = "check-in time is 3:00 PM"
        vector = MockProvider().create_embeddings(texts=[text]).vectors[0]
        DocumentChunk.objects.filter(document=document).update(embedding=vector)

        results = VectorRetriever().retrieve(
            organization_id=organization.id,
            knowledge_base_ids=[knowledge_base.id],
            query=text,
            top_k=3,
        )
        assert results
        assert results[0].score > 0.9

    def test_chunks_without_embedding_are_skipped(self, populated):
        organization, knowledge_base, _ = populated
        assert (
            VectorRetriever().retrieve(
                organization_id=organization.id,
                knowledge_base_ids=[knowledge_base.id],
                query="check-in",
            )
            == []
        )


@pytest.mark.django_db
class TestHybridRetriever:
    def test_fusion_returns_unique_chunks(self, populated, settings):
        settings.EMBEDDING_PROVIDER = "mock"
        organization, knowledge_base, document = populated
        text = "check-in time is 3:00 PM"
        vector = MockProvider().create_embeddings(texts=[text]).vectors[0]
        DocumentChunk.objects.filter(document=document).update(embedding=vector)

        results = HybridRetriever().retrieve(
            organization_id=organization.id,
            knowledge_base_ids=[knowledge_base.id],
            query=text,
            top_k=5,
        )
        ids = [chunk.chunk_id for chunk in results]
        assert len(ids) == len(set(ids))
        assert ids

    def test_semantic_branch_failure_degrades_to_lexical(self, populated, monkeypatch):
        organization, knowledge_base, _ = populated

        class BrokenRetriever:
            name = "broken"

            def retrieve(self, **kwargs):
                raise RuntimeError("vector store down")

        results = HybridRetriever(semantic=BrokenRetriever()).retrieve(
            organization_id=organization.id,
            knowledge_base_ids=[knowledge_base.id],
            query="check-in time",
        )
        assert results


@pytest.mark.django_db
def test_chunk_search_vector_is_populated(organization, knowledge_base, ready_document):
    assert DocumentChunk.objects.filter(document=ready_document).exclude(search_vector=None).exists()

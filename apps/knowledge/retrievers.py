"""Retrieval interface and implementations.

Tenant isolation is enforced inside every retriever: the queryset is always
filtered by ``organization_id`` **and** by the authorised knowledge base ids.
There is no code path that queries chunks without those two filters.

The interface is intentionally small so a different vector store can be added
later without touching the chat pipeline.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import QuerySet

from apps.knowledge.models import Document, DocumentChunk

logger = logging.getLogger("apps.knowledge.retrieval")

RRF_K = 60  # reciprocal rank fusion constant


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_title: str
    knowledge_base_id: str
    chunk_index: int
    content: str
    score: float
    page_number: int | None = None

    def as_citation(self) -> dict:
        return {
            "document_id": self.document_id,
            "document_title": self.document_title,
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "score": round(self.score, 4),
        }


class Retriever(Protocol):
    name: str

    def retrieve(
        self,
        *,
        organization_id,
        knowledge_base_ids: list,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[RetrievedChunk]: ...


def _base_queryset(organization_id, knowledge_base_ids: list) -> QuerySet[DocumentChunk]:
    if not knowledge_base_ids:
        return DocumentChunk.objects.none()
    return DocumentChunk.objects.filter(
        organization_id=organization_id,
        knowledge_base_id__in=knowledge_base_ids,
        document__status=Document.Status.READY,
        document__deleted_at__isnull=True,
    ).select_related("document")


def _to_chunk(row, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(row.id),
        document_id=str(row.document_id),
        document_title=row.document.title,
        knowledge_base_id=str(row.knowledge_base_id),
        chunk_index=row.index,
        content=row.content,
        score=score,
        page_number=row.page_number,
    )


class FullTextRetriever:
    """PostgreSQL full-text search with a substring fallback."""

    name = "fulltext"

    def retrieve(
        self,
        *,
        organization_id,
        knowledge_base_ids: list,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[RetrievedChunk]:
        cleaned = (query or "").strip()
        if not cleaned:
            return []

        queryset = _base_queryset(organization_id, knowledge_base_ids)
        config = settings.KNOWLEDGE_SEARCH_CONFIG
        search_vector = SearchVector("content", config=config)

        try:
            search_query = SearchQuery(cleaned, config=config, search_type="websearch")
            ranked = (
                queryset.annotate(rank=SearchRank(search_vector, search_query))
                .filter(rank__gt=min_score)
                .order_by("-rank")[:top_k]
            )
            results = [_to_chunk(row, float(row.rank)) for row in ranked]
            if results:
                return results
        except Exception:  # noqa: BLE001 - malformed query syntax
            logger.info("Full-text query failed, using fallback", exc_info=True)

        return self._fallback(queryset, cleaned, top_k)

    @staticmethod
    def _fallback(queryset: QuerySet[DocumentChunk], query: str, top_k: int) -> list[RetrievedChunk]:
        tokens = [token for token in query.split() if len(token) > 3]
        if not tokens:
            return []
        from django.db.models import Q

        condition = Q()
        for token in tokens[:5]:
            condition |= Q(content__icontains=token)
        rows = queryset.filter(condition)[:top_k]
        return [_to_chunk(row, 0.05) for row in rows]


class VectorRetriever:
    """pgvector cosine similarity search."""

    name = "vector"

    def retrieve(
        self,
        *,
        organization_id,
        knowledge_base_ids: list,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[RetrievedChunk]:
        cleaned = (query or "").strip()
        if not cleaned:
            return []

        from pgvector.django import CosineDistance

        from apps.ai_gateway.registry import get_embedding_provider

        embedding = get_embedding_provider().create_embeddings(texts=[cleaned]).vectors[0]
        queryset = _base_queryset(organization_id, knowledge_base_ids).filter(embedding__isnull=False)
        rows = queryset.annotate(distance=CosineDistance("embedding", embedding)).order_by("distance")[:top_k]

        results: list[RetrievedChunk] = []
        for row in rows:
            score = 1.0 - float(row.distance)
            if score >= min_score:
                results.append(_to_chunk(row, score))
        return results


class HybridRetriever:
    """Reciprocal rank fusion of lexical and semantic results."""

    name = "hybrid"

    def __init__(self, lexical: Retriever | None = None, semantic: Retriever | None = None):
        self.lexical = lexical or FullTextRetriever()
        self.semantic = semantic or VectorRetriever()

    def retrieve(
        self,
        *,
        organization_id,
        knowledge_base_ids: list,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[RetrievedChunk]:
        candidates: dict[str, tuple[RetrievedChunk, float]] = {}

        for retriever in (self.lexical, self.semantic):
            try:
                results = retriever.retrieve(
                    organization_id=organization_id,
                    knowledge_base_ids=knowledge_base_ids,
                    query=query,
                    top_k=top_k,
                )
            except Exception:  # noqa: BLE001 - one branch failing must not break retrieval
                logger.warning("Retriever %s failed", retriever.name, exc_info=True)
                continue
            for rank, item in enumerate(results, start=1):
                fused = 1.0 / (RRF_K + rank)
                if item.chunk_id in candidates:
                    previous, previous_score = candidates[item.chunk_id]
                    candidates[item.chunk_id] = (previous, previous_score + fused)
                else:
                    candidates[item.chunk_id] = (item, fused)

        ranked = sorted(candidates.values(), key=lambda pair: pair[1], reverse=True)[:top_k]
        return [
            RetrievedChunk(
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                document_title=item.document_title,
                knowledge_base_id=item.knowledge_base_id,
                chunk_index=item.chunk_index,
                content=item.content,
                score=score,
                page_number=item.page_number,
            )
            for item, score in ranked
        ]


def get_retriever() -> Retriever:
    if settings.VECTOR_SEARCH_ENABLED:
        return HybridRetriever()
    return FullTextRetriever()


def retrieve_for_assistant(
    assistant,
    query: str,
    *,
    top_k: int | None = None,
    retriever: Retriever | None = None,
) -> tuple[list[RetrievedChunk], int, str]:
    """Retrieve approved knowledge for an assistant.

    Returns ``(chunks, latency_ms, retriever_name)``.
    """
    knowledge_base_ids = list(assistant.knowledge_bases.values_list("id", flat=True))
    if not knowledge_base_ids:
        return [], 0, "none"

    resolved = retriever or get_retriever()
    started = time.monotonic()
    chunks = resolved.retrieve(
        organization_id=assistant.organization_id,
        knowledge_base_ids=knowledge_base_ids,
        query=query,
        top_k=top_k or settings.KNOWLEDGE_RETRIEVAL_TOP_K,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    return chunks, latency_ms, resolved.name

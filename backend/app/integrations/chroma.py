"""The only way the app touches Chroma (blueprint §6.2, ADR-002, ADR-004, ADR-010).

- One shared collection; every record carries tenant, audience and version metadata.
- Record IDs are deterministic, `{organization_id}:{version_id}:{chunk_index}`, so a retried ingestion
  overwrites instead of duplicating (G7) and two tenants can never share an ID.
- Queries require a `Principal` and the active version IDs resolved from Postgres (ADR-003). The filter is
  built here, never from request input, and every hit is re-checked before it can reach the LLM
  (§6.3 post-filter).
- Metadata is a convenience for filtering. Postgres stays the source of truth for permissions and versions.
"""

import math
import uuid
from collections.abc import Collection as Iterable
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.core.permissions import Principal
from app.db.models import Audience

COLLECTION_NAME = "knowledge_chunks"
# Recorded on the collection so a different embedding model can never write into (or query) this index (§7.3).
FINGERPRINT_MODEL = "knowledgeai_embedding_model"
FINGERPRINT_DIMENSION = "knowledgeai_embedding_dimension"


class IndexMismatch(Exception):
    """The collection was built with another embedding model or dimension: rebuild instead of mixing."""


def chroma_id(organization_id: uuid.UUID, version_id: uuid.UUID, chunk_index: int) -> str:
    if chunk_index < 0:
        raise ValueError("chunk_index must be >= 0")
    return f"{organization_id}:{version_id}:{chunk_index}"


class ChunkMetadata(BaseModel):
    """Scalar-only metadata (Chroma stores str/int/float/bool). Rich data and permissions live in Postgres."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    title: str = Field(min_length=1)
    source_type: Literal["pdf", "docx", "txt"]
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    section: str | None = None
    audience: Audience
    product_model: str | None = None
    language: str = "en"

    def to_chroma(self) -> dict[str, str | int]:
        # Chroma rejects None values, so optional fields are omitted when unset.
        return {
            k: (str(v) if isinstance(v, uuid.UUID) else v)
            for k, v in self.model_dump(mode="json").items()
            if v is not None
        }


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    chunk_index: int
    text: str
    embedding: Sequence[float]
    metadata: ChunkMetadata


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chroma_id: str
    text: str
    metadata: dict[str, Any]
    distance: float


class KnowledgeIndex:
    def __init__(
        self,
        client: ClientAPI,
        dimension: int,
        embedding_model: str,
        collection_name: str = COLLECTION_NAME,
    ) -> None:
        self._dimension = dimension
        self._collection: Collection = client.get_or_create_collection(
            name=collection_name,
            embedding_function=None,  # we always pass our own embeddings; never download a default model
            configuration={"hnsw": {"space": "cosine"}},
        )
        self._check_fingerprint(embedding_model, dimension)

    def _check_fingerprint(self, model: str, dimension: int) -> None:
        metadata = dict(self._collection.metadata or {})
        recorded = (metadata.get(FINGERPRINT_MODEL), metadata.get(FINGERPRINT_DIMENSION))
        if recorded == (model, dimension):
            return
        if recorded == (None, None) and self._collection.count() == 0:
            # New (or never written) collection: claim it for this model.
            metadata.update({FINGERPRINT_MODEL: model, FINGERPRINT_DIMENSION: dimension})
            self._collection.modify(metadata=metadata)
            return
        raise IndexMismatch(
            f"collection {self._collection.name!r} holds {recorded[0]!r}/{recorded[1]!r} embeddings, "
            f"settings say {model!r}/{dimension}; rebuild into a new CHROMA_COLLECTION instead"
        )

    @property
    def name(self) -> str:
        return self._collection.name

    # ---------- ingestion side (system context: tenant comes from the job, not a user) ----------

    def upsert_version(
        self, organization_id: uuid.UUID, version_id: uuid.UUID, chunks: Sequence[ChunkRecord]
    ) -> list[str]:
        if not chunks:
            return []
        for chunk in chunks:
            meta = chunk.metadata
            if (meta.organization_id, meta.document_version_id) != (organization_id, version_id):
                raise ValueError("chunk metadata does not match the version being indexed")
            if len(chunk.embedding) != self._dimension:
                raise ValueError(f"embedding has {len(chunk.embedding)} dims, expected {self._dimension}")
        ids = [chroma_id(organization_id, version_id, c.chunk_index) for c in chunks]
        vectors: list[Sequence[float] | Sequence[int]] = [list(c.embedding) for c in chunks]
        self._collection.upsert(
            ids=ids,
            embeddings=vectors,
            documents=[c.text for c in chunks],
            metadatas=[c.metadata.to_chroma() for c in chunks],
        )
        return ids

    def count_version(self, organization_id: uuid.UUID, version_id: uuid.UUID) -> int:
        result = self._collection.get(where=self._version_filter(organization_id, version_id), include=[])
        return len(result["ids"])

    def delete_version(self, organization_id: uuid.UUID, version_id: uuid.UUID) -> None:
        self._collection.delete(where=self._version_filter(organization_id, version_id))

    def prune_version(self, organization_id: uuid.UUID, version_id: uuid.UUID, keep: Iterable[str]) -> int:
        """Delete this version's records that are not in `keep` (left over from an earlier, longer run)."""
        existing = self._collection.get(where=self._version_filter(organization_id, version_id), include=[])
        stale = sorted(set(existing["ids"]) - set(keep))
        if stale:
            self._collection.delete(ids=stale)
        return len(stale)

    def indexed_version_ids(self, organization_id: uuid.UUID, page_size: int = 5000) -> set[uuid.UUID]:
        """Every version with records for this tenant: used by reconciliation to find orphans (ADR-002)."""
        found: set[uuid.UUID] = set()
        offset = 0
        while True:
            page = self._collection.get(
                where={"organization_id": str(organization_id)},
                include=["metadatas"],
                limit=page_size,
                offset=offset,
            )
            for metadata in page["metadatas"] or []:
                found.add(uuid.UUID(str(metadata["document_version_id"])))
            if len(page["ids"]) < page_size:
                return found
            offset += page_size

    # ---------- question side (always scoped by a Principal) ----------

    def version_chunks(self, principal: Principal, version_id: uuid.UUID) -> dict[str, str]:
        """Admin diagnostics (§10.3): the stored text of one version's records, by record ID. Managers and
        admins only, and only in their own tenant; the caller has already checked the version is theirs.
        """
        if not principal.can_manage_documents:
            raise PermissionError("chunk diagnostics are for managers and admins")
        result = self._collection.get(
            where=self._version_filter(principal.organization_id, version_id), include=["documents"]
        )
        return dict(zip(result["ids"], (result["documents"] or []), strict=True))

    def fetch(
        self,
        principal: Principal,
        ids: Sequence[str],
        embedding: Sequence[float],
        active_version_ids: Iterable[uuid.UUID],
    ) -> list[RetrievedChunk]:
        """Records found by keyword search (ADR-028), under the same filter and re-check as `query`, with
        their cosine distance to the question so evidence grading treats them like any other candidate."""
        version_ids = sorted({str(v) for v in active_version_ids})
        if not ids or not version_ids:
            return []
        audiences = [a.value for a in principal.allowed_audiences]
        result = self._collection.get(
            ids=list(ids),
            where=self._principal_filter(principal, version_ids),
            include=["documents", "metadatas", "embeddings"],
        )
        found: dict[str, RetrievedChunk] = {}
        for record_id, text, metadata, vector in zip(
            result["ids"],
            result["documents"] or [],
            result["metadatas"] or [],
            result["embeddings"] if result["embeddings"] is not None else [],
            strict=True,
        ):
            if (
                metadata.get("organization_id") != str(principal.organization_id)
                or metadata.get("audience") not in audiences
                or metadata.get("document_version_id") not in version_ids
            ):
                continue
            found[record_id] = RetrievedChunk(
                record_id, text or "", dict(metadata), _cosine_distance(embedding, [float(x) for x in vector])
            )
        return [found[i] for i in ids if i in found]  # keep the keyword ranking

    def query(
        self,
        principal: Principal,
        embedding: Sequence[float],
        active_version_ids: Iterable[uuid.UUID],
        n_results: int = 5,
    ) -> list[RetrievedChunk]:
        """Top matches the principal may see, from active versions only. Empty input → empty result."""
        version_ids = sorted({str(v) for v in active_version_ids})
        if not version_ids:
            return []  # nothing published for this principal; never query unfiltered
        audiences = [a.value for a in principal.allowed_audiences]
        where = self._principal_filter(principal, version_ids)
        query_vector: list[Sequence[float] | Sequence[int]] = [list(embedding)]
        result = self._collection.query(
            query_embeddings=query_vector,
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits = zip(
            result["ids"][0],
            (result["documents"] or [[]])[0],
            (result["metadatas"] or [[]])[0],
            (result["distances"] or [[]])[0],
            strict=True,
        )
        allowed_versions = set(version_ids)
        retrieved: list[RetrievedChunk] = []
        for record_id, text, metadata, distance in hits:
            # Defense in depth: re-check every hit even though the filter already applied (§6.3).
            if (
                metadata.get("organization_id") != str(principal.organization_id)
                or metadata.get("audience") not in audiences
                or metadata.get("document_version_id") not in allowed_versions
            ):
                continue
            retrieved.append(RetrievedChunk(record_id, text or "", dict(metadata), float(distance)))
        return retrieved

    @staticmethod
    def _principal_filter(principal: Principal, version_ids: list[str]) -> dict[str, Any]:
        """From the principal and Postgres's active versions only, never from request input (ADR-004)."""
        return {
            "$and": [
                {"organization_id": str(principal.organization_id)},
                {"audience": {"$in": [a.value for a in principal.allowed_audiences]}},
                {"document_version_id": {"$in": version_ids}},
            ]
        }

    @staticmethod
    def _version_filter(organization_id: uuid.UUID, version_id: uuid.UUID) -> dict[str, Any]:
        return {"$and": [{"organization_id": str(organization_id)}, {"document_version_id": str(version_id)}]}


def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Chroma's cosine space distance, 1 - cos(a, b): the same scale `query` returns."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norms = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 1.0 - dot / norms if norms else 1.0


@lru_cache
def get_knowledge_index() -> KnowledgeIndex:
    from app.ingestion.embeddings import get_embedder  # the model actually in use, versioned (ADR-013)

    settings = get_settings()
    client = chromadb.HttpClient(
        host=settings.chroma_host, port=settings.chroma_port, ssl=settings.chroma_ssl
    )
    return KnowledgeIndex(
        client,
        dimension=settings.embedding_dimension,
        embedding_model=get_embedder().model,
        collection_name=settings.chroma_collection,
    )

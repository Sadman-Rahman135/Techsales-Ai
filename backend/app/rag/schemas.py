"""Shapes that cross a boundary: retrieved sources, the LLM's structured output (§8.4), and the API answer
(§9.1).
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import AnswerStatus


class Source(BaseModel):
    """One authorized, retrieved chunk as the graph sees it. Never persisted: only its IDs are (§13)."""

    model_config = ConfigDict(frozen=True)

    source_id: str  # "S1", "S2", … in rank order: the only IDs the LLM ever sees
    chroma_id: str
    document_id: uuid.UUID
    version_id: uuid.UUID
    title: str
    page_start: int
    page_end: int
    section: str | None
    audience: str
    text: str
    similarity: float
    product_model: str | None = None
    rerank_score: float | None = None  # set when the optional reranker ran (ADR-028)


class CitedSource(BaseModel):
    source_id: str = Field(description="ID of a supplied source, e.g. S1")
    quote: str = Field(description="Short verbatim quote from that source that supports the answer")


class GeneratedAnswer(BaseModel):
    """What the model must return (strict JSON schema), parsed with Pydantic. A parse failure: abstain."""

    answer: str = Field(description="Concise answer, with [S1]-style markers after supported sentences")
    answer_status: Literal["answered", "insufficient_evidence"] = Field(
        description='"answered" when the sources answer the question; '
        '"insufficient_evidence" only when they do not'
    )
    citations: list[CitedSource]
    suggested_followups: list[str] = Field(description="Up to 3 short follow-up questions; may be empty")


class Citation(BaseModel):
    """A validated citation as returned to clients (§9.1). `preview_url` opens the cited page (authorized)."""

    position: int
    document_id: uuid.UUID
    version_id: uuid.UUID
    chunk_id: str
    title: str
    page_start: int
    page_end: int
    section: str | None
    quote: str
    audience: str = Field(description="`public` or `employee`: staff screens mark employee-only sources")
    preview_url: str | None = None


class OrderLineRecord(BaseModel):
    name: str
    sku: str
    quantity: int
    unit_price_cents: int


class OrderRecord(BaseModel):
    """One order as the customer may see it (ADR-029, ADR-031)."""

    kind: Literal["order"] = "order"
    order_number: str
    status: str
    delivery_method: str
    placed_at: datetime
    shipped_at: datetime | None
    delivered_at: datetime | None
    expected_on: date | None
    carrier: str | None
    tracking_number: str | None
    total_cents: int
    pickup_store: str | None = None
    pickup_by: date | None = None
    lines: list[OrderLineRecord]


class RepairRecord(BaseModel):
    """One repair job as the customer may see it (ADR-031)."""

    kind: Literal["repair"] = "repair"
    reference: str
    product_name: str
    status: str
    reported_fault: str
    diagnosis: str | None
    resolution: str | None
    store: str
    under_warranty: bool
    cost_cents: int | None
    received_at: datetime
    ready_on: date | None
    collected_at: datetime | None
    order_number: str | None


AccountRecord = Annotated[OrderRecord | RepairRecord, Field(discriminator="kind")]


class AskResult(BaseModel):
    request_id: str
    trace_id: str
    conversation_id: uuid.UUID
    answer_id: uuid.UUID
    answer_status: AnswerStatus
    grounded: bool
    answer: str
    citations: list[Citation]
    suggested_followups: list[str]
    # What the client should offer next: "verify_order" shows the verification card (US-05, ADR-029);
    # "show_records" means the answer came from the caller's own orders or repairs, listed in `records`
    # (ADR-031).
    next_action: Literal["verify_order", "show_records"] | None = None
    # The orders and repairs the answer is about: fetched by fixed queries for this caller only, never by the
    # model.
    records: list[AccountRecord] = Field(default_factory=list)


Stage = Literal["searching", "rewriting", "checking"]

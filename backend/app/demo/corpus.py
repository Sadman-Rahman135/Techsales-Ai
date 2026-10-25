"""The fictional TechCare demo corpus (docs/evaluation.md: all content fictional) and a loader that runs it
through the real ingestion path: validate → upload → worker → publish.

Employee-only documents carry the canary string `CANARY-EMP-7Q2`, so any leak to a public caller can be
detected mechanically in an answer (docs/evaluation.md §1, gate G5).
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import select

from app.core.permissions import Principal
from app.db.models import FINISHED_JOB_STATES, Audience, Document, IngestionJob, JobState
from app.db.session import session_factory, tenant_session
from app.demo.builders import docx_bytes, pdf_bytes, warranty_pdf
from app.ingestion.validation import validate_upload
from app.ingestion.worker import WorkerContext, run_once
from app.services import documents as docs
from app.services.versions import publish_version

EMPLOYEE_CANARY = "CANARY-EMP-7Q2"


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    filename: str
    title: str
    audience: Audience
    build: Callable[[], bytes]
    product_model: str | None = None
    category: str | None = None
    revisions: tuple[
        Callable[[], bytes], ...
    ] = ()  # later versions, each published in turn (lifecycle cases)


def _returns_policy() -> bytes:
    return docx_bytes(
        [
            ("title", "TechCare Returns Policy"),
            ("p", "You can return most items within 30 days of delivery with the original receipt."),
            ("h1", "Laptops and tablets"),
            ("p", "Unopened laptops can be returned within 30 days for a full refund."),
            (
                "p",
                "Opened laptops can be returned within 14 days and have a 15% restocking fee unless faulty.",
            ),
            (
                "table",
                "Condition|Refund|Window\nUnopened|100%|30 days\nOpened|85%|14 days\nFaulty|100%|30 days",
            ),
            ("pagebreak", ""),
            ("h1", "Phones"),
            ("p", "To return a phone, bring the SIM tray, the charger and the original box to any store."),
            ("p", "Activated phones are refunded at 90% of the purchase price."),
        ]
    )


def _store_hours() -> bytes:
    return (
        b"# Store hours\n"
        b"TechCare stores are open Monday to Saturday from 9am to 9pm, and on Sunday from 11am to 6pm.\n\n"
        b"## Holidays\nStores are closed on New Year's Day, Thanksgiving and Christmas Day.\f"
        b"# Contact support\n"
        b"Call TechCare support on 1-800-555-0199 or email support@techcare.example. "
        b"Support answers phone calls every day from 8am to 10pm.\n"
    )


FADED_PRINTS = (
    "Faded prints usually mean low toner or a low print density setting. Check the toner level on the "
    "printer display, then raise Print Density in Settings > Print Quality. If pages are still faded, "
    "gently shake the toner cartridge and reinstall it."
)
PAPER_JAMS = (
    "Turn the printer off before you clear a paper jam. Open the rear access door and pull the jammed paper "
    "out slowly with both hands. Remove any torn pieces before you close the door."
)
WIFI_SETUP = (
    "To connect the printer to Wi-Fi, press and hold the Wi-Fi button for 3 seconds until the light blinks "
    "blue. Then select DemoPrinter-X2 in the TechCare Print app and enter your network password. The "
    "printer supports 2.4 GHz networks only."
)
DIAGNOSTIC_FEE = (
    "The diagnostic fee is 49 dollars. Waive the diagnostic fee when the customer approves the repair or "
    "when the device is under warranty."
)
LOANER_LAPTOPS = (
    "Offer a loaner laptop when a warranty repair will take longer than 5 business days. Every loaner laptop "
    "needs a signed loan agreement."
)


def _printer_guide() -> bytes:
    return pdf_bytes(
        [
            [
                ("h1", "DemoPrinter-X2 Troubleshooting Guide"),
                ("h2", "Faded or light prints"),
                ("p", FADED_PRINTS),
            ],
            [
                ("h2", "Paper jams"),
                ("p", PAPER_JAMS),
                ("h2", "Error codes"),
                (
                    "table",
                    "Code|Meaning|What to do\nE-02|Paper tray empty|Load paper\n"
                    "E-04|Toner cartridge not detected|Reinstall the cartridge until it clicks\n"
                    "E-07|Fuser overheated|Turn the printer off for 30 minutes",
                ),
            ],
            [
                ("h2", "Wi-Fi setup"),
                ("p", WIFI_SETUP),
            ],
        ],
        header="DemoPrinter-X2 Troubleshooting Guide",
        footer_page_numbers=True,
    )


def _repair_desk() -> bytes:
    return docx_bytes(
        [
            ("title", "Repair Desk Procedures"),
            ("p", f"Internal reference {EMPLOYEE_CANARY}. Staff use only; never share with customers."),
            ("h1", "Diagnostic fee"),
            ("p", DIAGNOSTIC_FEE),
            ("h1", "Loaner laptops"),
            ("p", LOANER_LAPTOPS),
        ]
    )


DOCUMENTS: tuple[CorpusDocument, ...] = (
    CorpusDocument(
        "TechCare-Laptop-Warranty.pdf",
        "TechCare Laptop Warranty",
        Audience.PUBLIC,
        warranty_pdf,
        category="Warranty",
    ),
    CorpusDocument(
        "TechCare-Returns-Policy.docx",
        "TechCare Returns Policy",
        Audience.PUBLIC,
        _returns_policy,
        category="Returns",
    ),
    CorpusDocument(
        "TechCare-Store-Hours.txt",
        "TechCare Store Hours and Contact",
        Audience.PUBLIC,
        _store_hours,
        category="Stores",
    ),
    CorpusDocument(
        "DemoPrinter-X2-Troubleshooting.pdf",
        "DemoPrinter-X2 Troubleshooting Guide",
        Audience.PUBLIC,
        _printer_guide,
        product_model="DemoPrinter-X2",
        category="Troubleshooting",
    ),
    CorpusDocument(
        "Repair-Desk-Procedures.docx",
        "Repair Desk Procedures",
        Audience.EMPLOYEE,
        _repair_desk,
        category="Internal",
    ),
)


def load_corpus(
    ctx: WorkerContext,
    manager: Principal,
    timeout_seconds: float = 60.0,
    documents: Sequence[CorpusDocument] = DOCUMENTS,
) -> dict[str, Document]:
    """Upload, process and publish every corpus document that isn't published yet, then each of its revisions
    in order (publishing one archives the previous). Returns title → document.

    Processes jobs itself, so no separate worker is needed; a running worker may simply finish one first.
    """
    loaded: dict[str, Document] = {}
    for item in documents:
        with tenant_session(manager.organization_id, ctx.engine) as session:
            existing = session.scalar(
                select(Document).where(
                    Document.title == item.title,
                    Document.deleted_at.is_(None),
                    Document.active_version_id.is_not(None),
                )
            )
        if existing is not None:
            loaded[item.title] = existing
            continue
        validated = validate_upload(item.filename, None, item.build(), ctx.settings)
        with tenant_session(manager.organization_id, ctx.engine) as session:
            result = docs.create_upload(
                session,
                ctx.storage,
                ctx.settings,
                manager,
                validated,
                title=item.title,
                audience=item.audience,
                category=item.category,
                product_model=item.product_model,
                allow_duplicate=True,
            )
        _wait_for(ctx, result.job.id, timeout_seconds)
        with tenant_session(manager.organization_id, ctx.engine) as session:
            publish_version(session, ctx.index, manager, result.document.id, result.version.id)
        for number, build in enumerate(item.revisions, start=2):
            revision = validate_upload(item.filename, None, build(), ctx.settings)
            with tenant_session(manager.organization_id, ctx.engine) as session:
                upload = docs.create_upload(
                    session, ctx.storage, ctx.settings, manager, revision, document_id=result.document.id
                )
            _wait_for(ctx, upload.job.id, timeout_seconds)
            with tenant_session(manager.organization_id, ctx.engine) as session:
                publish_version(session, ctx.index, manager, result.document.id, upload.version.id)
            if upload.version.version_number != number:
                raise RuntimeError(f"{item.title}: expected version {number}")
        with tenant_session(manager.organization_id, ctx.engine) as session:
            loaded[item.title] = session.get_one(Document, result.document.id)
    return loaded


def _wait_for(ctx: WorkerContext, job_id: object, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with session_factory(ctx.engine)() as session:
            state = session.scalar(select(IngestionJob.state).where(IngestionJob.id == job_id))
        if state in [s.value for s in FINISHED_JOB_STATES]:
            if state != JobState.DONE.value:
                raise RuntimeError(f"demo document failed to ingest (job {job_id} is {state})")
            return
        if run_once(ctx) is None:
            time.sleep(0.2)
    raise TimeoutError(f"job {job_id} did not finish in {timeout_seconds}s")

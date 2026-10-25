"""Write the fictional TechCare demo corpus to data/demo-docs/ (git-ignored) for manual testing.

    backend/.venv/Scripts/python scripts/make_demo_docs.py

The files are the ones the M3 gate uses (backend/app/demo/corpus.py), plus a v2 of the warranty for version tests
and a scanned PDF (no text layer) for the failure path. To upload and publish them in one go instead, run
`python -m app.evaluation.runner --dataset ../evaluation/datasets/m3-handpicked.yaml --load-corpus` from backend/.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.demo.builders import pdf_bytes, scanned_pdf_bytes  # noqa: E402
from app.demo.corpus import DOCUMENTS  # noqa: E402

OUT = ROOT / "data" / "demo-docs"

FILES = {doc.filename: doc.build() for doc in DOCUMENTS}
FILES["TechCare-Laptop-Warranty-v2.pdf"] = pdf_bytes(
    [
        [
            ("h1", "TechCare Laptop Warranty"),
            ("h2", "Coverage period"),
            ("p", "Laptops are covered for 24 months from the date of purchase. Batteries for 12 months."),
        ]
    ]
)
FILES["Scanned-Service-Form.pdf"] = scanned_pdf_bytes()

# The M4 usability session's policy and its update (docs/demos/m4-usability-test.md, tasks 2 and 5).
_WRAPPING = "TechCare gift wrapping is free for any purchase over 25 dollars. Smaller purchases cost 3 dollars to wrap."
_ORDER = "Ask at the checkout or tick Gift wrap when you order online. Wrapped items ship in a plain outer box."
_FICTIONAL = "This is a fictional document for the TechCare demo."
FILES["Gift-Wrapping-Policy.pdf"] = pdf_bytes(
    [[("h1", "Gift Wrapping Policy"), ("p", _WRAPPING), ("p", _ORDER), ("p", _FICTIONAL)]]
)
FILES["Gift-Wrapping-Policy-recycled.pdf"] = pdf_bytes(
    [
        [
            ("h1", "Gift Wrapping Policy"),
            ("p", _WRAPPING),
            ("p", "All gift wrapping now uses 100% recycled paper and paper tape, with no plastic ribbon."),
            ("p", _ORDER),
            ("p", _FICTIONAL),
        ]
    ]
)

OUT.mkdir(parents=True, exist_ok=True)
for name, data in FILES.items():
    (OUT / name).write_bytes(data)
    print(f"wrote {(OUT / name).relative_to(ROOT)} ({len(data)} bytes)")

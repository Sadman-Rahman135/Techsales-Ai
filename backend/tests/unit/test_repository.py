"""§14 repository structure: the documented layout and the docs' test references stay true."""

import re
from pathlib import Path

from app.config import REPO_ROOT

DOCS = REPO_ROOT / "docs"


def test_every_path_in_the_repository_map_exists() -> None:
    text = (DOCS / "repository.md").read_text(encoding="utf-8")
    layout = text.split("## 2.")[0]
    paths = re.findall(r"^\| `([^`]+)` \|", layout, re.MULTILINE)
    assert len(paths) > 40
    missing = [p for p in paths if not (REPO_ROOT / p).exists()]
    assert missing == []


def test_tests_cited_as_evidence_exist() -> None:
    """`test_file::test_name` in the docs (threat model, API contract, evidence pages) names a real test."""
    names: dict[str, set[str]] = {}
    for path in (REPO_ROOT / "backend" / "tests").rglob("test_*.py"):
        names.setdefault(path.stem, set()).update(
            re.findall(
                r"^\s*(?:def|class) (test_\w+|Test\w+)", path.read_text(encoding="utf-8"), re.MULTILINE
            )
        )
    cited: list[tuple[Path, str, str]] = []
    for doc in [
        DOCS / "threat-model.md",
        DOCS / "api-contract.md",
        DOCS / "risk-register.md",
        DOCS / "deployment.md",
        DOCS / "definition-of-done.md",
        *sorted((DOCS / "demos").glob("1[1-6]-*.md")),
    ]:
        for module, test in re.findall(r"(test_\w+)::(?:…)?(\w+)", doc.read_text(encoding="utf-8")):
            cited.append((doc, module, test))
    assert len(cited) > 30
    # A citation may abbreviate a long name ("…test_slow_statements_are_cancelled…").
    wrong = [
        f"{doc.name}: {module}::{test}"
        for doc, module, test in cited
        if not any(test in name for name in names.get(module, set()))
    ]
    assert wrong == []

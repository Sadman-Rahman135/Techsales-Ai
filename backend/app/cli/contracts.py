"""Write or check the committed API contracts (blueprint §9.2 "version this contract", §18 "API OpenAPI
documentation"):

- `docs/contracts/knowledge-answer.v1.schema.json`: the frozen voice contract for Project 2.
- `docs/contracts/openapi.json`: the whole HTTP API as OpenAPI, so a route or schema change shows up as a
  reviewed diff (docs/api-contract.md explains it, with sample requests).

    python -m app.cli.contracts           # check: exits 1 if the code drifted from either committed file
    python -m app.cli.contracts --write   # regenerate (only for an intended, reviewed change)

A compatible change (a new optional field) may be written into v1. Anything else is a new version: add a v2
route and a new schema file, and keep v1 working until Project 2 has moved.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from app.api.internal import CONTRACT_VERSION, KnowledgeAnswer, KnowledgeQuestion
from app.config import REPO_ROOT

SCHEMA_PATH = REPO_ROOT / "docs" / "contracts" / f"{CONTRACT_VERSION}.schema.json"
OPENAPI_PATH = REPO_ROOT / "docs" / "contracts" / "openapi.json"


def current_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": CONTRACT_VERSION,
        "endpoint": "POST /api/v1/internal/knowledge/answer",
        "request": KnowledgeQuestion.model_json_schema(mode="validation"),
        "response": KnowledgeAnswer.model_json_schema(mode="serialization"),
    }


def current_openapi() -> dict[str, Any]:
    from app.main import app  # imported late: the voice check above doesn't need the whole app

    return app.openapi()


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def contracts() -> list[tuple[Path, str]]:
    return [(SCHEMA_PATH, render(current_schema())), (OPENAPI_PATH, render(current_openapi()))]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, text in contracts():
        name = path.relative_to(REPO_ROOT).as_posix()
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {name}")
        elif not path.exists() or path.read_text(encoding="utf-8") != text:
            stale.append(name)
        else:
            print(f"{name} matches the code")
    if stale:
        raise SystemExit(f"out of date with the code: {', '.join(stale)} (review the change, then --write)")


if __name__ == "__main__":
    main()

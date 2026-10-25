"""Turn docs/IMPLEMENTATION_PLAN.md into GitHub labels, milestones and issues.

Each plan subsection that contains checkboxes becomes one issue whose body is a GitHub task
list, so progress shows on the issue. Priority labels are applied only where the plan states a
priority in the heading (e.g. "P0 - Minimum viable product"); inline "(P1)" tags stay in the
task text. "Milestone M<n>" subsections are attached to matching GitHub milestones.

Usage (requires the GitHub CLI, authenticated with `gh auth login`):
    python scripts/github_setup.py                      # dry run: print what would be created
    python scripts/github_setup.py --create             # create in the current repo
    python scripts/github_setup.py --create --repo you/knowledgeai

Safe to re-run: labels are upserted, and milestones/issues whose title already exists are skipped.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PLAN = Path(__file__).resolve().parent.parent / "docs" / "IMPLEMENTATION_PLAN.md"
PLAN_LINK = "docs/IMPLEMENTATION_PLAN.md"

LABELS = {
    "P0": ("b60205", "Required MVP work"),
    "P1": ("d93f0b", "Production-oriented improvement"),
    "P2": ("fbca04", "Optional stretch work"),
    "plan": ("0e8a16", "Tracked item from the implementation plan"),
    "release-gate": ("5319e7", "Must pass before calling the project finished"),
}

# Section 0 is meta-instructions about using the plan, not implementation work.
SKIP_SECTIONS = {"0"}
EVIDENCE_FOOTER = (
    "\n---\nTick a box only when it has automated tests, a manual test and a screenshot/demo "
    "(plan §0). A UI existing is not completion."
)

H2 = re.compile(r"^## (\d+)\. (.+)$")
H3 = re.compile(r"^### (.+)$")
TASK = re.compile(r"^\s*- \[[ xX]\] .+")
HEADING_PRIORITY = re.compile(r"\bP([012])\b")
MILESTONE = re.compile(r"^Milestone (M\d+) - (.+)$")


@dataclass
class Issue:
    title: str
    section: str
    tasks: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=lambda: ["plan"])
    milestone: str | None = None

    @property
    def body(self) -> str:
        return f"Source: `{PLAN_LINK}` — {self.section}\n\n" + "\n".join(self.tasks) + EVIDENCE_FOOTER


def parse_plan(text: str) -> list[Issue]:
    issues: list[Issue] = []
    current: Issue | None = None
    section_no = ""

    def flush() -> None:
        if current and current.tasks and section_no not in SKIP_SECTIONS:
            issues.append(current)

    for line in text.splitlines():
        if m := H2.match(line):
            flush()
            section_no, name = m.groups()
            current = Issue(title=f"[§{section_no}] {name}", section=f"§{section_no} {name}")
            if section_no == "18":
                current.labels.append("release-gate")
        elif (m := H3.match(line)) and current is not None:
            flush()
            name = m.group(1)
            current = Issue(title=f"[§{section_no}] {name}", section=f"§{section_no} › {name}")
            if p := HEADING_PRIORITY.search(name):
                current.labels.append(f"P{p.group(1)}")
            if ms := MILESTONE.match(name):
                current.milestone = f"{ms.group(1)} - {ms.group(2)}"
            if section_no == "18":
                current.labels.append("release-gate")
        elif current is not None and TASK.match(line):
            current.tasks.append(line.strip())
    flush()
    return issues


def gh(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["gh", *args], input=stdin, capture_output=True, text=True, encoding="utf-8", check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()}")
    return result.stdout


def create(issues: list[Issue], repo: list[str]) -> None:
    for name, (color, desc) in LABELS.items():
        gh("label", "create", name, "--color", color, "--description", desc, "--force", *repo)
    print(f"Labels upserted: {', '.join(LABELS)}")

    repo_path = repo[1] if repo else "{owner}/{repo}"
    existing_ms = {m["title"] for m in json.loads(gh("api", f"repos/{repo_path}/milestones?state=all"))}
    for title in sorted({i.milestone for i in issues if i.milestone} - existing_ms):
        gh("api", f"repos/{repo_path}/milestones", "-f", f"title={title}")
        print(f"Milestone created: {title}")

    listing = gh("issue", "list", "--state", "all", "--limit", "1000", "--json", "title", *repo)
    existing = {i["title"] for i in json.loads(listing)}
    for issue in issues:
        if issue.title in existing:
            print(f"skip (exists): {issue.title}")
            continue
        args = ["issue", "create", "--title", issue.title, "--body-file", "-", *repo]
        for label in issue.labels:
            args += ["--label", label]
        if issue.milestone:
            args += ["--milestone", issue.milestone]
        print(gh(*args, stdin=issue.body).strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--create", action="store_true", help="actually create labels, milestones and issues")
    parser.add_argument("--repo", help="OWNER/NAME; defaults to the repo of the current directory")
    args = parser.parse_args()

    issues = parse_plan(PLAN.read_text(encoding="utf-8"))
    if not args.create:
        for i in issues:
            extra = f"  milestone={i.milestone}" if i.milestone else ""
            print(f"{len(i.tasks):3} tasks  {i.title}  labels={','.join(i.labels)}{extra}")
        print(f"\n{len(issues)} issues, {sum(len(i.tasks) for i in issues)} tasks. Re-run with --create to apply.")
        return 0

    create(issues, ["--repo", args.repo] if args.repo else [])
    return 0


if __name__ == "__main__":
    sys.exit(main())

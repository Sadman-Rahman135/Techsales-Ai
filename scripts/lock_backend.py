"""Regenerate backend/constraints.txt, the full dependency lock (docs/dependency-versions.md). Never
hand-edit it.

The lock is the union of two resolved sets:
  1. the local dev venv (backend/.venv: runtime + dev tools), as before, and
  2. the backend Docker image (Linux, runtime only), so packages that only Linux installs (uvloop via
     uvicorn[standard]) are pinned too. They get a `sys_platform != "win32"` marker.
A package resolved to different versions on the two platforms is an error: fix the pins, don't pick one.

    docker compose --profile app build migrate      # or: docker build -t knowledgeai-backend:local backend
    backend/.venv/Scripts/python scripts/lock_backend.py            # writes the lock
    backend/.venv/Scripts/python scripts/lock_backend.py --check    # exits 1 if the lock is out of date

Then re-run the backend tests (CLAUDE.md "Dependencies").
"""

import argparse
import platform
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "backend" / "constraints.txt"
PROJECT = "knowledgeai-backend"
LINUX_ONLY = '; sys_platform != "win32"'


def parse(freeze: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in freeze.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-e")) or " @ " in line or "==" not in line:
            continue
        name, version = line.split(";")[0].split("==")
        name = name.strip().lower().replace("_", "-")
        if name != PROJECT:
            pins[name] = version.strip()
    return pins


def freeze_local(python: Path) -> dict[str, str]:
    return parse(
        subprocess.run(
            [str(python), "-m", "pip", "freeze"], check=True, capture_output=True, text=True
        ).stdout
    )


def freeze_image(image: str) -> dict[str, str]:
    command = ["docker", "run", "--rm", "--entrypoint", "pip", image, "freeze"]
    return parse(subprocess.run(command, check=True, capture_output=True, text=True).stdout)


def render(local: dict[str, str], linux: dict[str, str]) -> str:
    conflicts = {n: (local[n], v) for n, v in linux.items() if n in local and local[n] != v}
    if conflicts:
        raise SystemExit(f"resolved differently on {platform.system()} and Linux: {conflicts}")
    lines = [
        "# Full resolved dependency set for the KnowledgeAI backend (direct pins: backend/pyproject.toml).",
        f"# Regenerated {date.today().isoformat()} by scripts/lock_backend.py: CPython "
        f"{platform.python_version()} ({platform.system()}) dev venv + the Linux backend image.",
        '# Install: pip install -c constraints.txt -e ".[dev]"   (run from backend/)',
        "# Do not hand-edit. Regenerate with scripts/lock_backend.py, then re-run the tests.",
    ]
    names = sorted(set(local) | set(linux))
    lines += [f"{n}=={local[n]}" if n in local else f"{n}=={linux[n]}{LINUX_ONLY}" for n in names]
    return "\n".join(lines) + "\n"


def body(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.startswith("#")]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--image", default="knowledgeai-backend:local")
    parser.add_argument("--python", type=Path, default=Path(sys.executable), help="the dev venv's python")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--from-local",
        action="store_true",
        help="step 1 after adding a dependency: lock from the dev venv, keeping the current Linux-only pins; "
        "then rebuild the image (it installs from this lock) and run again without the flag",
    )
    args = parser.parse_args()
    if args.from_local:
        linux_only = {
            name: version
            for line in body(LOCK.read_text(encoding="utf-8"))
            if LINUX_ONLY.strip("; ") in line
            for name, version in [line.split(";")[0].split("==")]
        }
        text = render(freeze_local(args.python), linux_only)
    else:
        text = render(freeze_local(args.python), freeze_image(args.image))
    if args.check:
        if body(LOCK.read_text(encoding="utf-8")) != body(text):
            raise SystemExit("backend/constraints.txt is out of date: run scripts/lock_backend.py")
        print("backend/constraints.txt matches both platforms")
        return
    LOCK.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {LOCK.relative_to(ROOT)} ({len(body(text))} pins)")


if __name__ == "__main__":
    main()

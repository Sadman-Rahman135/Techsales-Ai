"""Download the local models listed in LLM/models.json into LLM/models/ (ADR-026). No account or key is needed:
both are public, Apache-2.0 models on Hugging Face.

    backend/.venv/Scripts/python scripts/download_models.py          # download what's missing, verify everything
    backend/.venv/Scripts/python scripts/download_models.py --check  # verify only (exit 1 if missing or corrupt)

Each file is fetched from its pinned revision (never "main"), resumed if a previous download stopped, and
accepted only when its SHA-256 matches the manifest. The files are large and git-ignored.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "LLM" / "models.json"
MODELS_DIR = ROOT / "LLM" / "models"
CHUNK = 8 * 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def download(model: dict[str, str | int], client: httpx.Client) -> None:
    target = MODELS_DIR / str(model["file"])
    partial = target.with_name(target.name + ".partial")
    url = f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{model['file']}"
    have = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with client.stream("GET", url, headers=headers, follow_redirects=True) as response:
        if response.status_code == 416:  # already complete
            pass
        elif response.status_code not in (200, 206):
            raise SystemExit(f"{model['file']}: HTTP {response.status_code}")
        else:
            mode = "ab" if response.status_code == 206 else "wb"
            done = have if mode == "ab" else 0
            total = int(model["size"])
            with partial.open(mode) as handle:
                for block in response.iter_bytes(CHUNK):
                    handle.write(block)
                    done += len(block)
                    print(f"\r  {model['file']}: {done / 1e9:.2f} / {total / 1e9:.2f} GB", end="", flush=True)
            print()
    partial.replace(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify only, download nothing")
    args = parser.parse_args()
    models = json.loads(MANIFEST.read_text(encoding="utf-8"))["models"]
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    failed = False
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        for model in models:
            target = MODELS_DIR / model["file"]
            if not target.exists():
                if args.check:
                    print(f"MISSING {model['file']}")
                    failed = True
                    continue
                print(f"downloading {model['name']} from {model['repo']}@{model['revision'][:12]}")
                download(model, client)
            actual = sha256_of(target)
            if actual != model["sha256"]:
                print(f"CORRUPT {model['file']}: sha256 {actual[:16]}… expected {model['sha256'][:16]}…")
                target.rename(target.with_name(target.name + ".bad"))  # never serve a file that doesn't match
                failed = True
            else:
                print(f"OK {model['file']} ({target.stat().st_size / 1e9:.2f} GB, sha256 verified)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

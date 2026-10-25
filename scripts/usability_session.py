"""A clean, separate copy of the app for the M4 usability session (docs/demos/m4-usability-test.md).

    backend/.venv/Scripts/python scripts/usability_session.py up [--gpu]   # before the session
    backend/.venv/Scripts/python scripts/usability_session.py down         # after it: removes the session's data

`up` stops the everyday stack (its volumes are kept), starts the release images as the compose project
`knowledgeai-usability` with empty volumes, seeds the users and writes the demo files the tasks use. The
participant sees no leftovers from development. `down` deletes the session project and its volumes and starts the
everyday stack again. Both stacks use the same ports, so only one runs at a time.
"""

import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "knowledgeai-usability"
OUT = ROOT / "data" / "usability"
HEALTH = "http://127.0.0.1:18000/api/v1/health/live"
PYTHON = sys.executable


def compose(*args: str, project: str | None = None, gpu: bool = False, capture: bool = False) -> str:
    command = ["docker", "compose"]
    if project:
        command += ["-p", project]
    command += ["-f", "compose.yaml"] + (["-f", "compose.gpu.yaml"] if gpu else [])
    command += ["--profile", "app", *args]
    result = subprocess.run(command, cwd=ROOT, check=True, text=True, capture_output=capture)
    return result.stdout if capture else ""


def wait_for_api(seconds: int = 180) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH, timeout=3) as response:  # noqa: S310 - fixed local URL
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit(f"the API didn't become healthy within {seconds} s ({HEALTH})")


def up(gpu: bool) -> None:
    print("stopping the everyday stack (its data is kept)...")
    compose("down", gpu=gpu)
    print(f"starting a clean copy as project {PROJECT}...")
    compose("up", "-d", project=PROJECT, gpu=gpu)
    wait_for_api()
    seeded = compose("exec", "-T", "api", "python", "-m", "app.cli.seed", project=PROJECT, gpu=gpu, capture=True)
    manager = next(line.split() for line in seeded.splitlines() if "priya.manager@" in line)
    subprocess.run([PYTHON, str(ROOT / "scripts" / "make_demo_docs.py")], check=True, capture_output=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "session-credentials.txt").write_text(
        "M4 usability session: the participant's account (fictional, this session only)\n"
        f"email: {manager[0]}\npassword: {manager[1]}\n",
        encoding="utf-8",
    )
    print("ready: http://127.0.0.1:13000/login (signed out)")
    print("files for the participant: data/demo-docs/Gift-Wrapping-Policy.pdf, Gift-Wrapping-Policy-recycled.pdf")
    print(f"sign-in details for the facilitator: {(OUT / 'session-credentials.txt').relative_to(ROOT)}")


def down(gpu: bool) -> None:
    print(f"removing {PROJECT} and its data...")
    compose("down", "-v", project=PROJECT, gpu=gpu)
    (OUT / "session-credentials.txt").unlink(missing_ok=True)
    print("starting the everyday stack again...")
    compose("up", "-d", gpu=gpu)
    wait_for_api()
    print("everyday stack is back: http://127.0.0.1:13000")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["up", "down"])
    parser.add_argument("--gpu", action="store_true", help="run the local models on the NVIDIA GPU")
    args = parser.parse_args()
    (up if args.action == "up" else down)(args.gpu)


if __name__ == "__main__":
    main()

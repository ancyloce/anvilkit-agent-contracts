#!/usr/bin/env python3
"""One entry point for the verification of anvilkit-agent-contracts, runnable from a
checkout of this repository alone (no parent or service source is read).

  sh tools/verification-env.sh [--only STEP ...]     # creates the pinned Python 3.12 environment,
                                                     # then runs this file
  .local/verification-venv/bin/python tools/verify.py [--only STEP ...]

Steps, in order:
  check       tools/check.py                buf lint, OpenAPI validation, JSON Schema fixtures, API/RPC vectors
  generate    pnpm install --frozen-lockfile in ts/, then tools/generate.py --check: the pinned
                                            generators (ts-proto and openapi-typescript come from that
                                            locked install) reproduce every checked-in binding
  go          go build/vet/test in go/ and tests/go   the generated Go module compiles on its own; the
                                            fixture tests agree with the vectors (kin-openapi, strict
                                            ProtoJSON + protovalidate)
  ts          pnpm install --frozen-lockfile, then check-types (tsc), lint (Biome) and test (Vitest) in ts/
  python      pytest in python/            the pydantic consumer agrees with the inference vectors

Each step prints PASS, FAIL or UNEXECUTED; an UNEXECUTED step is one whose tools are missing
and is never counted as a pass. Exit code 1 if anything failed, 2 if something could not run,
0 otherwise. Nothing is regenerated in place, deleted or published.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pyenv_check  # noqa: E402

pyenv_check.require("yaml", "jsonschema", "referencing", "openapi_spec_validator", "datamodel_code_generator", "pydantic", "pytest")

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable


def run(cmd: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess:
    # GOWORK=off: the Go modules of this repository are checked on their own,
    # never through a workspace of an enclosing checkout.
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, GOWORK="off"))


def script(cmd: list[str]) -> tuple[str, str]:
    p = run(cmd, ROOT)
    return ("PASS" if p.returncode == 0 else "FAIL"), p.stdout + p.stderr


def step_go() -> tuple[str, str]:
    if not shutil.which("go"):
        return "UNEXECUTED", "go is not on PATH"
    out = []
    for mod in ("go", "tests/go"):
        for cmd in (["go", "build", "./..."], ["go", "vet", "./..."], ["go", "test", "-count=1", "./..."]):
            p = run(cmd, ROOT / mod)
            out.append(f"{mod}: {' '.join(cmd[:2])} -> {'ok' if p.returncode == 0 else 'FAIL'}")
            if p.returncode != 0:
                return "FAIL", "\n".join(out + [p.stdout[-2000:], p.stderr[-2000:]])
    return "PASS", "\n".join(out)


def locked_install() -> tuple[str, str] | None:
    """The locked install in ts/ provides the Node generators (ts-proto,
    openapi-typescript) that generate.py runs and the TypeScript checks; a
    fresh clone has none of them. Idempotent; None when it succeeded."""
    pnpm = shutil.which("pnpm")
    if not pnpm or not shutil.which("node"):
        return "UNEXECUTED", "pnpm/node are not on PATH (Node.js 24 LTS and the packageManager of ts/package.json are required)"
    p = run([pnpm, "install", "--frozen-lockfile"], ROOT / "ts")
    if p.returncode != 0:
        return "FAIL", "ts: pnpm install --frozen-lockfile -> FAIL\n" + p.stdout[-2000:] + p.stderr[-2000:]
    return None


def step_generate() -> tuple[str, str]:
    if outcome := locked_install():
        return outcome
    return script([PY, "tools/generate.py", "--check"])


def step_ts() -> tuple[str, str]:
    if outcome := locked_install():
        return outcome
    pnpm = shutil.which("pnpm")
    out = ["ts: pnpm install --frozen-lockfile -> ok"]
    for name, cmd in (
        ("check-types", [pnpm, "run", "check-types"]),
        ("lint", [pnpm, "run", "lint"]),
        ("test", [pnpm, "run", "test"]),
    ):
        p = run(cmd, ROOT / "ts")
        out.append(f"ts: {name} -> {'ok' if p.returncode == 0 else 'FAIL'}")
        if p.returncode != 0:
            return "FAIL", "\n".join(out + [p.stdout[-2000:], p.stderr[-2000:]])
    return "PASS", "\n".join(out)


def step_python() -> tuple[str, str]:
    p = run([PY, "-m", "pytest", "-q"], ROOT / "python")
    return ("PASS" if p.returncode == 0 else "FAIL"), f"python: pytest -> {'ok' if p.returncode == 0 else 'FAIL'}\n{p.stdout[-1500:]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append")
    a = ap.parse_args()
    steps = [
        ("check", lambda: script([PY, "tools/check.py"])),
        ("generate", step_generate),
        ("go", step_go),
        ("ts", step_ts),
        ("python", step_python),
    ]
    outcomes = []
    for name, fn in steps:
        if a.only and name not in a.only:
            continue
        t0 = time.time()
        status, detail = fn()
        outcomes.append((name, status))
        print(f"{status:<11} {name:<10} ({time.time() - t0:.1f}s)")
        if status != "PASS":
            for line in detail.strip().splitlines()[-15:]:
                print(f"            | {line}")
    failed = [n for n, s in outcomes if s == "FAIL"]
    unexec = [n for n, s in outcomes if s == "UNEXECUTED"]
    print(f"\n{len(outcomes)} steps, {len(failed)} failed, {len(unexec)} unexecuted")
    if unexec:
        print("  unexecuted: " + ", ".join(unexec) + "   (tools missing; NOT a pass)")
    return 1 if failed else (2 if unexec else 0)


if __name__ == "__main__":
    sys.exit(main())

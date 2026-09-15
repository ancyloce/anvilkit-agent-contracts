#!/usr/bin/env python3
"""Read-only check of the AnvilKit contract baseline (anvilkit-agent-contracts).

Usage: python3 tools/check.py [--against GIT_REF]

Checks, each failing loudly when its input is missing:
  1. Proto: `buf lint` on the Buf module (proto/); with --against, `buf breaking` against the
     recorded baseline ref of this repository (never against legacy protos).
  2. OpenAPI: openapi/{agent,inference,model-proxy}.yaml validate as OpenAPI 3.0.
  3. JSON Schema: every **/*.schema.json passes the 2020-12 metaschema; every **/fixtures.json
     case validates (or is rejected) as declared; every profile in jobs/profiles.json validates
     as a Job profile.
  4. OpenAPI vectors: openapi/<spec>.fixtures.json for every spec (agent, inference,
     model-proxy): instances validate against the named component schema; raw cases are rejected
     by a strict parser (duplicate members, trailing data, non-finite numbers).
  5. Proto vectors: proto/messages.fixtures.json names existing messages of the Buf module; the
     protovalidate positive/negative outcomes themselves are asserted by tests/go (Go), the
     runtime that enforces them.

The same fixture files are consumed by the Go (tests/go), TypeScript (ts/test) and Python
(python/tests) consumer tests so every runtime agrees. This script writes nothing and
establishes nothing about runtime behavior. It runs under the pinned CPython 3.12 toolchain of
tools/requirements.txt (tools/verification-env.sh); any other interpreter or a missing pin exits
2 (UNEXECUTED) before anything is checked.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pyenv_check  # noqa: E402

pyenv_check.require("yaml", "jsonschema", "referencing", "openapi_spec_validator")

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACTS = ROOT
# The JSON Schema sources; the generated consumers (go/, ts/, python/) and
# their dependency directories hold verbatim copies and third-party schemas
# that are never the source.
SCHEMA_DIRS = ("jobs", "components", "events")
FAILURES: list[str] = []
COUNTS: dict[str, int] = {}


def fail(msg: str) -> None:
    FAILURES.append(msg)


def count(key: str) -> None:
    COUNTS[key] = COUNTS.get(key, 0) + 1


def require(path: pathlib.Path) -> bool:
    if not path.exists():
        fail(f"missing required input {path.relative_to(ROOT)}")
        return False
    return True


def strict_loads(text: str):
    def no_dupes(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError(f"duplicate member {k!r}")
            out[k] = v
        return out

    def bad_constant(name):
        raise ValueError(f"non-finite number {name}")

    return json.loads(text, object_pairs_hook=no_dupes, parse_constant=bad_constant)


def check_proto(against: str | None) -> None:
    buf = pathlib.Path(subprocess.run(["go", "env", "GOPATH"], capture_output=True, text=True).stdout.strip()) / "bin" / "buf"
    if not buf.exists():
        buf = shutil.which("buf")
    if not buf:
        fail("buf is not installed (pinned 1.73.0)")
        return
    if not require(CONTRACTS / "buf.yaml"):
        return
    p = subprocess.run([str(buf), "lint"], cwd=CONTRACTS, capture_output=True, text=True)
    if p.returncode != 0:
        fail("buf lint: " + (p.stdout + p.stderr).strip())
    else:
        count("buf_lint")
    if against:
        p = subprocess.run([str(buf), "breaking", "--against", f"{ROOT}/.git#ref={against}"],
                           cwd=CONTRACTS, capture_output=True, text=True)
        if p.returncode != 0:
            fail("buf breaking: " + (p.stdout + p.stderr).strip())
        else:
            count("buf_breaking")


def check_openapi() -> dict[str, dict]:
    import yaml
    from openapi_spec_validator import validate

    specs: dict[str, dict] = {}
    for name in ("agent", "inference", "model-proxy"):
        path = CONTRACTS / "openapi" / f"{name}.yaml"
        if not require(path):
            continue
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        try:
            validate(spec)
            count("openapi_valid")
            specs[name] = spec
        except Exception as e:  # noqa: BLE001
            fail(f"{path.relative_to(ROOT)}: {str(e)[:300]}")
    return specs


def check_schemas() -> None:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    schemas: dict[str, dict] = {}
    for path in sorted(p for d in SCHEMA_DIRS for p in (CONTRACTS / d).rglob("*.schema.json")):
        try:
            doc = strict_loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(doc)
            schemas[doc["$id"]] = doc
            count("schemas")
        except Exception as e:  # noqa: BLE001
            fail(f"{path.relative_to(ROOT)}: {e}")
    for expected in ("urn:anvilkit:jobs:v1", "urn:anvilkit:components:v1", "urn:anvilkit:events:v1"):
        if expected not in schemas:
            fail(f"missing required schema {expected}")
    registry = Registry().with_resources(
        [(i, Resource.from_contents(d, default_specification=DRAFT202012)) for i, d in schemas.items()])

    fixtures = sorted(p for d in SCHEMA_DIRS for p in (CONTRACTS / d).rglob("fixtures.json"))
    if not fixtures:
        fail("no {jobs,components,events}/**/fixtures.json found")
    for path in fixtures:
        try:
            doc = strict_loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            fail(f"{path.relative_to(ROOT)}: {e}")
            continue
        for case in doc["cases"]:
            v = Draft202012Validator({"$ref": doc["schema"] + case["ref"]}, registry=registry)
            ok = v.is_valid(case["instance"])
            if ok != case["valid"]:
                fail(f"{path.relative_to(ROOT)}: {case['name']!r} expected valid={case['valid']}")
            else:
                count("fixture_cases")

    profiles = CONTRACTS / "jobs" / "profiles.json"
    if require(profiles) and "urn:anvilkit:jobs:v1" in schemas:
        doc = strict_loads(profiles.read_text(encoding="utf-8"))
        v = Draft202012Validator({"$ref": "urn:anvilkit:jobs:v1#/$defs/profile"}, registry=registry)
        ids = set()
        for prof in doc.get("profiles", []):
            errors = list(v.iter_errors(prof))
            if errors:
                fail(f"jobs/profiles.json {prof.get('profileId')}: {errors[0].message[:200]}")
            elif prof["profileId"] in ids:
                fail(f"jobs/profiles.json duplicate profileId {prof['profileId']}")
            else:
                ids.add(prof["profileId"])
                count("job_profiles")
        if "local-check-v1" not in ids:
            fail("jobs/profiles.json lacks the local-check-v1 fixture profile")


def check_api_vectors(specs: dict[str, dict]) -> None:
    from jsonschema import Draft4Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT4

    for name in ("agent", "inference", "model-proxy"):
        spec = specs.get(name)
        path = CONTRACTS / "openapi" / f"{name}.fixtures.json"
        if spec is None or not require(path):
            continue
        label = path.name
        doc = strict_loads(path.read_text(encoding="utf-8"))
        uri = f"urn:anvilkit:openapi:{name}"
        registry = Registry().with_resource(uri, Resource.from_contents(spec, default_specification=DRAFT4))
        for case in doc["cases"]:
            schema = case["schema"]
            if schema not in spec["components"]["schemas"]:
                fail(f"{label} {case['name']!r}: unknown schema {schema}")
                continue
            if "raw" in case:
                try:
                    strict_loads(case["raw"])
                    fail(f"{label} {case['name']!r}: strict parser accepted the raw document")
                except ValueError:
                    count("strict_raw_rejected")
                continue
            v = Draft4Validator({"$ref": f"{uri}#/components/schemas/{schema}"}, registry=registry)
            ok = v.is_valid(case["instance"])
            if ok != case["valid"]:
                fail(f"{label} {case['name']!r}: expected valid={case['valid']}")
            else:
                count(f"api_vectors[{name}]")


def check_proto_vectors() -> None:
    """The message names of the proto vectors must exist in the Buf module; Go asserts the outcomes."""
    import re

    path = CONTRACTS / "proto" / "messages.fixtures.json"
    if not require(path):
        return
    messages: set[str] = set()
    for proto in sorted((CONTRACTS / "proto").rglob("*.proto")):
        text = proto.read_text(encoding="utf-8")
        pkg = re.search(r"^package\s+([\w.]+);", text, flags=re.M)
        if not pkg:
            fail(f"{proto.relative_to(ROOT)}: no package statement")
            continue
        for m in re.finditer(r"^message\s+(\w+)\s*\{", text, flags=re.M):
            messages.add(f"{pkg.group(1)}.{m.group(1)}")
    doc = strict_loads(path.read_text(encoding="utf-8"))
    for case in doc["cases"]:
        for key in ("name", "message", "valid", "instance"):
            if key not in case:
                fail(f"proto/messages.fixtures.json {case.get('name')!r}: missing {key}")
        if case.get("message") not in messages:
            fail(f"proto/messages.fixtures.json {case.get('name')!r}: unknown message {case.get('message')}")
        else:
            count("proto_vectors")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--against", help="git ref holding the recorded new-system Proto baseline for buf breaking")
    a = ap.parse_args()
    if not (CONTRACTS / "proto").is_dir() or not (CONTRACTS / "openapi").is_dir():
        print("FAIL proto/ or openapi/ is missing; run from a checkout of anvilkit-agent-contracts")
        return 1
    check_proto(a.against)
    specs = check_openapi()
    check_schemas()
    check_api_vectors(specs)
    check_proto_vectors()
    for f in FAILURES:
        print("FAIL", f)
    print("counts:", json.dumps(COUNTS, sort_keys=True))
    print(f"contract check: {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

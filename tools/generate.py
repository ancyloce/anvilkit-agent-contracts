#!/usr/bin/env python3
"""Single generation entry point of the AnvilKit contract baseline (anvilkit-agent-contracts).

Usage:
  python3 tools/generate.py           # regenerate every binding in place
  python3 tools/generate.py --check   # regenerate into a scratch tree and fail on drift

Sources (authoritative, hand-maintained, all in this repository):
  proto/anvilkit/{control,knowledge,mcp}/v1/*.proto   Buf module, protovalidate rules
  openapi/agent.yaml                                  public REST/SSE (OpenAPI 3.0.3)
  openapi/model-proxy.yaml                            Model Proxy transport (OpenAPI 3.0.3)
  openapi/inference.yaml                              compute HTTP (OpenAPI 3.0.3)
  jobs/{job.schema.json,profiles.json}                Job envelope and profiles

Generated outputs (checked in, never hand-edited; consumers listed in README.md):
  go/anvilkit/<domain>/v1/*.pb.go, *_grpc.pb.go      protoc-gen-go + protoc-gen-go-grpc via buf
  go/agentapi/agent.gen.go                            oapi-codegen (types, Gin strict server, embedded spec)
  go/modelproxyapi/model-proxy.gen.go                 oapi-codegen (types, client, embedded spec)
  go/jobschema/{job.schema.json,profiles.json}        verbatim copies of jobs/ for Go embedding
  ts/src/proto/**/*.ts                                ts-proto (grpc-js services) via buf (buf.gen.ts.yaml)
  ts/src/proto/descriptors.ts                         buf build (FileDescriptorSet, base64) for the TS validation boundary
  ts/src/openapi/{agent,model-proxy,inference}.ts     openapi-typescript
  python/anvilkit_generated_clients/inference.py      datamodel-code-generator (pydantic v2)

Nothing outside this repository is read: service-owned generation (for example
Control's sqlc output) lives with the service that owns its schema. The pinned
tool identities below are verified before anything is generated so an older
binary on PATH cannot silently produce different output. The Go tools come from
GOPATH/bin, the Node tools from ts/node_modules (pnpm install --frozen-lockfile
in ts/), datamodel-codegen from the pinned Python toolchain
(tools/verification-env.sh).
"""
from __future__ import annotations

import argparse
import base64
import filecmp
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pyenv_check  # noqa: E402

pyenv_check.require("datamodel_code_generator")

ROOT = pathlib.Path(__file__).resolve().parent.parent
GOBIN = pathlib.Path(subprocess.run(["go", "env", "GOPATH"], capture_output=True, text=True, check=True).stdout.strip()) / "bin"
TS_PKG = ROOT / "ts"
PY_PKG = ROOT / "python"
NODE_BIN = TS_PKG / "node_modules/.bin"
PY_BIN = pathlib.Path(sys.executable).parent

TOOLS = {
    # name: (version flag, regex, pinned)
    "buf": (["--version"], r"(\d+\.\d+\.\d+)", "1.73.0"),
    "protoc-gen-go": (["--version"], r"v(\d+\.\d+\.\d+)", "1.36.12"),
    "protoc-gen-go-grpc": (["--version"], r"(\d+\.\d+\.\d+)", "1.6.2"),
    "oapi-codegen": (["-version"], r"v(\d+\.\d+\.\d+)", "2.8.0"),
    "datamodel-codegen": (["--version"], r"(\d+\.\d+\.\d+)", "0.81.0"),
}

# Node tools are pinned by ts/package.json and its lockfile; their installed
# package.json version is verified here.
NODE_TOOLS = {"ts-proto": "2.12.3", "openapi-typescript": "7.13.0"}

OUTPUTS = [
    ("go/anvilkit", "buf"),
    ("go/agentapi", "oapi-codegen"),
    ("go/modelproxyapi", "oapi-codegen"),
    ("go/jobschema/job.schema.json", "copy"),
    ("go/jobschema/profiles.json", "copy"),
    ("ts/src/proto", "ts-proto"),
    ("ts/src/openapi", "openapi-typescript"),
    ("python/anvilkit_generated_clients/inference.py", "datamodel-codegen"),
]

OPENAPI_TS = ("agent", "model-proxy", "inference")


def resolve(tool: str) -> pathlib.Path:
    candidate = (PY_BIN if tool == "datamodel-codegen" else GOBIN) / tool
    if not candidate.exists():
        found = shutil.which(tool)
        if not found:
            sys.exit(f"FAIL: {tool} is not installed; see README.md for the pinned installs")
        candidate = pathlib.Path(found)
    flag, pattern, pinned = TOOLS[tool]
    out = subprocess.run([str(candidate), *flag], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    text = (out.stdout + out.stderr).strip().splitlines()[-1] if (out.stdout + out.stderr).strip() else ""
    m = re.search(pattern, text)
    if not m or m.group(1) != pinned:
        sys.exit(f"FAIL: {tool} at {candidate} reports {text!r}; pinned {pinned}")
    return candidate


def resolve_node() -> None:
    """The locked workspace install supplies the pinned Node generators."""
    for name, pinned in NODE_TOOLS.items():
        manifest = TS_PKG / "node_modules" / name / "package.json"
        if not manifest.exists():
            sys.exit(f"FAIL: {name} is not installed under {TS_PKG.relative_to(ROOT)}/node_modules; run `pnpm install --frozen-lockfile` in ts/")
        version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
        if version != pinned:
            sys.exit(f"FAIL: {name} {version} installed; pinned {pinned}")
    if not (NODE_BIN / "protoc-gen-ts_proto").exists() or not (NODE_BIN / "openapi-typescript").exists():
        sys.exit("FAIL: ts/node_modules/.bin lacks protoc-gen-ts_proto or openapi-typescript; run `pnpm install --frozen-lockfile` in ts/")


def run(cmd: list[str], cwd: pathlib.Path, env: dict[str, str]) -> None:
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"FAIL: {' '.join(cmd)} in {cwd}\n{p.stdout}{p.stderr}")


def generate(target_root: pathlib.Path) -> None:
    tools = {t: resolve(t) for t in TOOLS}
    resolve_node()
    env = dict(os.environ)
    env["PATH"] = f"{GOBIN}{os.pathsep}{env.get('PATH', '')}"
    contracts = ROOT

    gen_go = target_root / "go"
    shutil.rmtree(gen_go / "anvilkit", ignore_errors=True)
    run([str(tools["buf"]), "generate", "--template", str(contracts / "buf.gen.yaml"), "-o", str(gen_go)], contracts, env)

    api_out = gen_go / "agentapi"
    api_out.mkdir(parents=True, exist_ok=True)
    run([str(tools["oapi-codegen"]), "-config", str(contracts / "openapi" / "oapi-codegen.yaml"),
         "-o", str(api_out / "agent.gen.go"), str(contracts / "openapi" / "agent.yaml")], contracts, env)

    proxy_out = gen_go / "modelproxyapi"
    proxy_out.mkdir(parents=True, exist_ok=True)
    run([str(tools["oapi-codegen"]), "-config", str(contracts / "openapi" / "oapi-codegen.model-proxy.yaml"),
         "-o", str(proxy_out / "model-proxy.gen.go"), str(contracts / "openapi" / "model-proxy.yaml")], contracts, env)

    jobschema = gen_go / "jobschema"
    jobschema.mkdir(parents=True, exist_ok=True)
    for name in ("job.schema.json", "profiles.json"):
        shutil.copyfile(ROOT / "jobs" / name, jobschema / name)

    gen_ts = target_root / "ts/src"
    shutil.rmtree(gen_ts / "proto", ignore_errors=True)
    (gen_ts / "proto").mkdir(parents=True, exist_ok=True)
    run([str(tools["buf"]), "generate", "--template", str(contracts / "buf.gen.ts.yaml"), "-o", str(gen_ts / "proto")], contracts, env)
    # ts-proto emits no descriptors; the explicit TS checks of the RPC surface
    # (ts/src/validation/rpc.ts: protovalidate-es and strict ProtoJSON) need
    # the FileDescriptorSet of the module and its imports,
    # so the buf image is embedded as one generated module (source info excluded
    # for a stable, comment-free artifact).
    image = subprocess.run([str(tools["buf"]), "build", "--exclude-source-info", "--as-file-descriptor-set", "-o", "-"],
                           cwd=contracts, env=env, check=True, capture_output=True).stdout
    (gen_ts / "proto" / "descriptors.ts").write_text(
        "// Code generated by tools/generate.py (buf build --exclude-source-info --as-file-descriptor-set). DO NOT EDIT.\n"
        "// google.protobuf.FileDescriptorSet (base64) of proto/ and its imports:\n"
        "// the runtime descriptors of the TypeScript validation boundary (src/validation/rpc.ts).\n\n"
        f"export const fileDescriptorSet =\n  \"{base64.b64encode(image).decode('ascii')}\";\n",
        encoding="utf-8", newline="\n")
    (gen_ts / "openapi").mkdir(parents=True, exist_ok=True)
    for name in OPENAPI_TS:
        run([str(NODE_BIN / "openapi-typescript"), str(contracts / "openapi" / f"{name}.yaml"), "-o", str(gen_ts / "openapi" / f"{name}.ts")], TS_PKG, env)

    gen_py = target_root / "python/anvilkit_generated_clients"
    gen_py.mkdir(parents=True, exist_ok=True)
    run([str(tools["datamodel-codegen"]), "--input", str(contracts / "openapi" / "inference.yaml"), "--input-file-type", "openapi",
         "--output", str(gen_py / "inference.py"), "--output-model-type", "pydantic_v2.BaseModel", "--target-python-version", "3.12",
         "--use-annotated", "--strict-nullable", "--field-constraints", "--use-schema-description", "--disable-timestamp",
         "--collapse-root-models", "--formatters", "builtin"], PY_PKG, env)


def tree_differs(a: pathlib.Path, b: pathlib.Path) -> list[str]:
    diffs: list[str] = []
    cmp = filecmp.dircmp(a, b)

    def walk(c: filecmp.dircmp, prefix: str) -> None:
        for n in c.left_only:
            diffs.append(f"missing in checkout: {prefix}{n}")
        for n in c.right_only:
            diffs.append(f"stale in checkout: {prefix}{n}")
        for n in c.diff_files:
            diffs.append(f"differs: {prefix}{n}")
        for n, sub in c.subdirs.items():
            walk(sub, f"{prefix}{n}/")

    walk(cmp, "")
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if regeneration would change any checked-in output")
    a = ap.parse_args()
    if not a.check:
        generate(ROOT)
        print("generated:", ", ".join(o for o, _ in OUTPUTS))
        return 0
    with tempfile.TemporaryDirectory() as scratch:
        scratch_root = pathlib.Path(scratch)
        # The templates take the output directory from -o; mirror the layout in scratch.
        (scratch_root / "go").mkdir(parents=True)
        (scratch_root / "ts/src").mkdir(parents=True)
        generate(scratch_root)
        problems: list[str] = []
        for rel, tool in OUTPUTS:
            fresh, current = scratch_root / rel, ROOT / rel
            if not fresh.exists():
                problems.append(f"generator produced no output for {rel}")
                continue
            if not current.exists():
                problems.append(f"missing in checkout: {rel}")
                continue
            if fresh.is_file():
                if not filecmp.cmp(fresh, current, shallow=False):
                    problems.append(f"differs: {rel}")
                continue
            problems += [f"{rel}: {d}" for d in tree_differs(fresh, current)]
        for p in problems:
            print("FAIL", p)
        print(f"generation check: {len(problems)} differences")
        return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

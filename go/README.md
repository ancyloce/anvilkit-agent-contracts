# Generated Go bindings (`github.com/ancyloce/anvilkit-agent-contracts/go`)

Generated from the sources of this repository by `python3 tools/generate.py`; never hand-edited.
`python3 tools/generate.py --check` regenerates into a scratch tree and fails on drift. The
outputs are checked in so every consumer builds from a tagged version without the generators.

| Package | Source | Generator (pinned) | Consumers |
| --- | --- | --- | --- |
| `anvilkit/control/v1` (`controlv1`) | `proto/anvilkit/control/v1/*.proto` | buf 1.73.0, protoc-gen-go 1.36.12, protoc-gen-go-grpc 1.6.2 | `anvilkit-agent-control` (server), `anvilkit-agent-api` and `anvilkit-agent-workflow` (clients) |
| `anvilkit/knowledge/v1` (`knowledgev1`) | `proto/anvilkit/knowledge/v1/knowledge.proto` | same | `anvilkit-agent-api` client (handlers arrive with P15–P17); Knowledge itself consumes the ts-proto output |
| `anvilkit/mcp/v1` (`mcpv1`) | `proto/anvilkit/mcp/v1/mcp.proto` | same | `anvilkit-agent-api` client (handlers arrive with P18–P19); `anvilkit-agent-mcp` server (P18) |
| `agentapi` | `openapi/agent.yaml` | oapi-codegen 2.8.0 (models, Gin strict server, unpruned embedded spec) | `anvilkit-agent-api`; `tests/go` validates the public vectors against it |
| `modelproxyapi` | `openapi/model-proxy.yaml` | oapi-codegen 2.8.0 (models, typed client, unpruned embedded spec) | `tests/go` (model-proxy vectors); the Workflow's fixed Activities and the trusted sidecar from P11/P13 |
| `jobschema` | `jobs/{job.schema.json,profiles.json}` (verbatim copies) | copy + hand-written validator | `anvilkit-agent-control` (result manifests), `anvilkit-agent-workflow` |

Pinned installs (recorded in `tools/generate.py`):

```sh
go install github.com/bufbuild/buf/cmd/buf@v1.73.0
go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.12
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.6.2
go install github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@v2.8.0
```

Consumers require an explicit version (`go get github.com/ancyloce/anvilkit-agent-contracts/go@v0.1.1`); the module is tagged `go/vX.Y.Z` on this repository. `tests/go` (the fixture-agreement suite) is a separate module so this one carries no test-only dependencies.

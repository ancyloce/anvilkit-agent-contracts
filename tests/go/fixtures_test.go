// Package contracts holds the Go-side consumer tests of the contract baseline:
// the generated Go module (../../go) against the same fixture files that
// tools/check.py (Python), ts/test (TypeScript) and python/tests (Python
// consumer) read, so every runtime agrees on every positive and negative
// vector. Nothing here touches a service or a database.
package contracts

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"testing"

	"buf.build/go/protovalidate"
	"github.com/getkin/kin-openapi/openapi3"
	"github.com/santhosh-tekuri/jsonschema/v6"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/reflect/protoreflect"
	"google.golang.org/protobuf/reflect/protoregistry"

	"github.com/ancyloce/anvilkit-agent-contracts/go/agentapi"
	_ "github.com/ancyloce/anvilkit-agent-contracts/go/anvilkit/control/v1"
	_ "github.com/ancyloce/anvilkit-agent-contracts/go/anvilkit/knowledge/v1"
	_ "github.com/ancyloce/anvilkit-agent-contracts/go/anvilkit/mcp/v1"
	"github.com/ancyloce/anvilkit-agent-contracts/go/modelproxyapi"
)

var contractsDir = filepath.Join("..", "..")

type fixtureFile struct {
	Schema string `json:"schema"`
	Cases  []struct {
		Name     string          `json:"name"`
		Ref      string          `json:"ref"`
		Valid    bool            `json:"valid"`
		Instance json.RawMessage `json:"instance"`
	} `json:"cases"`
}

func compileSchemas(t *testing.T) *jsonschema.Compiler {
	t.Helper()
	c := jsonschema.NewCompiler()
	matches, err := filepath.Glob(filepath.Join(contractsDir, "*", "*.schema.json"))
	require.NoError(t, err)
	require.NotEmpty(t, matches, "contracts/**/*.schema.json must exist")
	for _, m := range matches {
		raw, err := os.ReadFile(m)
		require.NoError(t, err)
		doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(raw))
		require.NoError(t, err, m)
		id := doc.(map[string]any)["$id"].(string)
		require.NoError(t, c.AddResource(id, doc))
	}
	return c
}

// TestSchemaFixturesAgree validates every jobs/components/events fixture case
// with the Go JSON Schema implementation.
func TestSchemaFixturesAgree(t *testing.T) {
	c := compileSchemas(t)
	files, err := filepath.Glob(filepath.Join(contractsDir, "*", "fixtures.json"))
	require.NoError(t, err)
	require.NotEmpty(t, files)
	total := 0
	for _, f := range files {
		raw, err := os.ReadFile(f)
		require.NoError(t, err)
		var fx fixtureFile
		require.NoError(t, json.Unmarshal(raw, &fx))
		for _, cs := range fx.Cases {
			schema, err := c.Compile(fx.Schema + cs.Ref)
			require.NoError(t, err, cs.Name)
			inst, err := jsonschema.UnmarshalJSON(bytes.NewReader(cs.Instance))
			require.NoError(t, err)
			verr := schema.Validate(inst)
			require.Equal(t, cs.Valid, verr == nil, "%s: %s (%v)", filepath.Base(filepath.Dir(f)), cs.Name, verr)
			total++
		}
	}
	require.GreaterOrEqual(t, total, 20)
}

// TestJobProfilesValidate proves every launchable profile is a valid Job
// profile and that the P05 fixture profile exists and runs no candidate code.
func TestJobProfilesValidate(t *testing.T) {
	c := compileSchemas(t)
	schema, err := c.Compile("urn:anvilkit:jobs:v1#/$defs/profile")
	require.NoError(t, err)
	raw, err := os.ReadFile(filepath.Join(contractsDir, "jobs", "profiles.json"))
	require.NoError(t, err)
	var doc struct {
		Profiles []json.RawMessage `json:"profiles"`
	}
	require.NoError(t, json.Unmarshal(raw, &doc))
	found := false
	for _, p := range doc.Profiles {
		inst, err := jsonschema.UnmarshalJSON(bytes.NewReader(p))
		require.NoError(t, err)
		require.NoError(t, schema.Validate(inst))
		m := inst.(map[string]any)
		if m["profileId"] == "local-check-v1" {
			found = true
			require.Equal(t, false, m["candidateCode"])
			require.NotEmpty(t, m["expectedResult"])
			expected := m["expectedResult"].(map[string]any)
			require.NotEmpty(t, expected["resultDigest"])
			require.NotEmpty(t, expected["resultSizeBytes"], "the fixed result binds a reviewed byte size, not only a digest")
		}
	}
	require.True(t, found, "local-check-v1 profile present")
}

type apiVectorFile struct {
	Cases []struct {
		Name     string          `json:"name"`
		Schema   string          `json:"schema"`
		Valid    bool            `json:"valid"`
		Instance json.RawMessage `json:"instance"`
		Raw      string          `json:"raw"`
	} `json:"cases"`
}

// strictDecode mirrors the API's parser contract: unknown fields, duplicate
// members, trailing values and non-finite numbers are rejected before schema
// validation.
func strictDecode(raw []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return nil, errors.New("trailing data after the JSON value")
	}
	if err := rejectDuplicates(json.NewDecoder(bytes.NewReader(raw))); err != nil {
		return nil, err
	}
	return v, nil
}

func rejectDuplicates(dec *json.Decoder) error {
	tok, err := dec.Token()
	if err != nil {
		return err
	}
	switch d := tok.(type) {
	case json.Delim:
		switch d {
		case '{':
			seen := map[string]bool{}
			for dec.More() {
				keyTok, err := dec.Token()
				if err != nil {
					return err
				}
				key := keyTok.(string)
				if seen[key] {
					return errors.New("duplicate member " + key)
				}
				seen[key] = true
				if err := rejectDuplicates(dec); err != nil {
					return err
				}
			}
			_, err = dec.Token()
			return err
		case '[':
			for dec.More() {
				if err := rejectDuplicates(dec); err != nil {
					return err
				}
			}
			_, err = dec.Token()
			return err
		}
	}
	return nil
}

// runOpenAPIVectors validates <name>.fixtures.json with kin-openapi, the
// validator the Go consumers run, against the given spec document.
func runOpenAPIVectors(t *testing.T, name string, spec *openapi3.T) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(contractsDir, "openapi", name+".fixtures.json"))
	require.NoError(t, err, "every OpenAPI surface has a fixture file")
	var vectors apiVectorFile
	require.NoError(t, json.Unmarshal(raw, &vectors))
	require.NotEmpty(t, vectors.Cases)
	for _, cs := range vectors.Cases {
		ref, ok := spec.Components.Schemas[cs.Schema]
		require.True(t, ok, "%s: schema %s", cs.Name, cs.Schema)
		if cs.Raw != "" {
			_, err := strictDecode([]byte(cs.Raw))
			require.Error(t, err, "%s: strict parser must reject", cs.Name)
			continue
		}
		inst, err := strictDecode(cs.Instance)
		require.NoError(t, err, cs.Name)
		verr := ref.Value.VisitJSON(inst)
		require.Equal(t, cs.Valid, verr == nil, "%s: %s (%v)", name, cs.Name, verr)
	}
}

// TestPublicAPIVectors validates agent.fixtures.json against the embedded
// generated spec the API serves and validates with.
func TestPublicAPIVectors(t *testing.T) {
	spec, err := agentapi.GetSwagger()
	require.NoError(t, err)
	runOpenAPIVectors(t, "agent", spec)
}

// TestModelProxyVectors validates model-proxy.fixtures.json against the
// embedded spec of the generated Go client (the Workflow/sidecar consumer).
func TestModelProxyVectors(t *testing.T) {
	spec, err := modelproxyapi.GetSwagger()
	require.NoError(t, err)
	runOpenAPIVectors(t, "model-proxy", spec)
}

// TestInferenceVectors validates inference.fixtures.json against the source
// document; its Go-side consumer does not exist (Knowledge is TypeScript,
// Inference is Python), so the source is loaded directly.
func TestInferenceVectors(t *testing.T) {
	loader := openapi3.NewLoader()
	spec, err := loader.LoadFromFile(filepath.Join(contractsDir, "openapi", "inference.yaml"))
	require.NoError(t, err)
	require.NoError(t, spec.Validate(loader.Context))
	runOpenAPIVectors(t, "inference", spec)
}

type protoVectorFile struct {
	Cases []struct {
		Name     string          `json:"name"`
		Message  string          `json:"message"`
		Valid    bool            `json:"valid"`
		Instance json.RawMessage `json:"instance"`
	} `json:"cases"`
}

// TestProtoVectors parses every internal RPC vector strictly (ProtoJSON,
// unknown fields rejected) into the generated Go message and asserts the
// protovalidate outcome, which is exactly what the grpc-go interceptors
// enforce before any handler runs.
func TestProtoVectors(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(contractsDir, "proto", "messages.fixtures.json"))
	require.NoError(t, err)
	var vectors protoVectorFile
	require.NoError(t, json.Unmarshal(raw, &vectors))
	require.NotEmpty(t, vectors.Cases)
	validator, err := protovalidate.New()
	require.NoError(t, err)
	domains := map[string]bool{}
	for _, cs := range vectors.Cases {
		mt, err := protoregistry.GlobalTypes.FindMessageByName(protoreflect.FullName(cs.Message))
		require.NoError(t, err, "%s: message %s must exist in the generated bindings", cs.Name, cs.Message)
		domains[string(mt.Descriptor().ParentFile().Package())] = true
		msg := mt.New().Interface()
		if err := (protojson.UnmarshalOptions{DiscardUnknown: false}).Unmarshal(cs.Instance, msg); err != nil {
			require.False(t, cs.Valid, "%s: strict parse failed for a vector declared valid: %v", cs.Name, err)
			continue
		}
		verr := validator.Validate(msg)
		require.Equal(t, cs.Valid, verr == nil, "%s (%v)", cs.Name, verr)
	}
	for _, pkg := range []string{"anvilkit.control.v1", "anvilkit.knowledge.v1", "anvilkit.mcp.v1"} {
		require.True(t, domains[pkg], "vectors cover %s", pkg)
	}
}

// Package jobschema exposes the jobs contract (contracts/jobs) to Go
// consumers. job.schema.json and profiles.json are verbatim copies refreshed
// by tools/generate-contracts.py; edit the sources under contracts/, never
// these copies.
package jobschema

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"fmt"
	"sync"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

//go:embed job.schema.json
var schemaJSON []byte

//go:embed profiles.json
var profilesJSON []byte

const schemaID = "urn:anvilkit:jobs:v1"

var (
	compileOnce sync.Once
	compiler    *jsonschema.Compiler
	compileErr  error
)

func compile(ref string) (*jsonschema.Schema, error) {
	compileOnce.Do(func() {
		doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(schemaJSON))
		if err != nil {
			compileErr = err
			return
		}
		c := jsonschema.NewCompiler()
		if err := c.AddResource(schemaID, doc); err != nil {
			compileErr = err
			return
		}
		compiler = c
	})
	if compileErr != nil {
		return nil, compileErr
	}
	return compiler.Compile(schemaID + ref)
}

// ValidateResultManifest validates manifest bytes against
// urn:anvilkit:jobs:v1#/$defs/resultManifest.
func ValidateResultManifest(manifest []byte) error {
	return validate("#/$defs/resultManifest", manifest)
}

// ValidateLaunchEnvelope validates envelope bytes against
// urn:anvilkit:jobs:v1#/$defs/launchEnvelope.
func ValidateLaunchEnvelope(envelope []byte) error {
	return validate("#/$defs/launchEnvelope", envelope)
}

func validate(ref string, raw []byte) error {
	s, err := compile(ref)
	if err != nil {
		return fmt.Errorf("jobschema: compile %s: %w", ref, err)
	}
	inst, err := jsonschema.UnmarshalJSON(bytes.NewReader(raw))
	if err != nil {
		return fmt.Errorf("jobschema: parse: %w", err)
	}
	return s.Validate(inst)
}

// ImageRef pins an image: a repository (completed with the launching
// environment's registry when it names none) and the digest that is the
// identity.
type ImageRef struct {
	Repository string `json:"repository"`
	Digest     string `json:"digest"`
}

// Profile is a reviewed fixed Job profile (contracts/jobs/profiles.json).
type Profile struct {
	SchemaVersion int      `json:"schemaVersion"`
	ProfileID     string   `json:"profileId"`
	Revision      string   `json:"revision"`
	JobKind       string   `json:"jobKind"`
	Description   string   `json:"description,omitempty"`
	Image         ImageRef `json:"image"`
	// SidecarImage is the trusted access sidecar of a harness profile; its
	// presence selects the two-container harness layout (DD-03 §5).
	SidecarImage *ImageRef `json:"sidecarImage,omitempty"`
	Entrypoint   []string  `json:"entrypoint"`
	Resources    struct {
		CPU    string `json:"cpu"`
		Memory string `json:"memory"`
	} `json:"resources"`
	DeadlineSeconds int    `json:"deadlineSeconds"`
	RuntimeClass    string `json:"runtimeClass,omitempty"`
	CandidateCode   bool   `json:"candidateCode"`
	// ExpectedResult is the one fixed result of a qualification fixture:
	// its digest and byte size (a decimal string, as sizeBytes on the
	// result manifest) the trusted observer and Control compare with.
	ExpectedResult *struct {
		ResultDigest    string `json:"resultDigest"`
		ResultSizeBytes string `json:"resultSizeBytes"`
	} `json:"expectedResult,omitempty"`
}

// Profiles returns every reviewed profile after validating each against the
// schema, so an edited copy cannot silently widen what may be launched.
func Profiles() ([]Profile, error) {
	var doc struct {
		Profiles []json.RawMessage `json:"profiles"`
	}
	if err := json.Unmarshal(profilesJSON, &doc); err != nil {
		return nil, fmt.Errorf("jobschema: profiles: %w", err)
	}
	out := make([]Profile, 0, len(doc.Profiles))
	for _, raw := range doc.Profiles {
		if err := validate("#/$defs/profile", raw); err != nil {
			return nil, fmt.Errorf("jobschema: profile rejected: %w", err)
		}
		var p Profile
		if err := json.Unmarshal(raw, &p); err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, nil
}

// Profile returns one reviewed profile by id.
func ProfileByID(id string) (Profile, error) {
	all, err := Profiles()
	if err != nil {
		return Profile{}, err
	}
	for _, p := range all {
		if p.ProfileID == id {
			return p, nil
		}
	}
	return Profile{}, fmt.Errorf("jobschema: no reviewed profile %q", id)
}

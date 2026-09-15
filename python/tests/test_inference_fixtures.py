"""Consumer/provider agreement of the generated pydantic models with the
cross-runtime vectors of openapi/inference.fixtures.json: every
`instance` case validates (or is rejected) by the model the Python provider
will enforce, exactly as tools/check.py (jsonschema), tests/go
(Go, kin-openapi) and the TypeScript package (Ajv) decide; every `raw` case is
rejected by the strict loader the provider must use before validation
(duplicate members, trailing data, non-finite numbers; contracts.md §4)."""
from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import BaseModel, ValidationError

from anvilkit_generated_clients import inference

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "openapi" / "inference.fixtures.json"


def strict_loads(text: str):
    """JSON loading with the strict rules of contracts.md §4."""

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


CASES = strict_loads(FIXTURES.read_text(encoding="utf-8"))["cases"]


def model_for(schema: str) -> type[BaseModel]:
    model = getattr(inference, schema, None)
    assert model is not None and issubclass(model, BaseModel), f"{schema} is generated"
    return model


def validate(model: type[BaseModel], instance) -> BaseModel:
    """The provider pipeline: strict pre-parse (strict_loads) of the request
    body, then pydantic's JSON-mode strict validation of the same document
    (no type coercion; enums and constrained strings as the schema states)."""
    document = json.dumps(instance, ensure_ascii=False)
    strict_loads(document)
    return model.model_validate_json(document, strict=True)


@pytest.mark.parametrize("case", [c for c in CASES if "instance" in c], ids=lambda c: c["name"])
def test_instance_vectors(case):
    model = model_for(case["schema"])
    if case["valid"]:
        loaded = validate(model, case["instance"])
        assert loaded.model_dump(mode="json", exclude_none=True, by_alias=True) == case["instance"]
    else:
        with pytest.raises(ValidationError):
            validate(model, case["instance"])


@pytest.mark.parametrize("case", [c for c in CASES if "raw" in c], ids=lambda c: c["name"])
def test_raw_vectors_are_rejected_by_the_strict_loader(case):
    model_for(case["schema"])
    with pytest.raises(ValueError):
        strict_loads(case["raw"])


def test_every_fixture_schema_forbids_unknown_members():
    for schema in sorted({c["schema"] for c in CASES}):
        assert model_for(schema).model_config.get("extra") == "forbid", schema

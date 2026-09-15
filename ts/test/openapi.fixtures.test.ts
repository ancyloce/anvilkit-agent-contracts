// Consumer-side agreement of the OpenAPI surfaces with the cross-runtime
// vectors (openapi/<spec>.fixtures.json): the generated
// openapi-typescript component exists for every named schema, and every case
// goes through the boundary's pipeline. `instance` cases are serialized,
// parsed by the strict parser of src/validation/json.ts and then validated
// (or rejected) against the source document's component schema exactly as
// tools/check.py (Python) and tests/go (Go, kin-openapi)
// decide; `raw` cases are byte-exact documents the strict parser itself must
// reject before any schema validation (duplicate members, trailing data,
// non-finite numbers), so they are executed against that parser, not counted.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import Ajv from "ajv";
import addFormats from "ajv-formats";
import { load } from "js-yaml";
import { describe, expect, it } from "vitest";
import { parseStrictJson } from "../src/validation/json.js";

type Case = { name: string; schema: string; valid?: boolean; instance?: unknown; raw?: string };

const contracts = new URL("../../openapi/", import.meta.url);

function read(rel: string): string {
	return readFileSync(fileURLToPath(new URL(rel, contracts)), "utf8");
}

for (const spec of ["agent", "model-proxy", "inference"]) {
	describe(`${spec}.yaml vectors`, () => {
		const document = load(read(`${spec}.yaml`)) as { components: { schemas: Record<string, unknown> } };
		const fixtures = JSON.parse(read(`${spec}.fixtures.json`)) as { cases: Case[] };
		const generated = readFileSync(fileURLToPath(new URL(`../src/openapi/${spec}.ts`, import.meta.url)), "utf8");
		const ajv = new Ajv({ strict: false, allErrors: true });
		addFormats(ajv);
		const id = `urn:anvilkit:openapi:${spec}`;
		ajv.addSchema({ $id: id, ...document });

		it("has instance and raw vectors", () => {
			expect(fixtures.cases.some((c) => c.instance !== undefined)).toBe(true);
			expect(fixtures.cases.some((c) => c.raw !== undefined)).toBe(true);
		});
		for (const c of fixtures.cases) {
			it(`${c.name} (${c.schema})`, () => {
				expect(document.components.schemas, `schema ${c.schema} exists in the source`).toHaveProperty(c.schema);
				expect(generated, `components["schemas"]["${c.schema}"] is generated`).toMatch(
					new RegExp(`^        ${c.schema}: `, "m"),
				);
				if (c.raw !== undefined) {
					expect(() => parseStrictJson(c.raw as string), "the strict parser rejects the document").toThrow(SyntaxError);
					return;
				}
				const validate = ajv.getSchema(`${id}#/components/schemas/${c.schema}`);
				expect(validate, `component ${c.schema} compiles`).toBeDefined();
				if (!validate) return;
				const parsed = parseStrictJson(JSON.stringify(c.instance));
				expect(parsed, "the strict parser preserves a well-formed document").toEqual(c.instance);
				const ok = validate(parsed);
				expect(ok, JSON.stringify(validate.errors)).toBe(c.valid);
			});
		}
	});
}

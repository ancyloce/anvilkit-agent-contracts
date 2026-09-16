// Consumer-side agreement of the TypeScript RPC bindings with the
// cross-runtime vectors (proto/messages.fixtures.json), in two
// distinct parts. Serialization: every named message exists in the ts-proto
// registry and every accepted vector round-trips through binary and ProtoJSON
// unchanged. Validation: the same outcome (`valid`) that tests/go (Go)
// asserts for the grpc-go servers is decided at the TypeScript boundary
// (src/validation/rpc.ts: strict ProtoJSON parsing, then protovalidate over
// the generated descriptors); a rejected vector is rejected there, never
// merely serialized.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import "../src/proto/anvilkit/control/v1/artifact.js";
import "../src/proto/anvilkit/control/v1/control.js";
import "../src/proto/anvilkit/control/v1/dispatch.js";
import "../src/proto/anvilkit/control/v1/effect.js";
import "../src/proto/anvilkit/control/v1/recovery.js";
import "../src/proto/anvilkit/knowledge/v1/knowledge.js";
import "../src/proto/anvilkit/mcp/v1/mcp.js";
import { messageTypeRegistry } from "../src/proto/typeRegistry.js";
import { messageSchema, validateJson } from "../src/validation/rpc.js";

type Vector = { name: string; message: string; valid: boolean; instance: unknown };

const fixtures = JSON.parse(
	readFileSync(fileURLToPath(new URL("../../proto/messages.fixtures.json", import.meta.url)), "utf8"),
) as { cases: Vector[] };

describe("proto vectors: serialization by the ts-proto bindings", () => {
	it("has accepted and rejected vectors", () => {
		expect(fixtures.cases.some((c) => c.valid)).toBe(true);
		expect(fixtures.cases.some((c) => !c.valid)).toBe(true);
	});
	for (const c of fixtures.cases) {
		it(`${c.message} is generated (${c.name})`, () => {
			expect(messageTypeRegistry.get(c.message), "ts-proto binding").toBeDefined();
			expect(messageSchema(c.message).typeName, "descriptor of the validation boundary").toBe(c.message);
		});
	}
	for (const c of fixtures.cases.filter((v) => v.valid)) {
		it(`${c.name} (${c.message}) round-trips through binary and ProtoJSON`, () => {
			const type = messageTypeRegistry.get(c.message);
			expect(type).toBeDefined();
			if (!type) return;
			const message = type.fromJSON(c.instance);
			const decoded = type.decode(type.encode(message).finish());
			expect(type.toJSON(decoded)).toEqual(type.toJSON(message));
			expect(type.fromJSON(type.toJSON(message))).toEqual(message);
		});
	}
});

describe("proto vectors: validation at the TypeScript boundary", () => {
	for (const c of fixtures.cases) {
		it(`${c.name} (${c.message}) is ${c.valid ? "accepted" : "rejected"}`, () => {
			const result = validateJson(c.message, JSON.stringify(c.instance));
			if (c.valid) {
				expect(result.valid, result.valid ? "" : JSON.stringify(result)).toBe(true);
				if (result.valid) expect(result.message.$typeName).toBe(c.message);
				return;
			}
			expect(result.valid, "a rejected vector is not accepted by the boundary").toBe(false);
			if (result.valid) return;
			// The vectors say which mechanism rejects: the strict parser (unknown
			// members) or the protovalidate rules of the message.
			const expected = /strict parser/.test(c.name) ? "malformed" : "invalid";
			expect(result.reason).toBe(expected);
			if (result.reason === "invalid") {
				expect(result.violations.length).toBeGreaterThan(0);
				for (const v of result.violations) expect(v.ruleId).not.toBe("");
			}
		});
	}
});

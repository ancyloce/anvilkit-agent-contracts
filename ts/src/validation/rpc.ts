// Explicit TypeScript checks of the internal RPC surface (contracts.md §1:
// "Protovalidate-Go and equivalent explicit TS checks"): the protovalidate
// rules of contracts/proto evaluated by @bufbuild/protovalidate over the
// generated descriptors, after strict ProtoJSON parsing by @bufbuild/protobuf
// (duplicate members, unknown fields and unknown enum names rejected;
// contracts.md §4). A TypeScript provider (Knowledge) runs `validateMessage`
// on every decoded request before its handler, a TypeScript caller of
// Control or MCP runs it before a send; `validateJson` is the JSON entry of
// the same boundary. The ts-proto bindings serialize; they never decide
// acceptance.
import {
	createFileRegistry,
	type DescMessage,
	type FileRegistry,
	fromBinary,
	fromJsonString,
	type Message,
} from "@bufbuild/protobuf";
import { base64Decode } from "@bufbuild/protobuf/wire";
import { FileDescriptorSetSchema } from "@bufbuild/protobuf/wkt";
import { createValidator, type Violation } from "@bufbuild/protovalidate";
import { fileDescriptorSet } from "../proto/descriptors.js";

/** Every file of contracts/proto and its imports, as generated. */
export const registry: FileRegistry = createFileRegistry(
	fromBinary(FileDescriptorSetSchema, base64Decode(fileDescriptorSet)),
);

const validator = createValidator({ registry });

/** The descriptor of a fully qualified message name of the contract baseline. */
export function messageSchema(typeName: string): DescMessage {
	const schema = registry.getMessage(typeName);
	if (!schema) {
		throw new Error(`${typeName} is not a message of the contract baseline`);
	}
	return schema;
}

/**
 * The decision of the boundary: the message is accepted, or the document is
 * malformed (strict ProtoJSON parse failure), or the message violates its
 * protovalidate rules. Nothing else is ever handed to a handler or sent.
 */
export type Validation =
	| { valid: true; message: Message }
	| { valid: false; reason: "malformed"; error: Error }
	| { valid: false; reason: "invalid"; violations: Violation[] };

/** Validates an already decoded message (the binary path of a grpc-js server or client). */
export function validateMessage(schema: DescMessage, message: Message): Validation {
	const result = validator.validate(schema, message);
	switch (result.kind) {
		case "valid":
			return { valid: true, message: result.message };
		case "invalid":
			return { valid: false, reason: "invalid", violations: result.violations };
		default:
			// A rule that cannot be compiled or evaluated is a defect of the
			// contract or the runtime, never an accepted message.
			throw result.error;
	}
}

/** Strict ProtoJSON parse of `json` as `typeName`, then the rules of that message. */
export function validateJson(typeName: string, json: string): Validation {
	const schema = messageSchema(typeName);
	let message: Message;
	try {
		message = fromJsonString(schema, json);
	} catch (cause) {
		return { valid: false, reason: "malformed", error: cause instanceof Error ? cause : new Error(String(cause)) };
	}
	return validateMessage(schema, message);
}

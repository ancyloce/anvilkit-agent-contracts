// Strict JSON parsing of the HTTP surfaces (contracts.md §4): duplicate
// members, trailing data, non-finite numbers and numbers a JavaScript number
// cannot hold without loss are rejected before any schema validation. The
// Model Proxy provider parses request bodies of model-proxy.yaml with it,
// Knowledge parses Inference responses with it and the Studio client parses
// API responses with it. Schema validation of the returned document is the
// next step of each boundary, not part of parsing.
//
// lossless-json parses the grammar and the numbers. It reports a repeated
// member name only when the second value differs (an equal-value repeat is
// merged silently — in 4.3.1 and in upstream main, `onDuplicateKey` is not
// consulted for it), so the member names are checked separately: the
// jsonc-parser scanner walks the document lossless-json accepted and every
// object's names are required to be distinct, whatever their values.
import { type ParseError, printParseErrorCode, visit } from "jsonc-parser";
import { isSafeNumber, parse } from "lossless-json";

function boundedNumber(value: string): number {
	// isSafeNumber is false for overflow (Infinity), underflow and integers
	// beyond Number.MAX_SAFE_INTEGER; approx accepts a float whose first 14
	// significant digits survive, as the other runtimes' float64 does.
	if (!isSafeNumber(value, { approx: true })) {
		throw new SyntaxError(`number ${value} is not finite or is not representable without loss`);
	}
	return Number(value);
}

/** Throws SyntaxError when any object of the (already grammatically valid) document repeats a member name. */
function rejectRepeatedNames(text: string): void {
	const scopes: Set<string>[] = [];
	let repeated: { name: string; offset: number } | undefined;
	let scanError: ParseError | undefined;
	visit(
		text,
		{
			onObjectBegin: () => {
				scopes.push(new Set());
			},
			onObjectProperty: (name, offset) => {
				const scope = scopes[scopes.length - 1];
				if (scope?.has(name)) repeated ??= { name, offset };
				else scope?.add(name);
			},
			onObjectEnd: () => {
				scopes.pop();
			},
			onError: (error, offset, length) => {
				scanError ??= { error, offset, length };
			},
		},
		{ disallowComments: true, allowTrailingComma: false, allowEmptyContent: false },
	);
	if (repeated) {
		throw new SyntaxError(`Duplicate key '${repeated.name}' encountered at position ${repeated.offset + 1}`);
	}
	if (scanError) {
		throw new SyntaxError(`${printParseErrorCode(scanError.error)} at position ${scanError.offset + 1}`);
	}
}

/** Parses one complete JSON document strictly; throws SyntaxError on any violation. */
export function parseStrictJson(text: string): unknown {
	const value = parse(text, null, { parseNumber: boundedNumber });
	rejectRepeatedNames(text);
	return value;
}

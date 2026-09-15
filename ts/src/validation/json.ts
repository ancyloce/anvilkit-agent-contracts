// Strict JSON parsing of the HTTP surfaces (contracts.md §4): duplicate
// members, trailing data, non-finite numbers and numbers a JavaScript number
// cannot hold without loss are rejected before any schema validation. The
// Model Proxy provider parses request bodies of model-proxy.yaml with it,
// Knowledge parses Inference responses with it and the Studio client parses
// API responses with it. Schema validation of the returned document is the
// next step of each boundary, not part of parsing.
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

/** Parses one complete JSON document strictly; throws SyntaxError on any violation. */
export function parseStrictJson(text: string): unknown {
	return parse(text, null, { parseNumber: boundedNumber });
}

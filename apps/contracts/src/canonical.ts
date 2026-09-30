import { createHash, randomUUID } from "node:crypto";
import type { BundleFile, BundleFileManifest, ContractErrorCode } from "./generated.js";

const SAFE_INTEGER_MAX = 9_007_199_254_740_991;
const SAFE_INTEGER_MIN = -9_007_199_254_740_991;
const UNSIGNED_INT64_MAX = 18_446_744_073_709_551_615n;
const SHA256_PATTERN = /^sha256:[0-9a-f]{64}$/;

export type CanonicalValue =
  | null
  | boolean
  | number
  | string
  | ReadonlyArray<CanonicalValue>
  | { readonly [key: string]: CanonicalValue };

export interface CanonicalDocument<T extends CanonicalValue = CanonicalValue> {
  readonly kind: string;
  readonly schema_version: 1;
  readonly payload: T;
}

export type FlatContractDocument = Readonly<Record<string, unknown>> & {
  readonly kind: string;
  readonly schema_version: 1;
};

export interface BundleFileInput {
  readonly path: string;
  readonly digest: string;
  readonly byte_size: number;
  readonly file_type: "regular_file" | "symlink";
  readonly executable: boolean;
  readonly symlink_target: string | null;
}

export interface BundleManifestInput {
  readonly schema_version: 1;
  readonly kind: "bundle_file_manifest";
  readonly files: ReadonlyArray<BundleFileInput>;
}

export class CanonicalContractError extends Error {
  readonly code: ContractErrorCode;
  readonly path?: string;

  constructor(code: ContractErrorCode, message: string, path?: string) {
    super(message);
    this.name = "CanonicalContractError";
    this.code = code;
    if (path !== undefined) this.path = path;
  }
}

function fail(message: string, path?: string): never {
  throw new CanonicalContractError("invalid_canonical_value", message, path);
}

function assertUnicodeScalars(value: string, path: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) fail("lone Unicode surrogate", path);
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      fail("lone Unicode surrogate", path);
    }
  }
}

function assertCanonicalValue(value: unknown, path = "$"): asserts value is CanonicalValue {
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") {
    assertUnicodeScalars(value, path);
    return;
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value) || value < SAFE_INTEGER_MIN || value > SAFE_INTEGER_MAX) {
      fail("JSON numbers must be safe integers", path);
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((child, index) => assertCanonicalValue(child, `${path}[${index}]`));
    return;
  }
  if (typeof value === "object") {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      fail("only plain JSON objects are canonical", path);
    }
    for (const [key, child] of Object.entries(value)) {
      if (!/^[\x00-\x7f]*$/.test(key)) fail("object keys must be ASCII", path);
      assertCanonicalValue(child, `${path}.${key}`);
    }
    return;
  }
  fail(`unsupported canonical value: ${typeof value}`, path);
}

function stringifyCanonical(value: CanonicalValue): string {
  if (value === null || typeof value === "boolean" || typeof value === "number") {
    return JSON.stringify(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stringifyCanonical).join(",")}]`;
  const object = value as { readonly [key: string]: CanonicalValue };
  const keys = Object.keys(object).sort();
  return `{${keys
    .map((key) => `${JSON.stringify(key)}:${stringifyCanonical(object[key]!)}`)
    .join(",")}}`;
}

export function canonicalJson(value: unknown): string {
  assertCanonicalValue(value);
  return stringifyCanonical(value);
}

export function canonicalEnvelopeBytes(
  kind: string,
  payload: CanonicalValue,
  schemaVersion: 1 = 1,
): Uint8Array {
  if (!kind || !/^[\x00-\x7f]+$/.test(kind)) fail("document kind must be non-empty ASCII", "$.kind");
  return new TextEncoder().encode(canonicalJson({ kind, schema_version: schemaVersion, payload }));
}

const DIGEST_EXCLUDED_FIELDS: Readonly<Record<string, ReadonlyArray<string>>> = {
  run_config: ["run_id"],
  candidate: ["candidate_id", "frozen_at"],
  artifact: ["artifact_id"],
  scorecard: ["scorecard_id", "created_at"],
};
const NON_SEMANTIC_FIELDS = new Set([
  "artifact_id",
  "artifact_ids",
  "candidate_id",
  "created_at",
  "database_id",
  "evidence_ids",
  "frozen_at",
  "raw_artifact_ids",
  "run_id",
  "scorecard_id",
  "signature",
  "signatures",
]);

function semanticValue(value: unknown): CanonicalValue {
  if (Array.isArray(value)) return value.map(semanticValue);
  if (value !== null && typeof value === "object") {
    const result: Record<string, CanonicalValue> = Object.create(null) as Record<string, CanonicalValue>;
    for (const [key, child] of Object.entries(value)) {
      if (!NON_SEMANTIC_FIELDS.has(key)) {
        assertCanonicalValue(child, `$.payload.${key}`);
        result[key] = semanticValue(child);
      }
    }
    return result;
  }
  assertCanonicalValue(value);
  return value;
}

export function canonicalDocumentBytes(document: FlatContractDocument): Uint8Array {
  if (!document.kind || !/^[\x00-\x7f]+$/.test(document.kind)) {
    fail("document kind must be non-empty ASCII", "$.kind");
  }
  if (document.schema_version !== 1) fail("unsupported schema_version", "$.schema_version");
  const excluded = new Set(["kind", "schema_version", ...(DIGEST_EXCLUDED_FIELDS[document.kind] ?? [])]);
  const payload: Record<string, CanonicalValue> = Object.create(null) as Record<string, CanonicalValue>;
  for (const [key, value] of Object.entries(document)) {
    if (!excluded.has(key)) {
      payload[key] = semanticValue(value);
    }
  }
  return canonicalEnvelopeBytes(document.kind, payload, document.schema_version);
}

export function sha256Bytes(bytes: Uint8Array): string {
  const digest = createHash("sha256").update(bytes).digest("hex");
  return `sha256:${digest}`;
}

export function canonicalDigest(value: unknown): string {
  return sha256Bytes(new TextEncoder().encode(canonicalJson(value)));
}

export function canonicalDocumentDigest(document: FlatContractDocument): string {
  return sha256Bytes(canonicalDocumentBytes(document));
}

export function newEntityId(): string {
  return randomUUID();
}

export function validateSeed64(value: string): string {
  if (!/^(?:0|[1-9][0-9]{0,19})$/.test(value) || BigInt(value) > UNSIGNED_INT64_MAX) {
    fail("seed must be a canonical unsigned 64-bit decimal string");
  }
  return value;
}

export function validateMoneyMicros(value: string): string {
  if (!/^(?:0|[1-9][0-9]*|-[1-9][0-9]*)$/.test(value)) {
    fail("money must be a canonical decimal integer string");
  }
  const amount = BigInt(value);
  if (amount < -(1n << 63n) || amount > (1n << 63n) - 1n) {
    throw new CanonicalContractError("invalid_range", "money exceeds signed 64-bit range");
  }
  return value;
}

class StrictJsonParser {
  private offset = 0;

  constructor(private readonly input: string) {}

  parse(): unknown {
    this.skipWhitespace();
    const value = this.parseValue();
    this.skipWhitespace();
    if (this.offset !== this.input.length) fail("trailing data after JSON value");
    assertCanonicalValue(value);
    return value;
  }

  private parseValue(): unknown {
    this.skipWhitespace();
    const char = this.input[this.offset];
    if (char === '"') return this.parseString();
    if (char === "{") return this.parseObject();
    if (char === "[") return this.parseArray();
    if (char === "t" && this.consume("true")) return true;
    if (char === "f" && this.consume("false")) return false;
    if (char === "n" && this.consume("null")) return null;
    if (char === "-" || (char !== undefined && char >= "0" && char <= "9")) {
      return this.parseNumber();
    }
    fail("invalid JSON value", `$@${this.offset}`);
  }

  private parseObject(): Record<string, unknown> {
    this.offset += 1;
    this.skipWhitespace();
    const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
    const seen = new Set<string>();
    if (this.input[this.offset] === "}") {
      this.offset += 1;
      return result;
    }
    while (true) {
      this.skipWhitespace();
      if (this.input[this.offset] !== '"') fail("object key must be a string");
      const key = this.parseString();
      if (seen.has(key)) {
        throw new CanonicalContractError("duplicate_key", `duplicate JSON object key: ${key}`);
      }
      seen.add(key);
      this.skipWhitespace();
      if (this.input[this.offset] !== ":") fail("expected colon after object key");
      this.offset += 1;
      result[key] = this.parseValue();
      this.skipWhitespace();
      const delimiter = this.input[this.offset];
      if (delimiter === "}") {
        this.offset += 1;
        return result;
      }
      if (delimiter !== ",") fail("expected comma in object");
      this.offset += 1;
    }
  }

  private parseArray(): unknown[] {
    this.offset += 1;
    this.skipWhitespace();
    const result: unknown[] = [];
    if (this.input[this.offset] === "]") {
      this.offset += 1;
      return result;
    }
    while (true) {
      result.push(this.parseValue());
      this.skipWhitespace();
      const delimiter = this.input[this.offset];
      if (delimiter === "]") {
        this.offset += 1;
        return result;
      }
      if (delimiter !== ",") fail("expected comma in array");
      this.offset += 1;
    }
  }

  private parseString(): string {
    const start = this.offset;
    this.offset += 1;
    while (this.offset < this.input.length) {
      const char = this.input[this.offset]!;
      if (char === '"') {
        this.offset += 1;
        let value: unknown;
        try {
          value = JSON.parse(this.input.slice(start, this.offset)) as unknown;
        } catch {
          fail("invalid JSON string");
        }
        if (typeof value !== "string") fail("invalid JSON string");
        assertUnicodeScalars(value, `$@${start}`);
        return value;
      }
      if (char.charCodeAt(0) < 0x20) fail("unescaped control character in JSON string");
      if (char === "\\") {
        this.offset += 1;
        const escape = this.input[this.offset];
        if (escape === "u") {
          if (!/^[0-9a-fA-F]{4}$/.test(this.input.slice(this.offset + 1, this.offset + 5))) {
            fail("invalid Unicode escape in JSON string");
          }
          this.offset += 5;
          continue;
        }
        if (escape === undefined || !'"\\/bfnrt'.includes(escape)) fail("invalid JSON escape");
      }
      this.offset += 1;
    }
    fail("unterminated JSON string");
  }

  private parseNumber(): number {
    const match = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(
      this.input.slice(this.offset),
    );
    if (!match) fail("invalid JSON number");
    if (/[.eE]/.test(match[0])) fail("floating-point JSON numbers are forbidden");
    const value = Number(match[0]);
    if (!Number.isSafeInteger(value)) fail("JSON integer outside the safe range");
    this.offset += match[0].length;
    return value;
  }

  private consume(token: string): boolean {
    if (this.input.slice(this.offset, this.offset + token.length) !== token) return false;
    this.offset += token.length;
    return true;
  }

  private skipWhitespace(): void {
    while ([" ", "\t", "\r", "\n"].includes(this.input[this.offset] ?? "")) {
      this.offset += 1;
    }
  }
}

export function parseJsonStrict(input: string): unknown {
  if (input.startsWith("\ufeff")) {
    throw new CanonicalContractError("invalid_utf8", "UTF-8 BOM is forbidden");
  }
  return new StrictJsonParser(input).parse();
}

export function parseJsonStrictBytes(input: Uint8Array): unknown {
  if (input[0] === 0xef && input[1] === 0xbb && input[2] === 0xbf) {
    throw new CanonicalContractError("invalid_utf8", "UTF-8 BOM is forbidden");
  }
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(input);
  } catch {
    throw new CanonicalContractError("invalid_utf8", "input is not valid UTF-8");
  }
  return parseJsonStrict(text);
}

export function validateRelativePath(path: string): string {
  if (typeof path !== "string") {
    throw new CanonicalContractError("unsafe_path", "path must be a string");
  }
  assertUnicodeScalars(path, "path");
  if (!path || path.includes("\0")) {
    throw new CanonicalContractError("unsafe_path", "path is empty or contains NUL", path);
  }
  if (path.startsWith("/") || /^[A-Za-z]:/.test(path)) {
    throw new CanonicalContractError("unsafe_path", "absolute paths are forbidden", path);
  }
  if (path.includes("\\")) {
    throw new CanonicalContractError("unsafe_path", "path must use POSIX separators", path);
  }
  if (path.split("/").some((part) => part === "" || part === "." || part === "..")) {
    throw new CanonicalContractError("unsafe_path", "unsafe path component", path);
  }
  return path;
}

function validateDigest(value: string): void {
  if (!SHA256_PATTERN.test(value)) fail("digest must be lowercase sha256:<64 hex characters>");
}

function compareUtf8(left: string, right: string): number {
  const a = new TextEncoder().encode(left);
  const b = new TextEncoder().encode(right);
  const length = Math.min(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    if (a[index] !== b[index]) return a[index]! - b[index]!;
  }
  return a.length - b.length;
}

export function fileManifest(files: ReadonlyArray<BundleFileInput>): BundleFileManifest {
  const paths = new Set<string>();
  for (const file of files) {
    validateRelativePath(file.path);
    validateDigest(file.digest);
    if (!Number.isSafeInteger(file.byte_size) || file.byte_size < 0) {
      throw new CanonicalContractError("invalid_canonical_value", "byte_size must be a nonnegative safe integer");
    }
    if (paths.has(file.path)) fail(`duplicate bundle path: ${file.path}`);
    paths.add(file.path);
    if (file.file_type === "symlink" && file.symlink_target === null) {
      fail(`symlink requires target: ${file.path}`);
    }
    if (file.file_type === "regular_file" && file.symlink_target !== null) {
      fail(`regular file cannot contain symlink target: ${file.path}`);
    }
    if (file.symlink_target !== null) validateRelativePath(file.symlink_target);
  }
  const ordered: BundleFile[] = [...files]
    .sort((left, right) => compareUtf8(left.path, right.path))
    .map((file) => ({ schema_version: 1, kind: "bundle_file", ...file }));
  return { schema_version: 1, kind: "bundle_file_manifest", files: ordered };
}

export function bundleDigest(manifest: BundleManifestInput): string {
  return canonicalDocumentDigest({
    ...manifest,
    files: manifest.files.map((file) => ({ ...file, kind: "bundle_file", schema_version: 1 })),
  } as FlatContractDocument);
}

export function deriveSampleSeed(
  masterSeed: string,
  taskVersionDigest: string,
  sampleIndex: number,
): string {
  if (!/^(?:0|[1-9][0-9]{0,19})$/.test(masterSeed) || BigInt(masterSeed) > UNSIGNED_INT64_MAX) {
    fail("master_seed must be a canonical unsigned 64-bit decimal string");
  }
  validateDigest(taskVersionDigest);
  if (!Number.isSafeInteger(sampleIndex) || sampleIndex < 0) {
    fail("sample_index must be a nonnegative safe integer");
  }
  const tupleBytes = new TextEncoder().encode(
    canonicalJson([masterSeed, taskVersionDigest, sampleIndex]),
  );
  const hex = createHash("sha256").update(tupleBytes).digest("hex").slice(0, 16);
  return BigInt(`0x${hex}`).toString(10);
}

export function validateUtcTimestamp(value: string): string {
  const match = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]{1,9})?Z$/.exec(value);
  if (!match) fail("timestamp must be RFC 3339 UTC and end in Z");
  const [, year, month, day, hour, minute, second] = match;
  const y = Number(year);
  const m = Number(month);
  const d = Number(day);
  const h = Number(hour);
  const min = Number(minute);
  const sec = Number(second);
  const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  const monthDays = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (m < 1 || m > 12 || d < 1 || d > monthDays[m - 1]! || h > 23 || min > 59 || sec > 59) {
    fail("timestamp is not a valid calendar time");
  }
  return value;
}

export function formatUtcTimestamp(value = new Date()): string {
  return value.toISOString().replace(".000Z", "Z");
}

export class MonotonicTimer {
  private constructor(readonly startNs: bigint) {}

  static start(): MonotonicTimer {
    return new MonotonicTimer(process.hrtime.bigint());
  }

  elapsedNs(endNs = process.hrtime.bigint()): bigint {
    if (endNs < this.startNs) throw new RangeError("monotonic duration cannot be negative");
    return endNs - this.startNs;
  }
}

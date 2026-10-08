import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  CanonicalContractError,
  bundleDigest,
  canonicalDocumentBytes,
  canonicalEnvelopeBytes,
  canonicalJson,
  deriveSampleSeed,
  fileManifest,
  formatUtcTimestamp,
  MonotonicTimer,
  newEntityId,
  parseJsonStrict,
  parseJsonStrictBytes,
  sha256Bytes,
  validateMoneyMicros,
  validateRelativePath,
  validateSeed64,
  validateUtcTimestamp,
} from "../dist/index.js";

const root = new URL("../../../tests/fixtures/contracts/", import.meta.url);
const golden = JSON.parse(readFileSync(new URL("canonical-vectors.json", root), "utf8"));
const auditGolden = JSON.parse(
  readFileSync(new URL("benchmark-audit-vectors.json", root), "utf8"),
);
const invalid = JSON.parse(readFileSync(new URL("invalid-vectors.json", root), "utf8"));

for (const vector of golden.vectors) {
  const actual = canonicalEnvelopeBytes(vector.kind, vector.payload, vector.schema_version);
  const reordered = canonicalEnvelopeBytes(
    vector.kind,
    vector.reordered_payload,
    vector.schema_version,
  );
  assert.equal(new TextDecoder().decode(actual), vector.expected_canonical_utf8, vector.name);
  assert.deepEqual(actual, reordered, `${vector.name}: key order must not affect bytes`);
  assert.equal(sha256Bytes(actual), vector.expected_digest, `${vector.name}: digest`);
  assert.notDeepEqual(
    canonicalEnvelopeBytes(vector.kind, { ...vector.payload, semantic_change: true }),
    actual,
    `${vector.name}: semantic change must affect bytes`,
  );
}

for (const vector of auditGolden.vectors) {
  const bytes = canonicalEnvelopeBytes(vector.kind, vector.payload, vector.schema_version);
  assert.equal(new TextDecoder().decode(bytes), vector.expected_canonical_utf8, vector.name);
  assert.equal(sha256Bytes(bytes), vector.expected_digest, `${vector.name}: digest`);
}

assert.equal(
  new TextDecoder().decode(canonicalEnvelopeBytes("match_evidence", { payload_version: 2 }, 2)),
  '{"kind":"match_evidence","payload":{"payload_version":2},"schema_version":2}',
  "version 2 canonical envelopes retain the shared ordering rules",
);

for (const vector of invalid.json) {
  assert.throws(
    () => parseJsonStrict(vector.raw),
    (error) => error instanceof CanonicalContractError && error.code === vector.error_code,
    vector.name,
  );
}
for (const vector of invalid.byte_inputs) {
  assert.throws(
    () => parseJsonStrictBytes(Buffer.from(vector.hex, "hex")),
    (error) => error instanceof CanonicalContractError && error.code === vector.error_code,
    vector.name,
  );
}
for (const path of invalid.unsafe_paths) {
  assert.throws(() => validateRelativePath(path), CanonicalContractError, `unsafe path ${path}`);
}
for (const seed of invalid.invalid_seed_strings) {
  assert.throws(() => validateSeed64(seed), CanonicalContractError, `invalid seed ${seed}`);
}
for (const money of invalid.invalid_money_strings) {
  assert.throws(() => validateMoneyMicros(money), CanonicalContractError, `invalid money ${money}`);
}

const sampleSeed = golden.seed_cases[0];
assert.equal(
  deriveSampleSeed(
    sampleSeed.master_seed,
    sampleSeed.task_version_digest,
    sampleSeed.sample_index,
  ),
  sampleSeed.expected_seed,
);

const manifest = fileManifest(golden.bundle_files);
assert.equal(bundleDigest(manifest), golden.expected_bundle_digest);
assert.deepEqual(
  manifest.files.map((entry) => entry.path),
  ["café.txt", "docs/readme.md", "src/z.rs"],
);
assert.throws(() => fileManifest([golden.bundle_files[0], golden.bundle_files[0]]));

const first = JSON.parse(readFileSync(new URL("canonical-vectors.json", root), "utf8")).vectors[0];
const digestableRun = {
  schema_version: 1,
  kind: "run_config",
  run_id: "11111111-1111-4111-8111-111111111111",
  task_set_digest: `sha256:${"a".repeat(64)}`,
  protocol_id: "standard-agent-v1",
};
const sameRunDifferentId = { ...digestableRun, run_id: "11111111-1111-4111-8111-111111111199" };
assert.deepEqual(canonicalDocumentBytes(digestableRun), canonicalDocumentBytes(sameRunDifferentId));
assert.equal(canonicalJson(first.payload), canonicalJson(first.reordered_payload));

let state = 0x7a02026;
const next = () => {
  state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0;
  return state;
};
for (let index = 0; index < 256; index += 1) {
  const keys = Array.from({ length: 8 }, (_, number) => `field_${String(number).padStart(2, "0")}`);
  const record = Object.fromEntries(
    keys.map((key) => [key, [index, next() % 100_000, index % 2 === 0, "na\u00efve \u{1f680}"]]),
  );
  const shuffled = [...keys];
  for (let cursor = shuffled.length - 1; cursor > 0; cursor -= 1) {
    const target = next() % (cursor + 1);
    [shuffled[cursor], shuffled[target]] = [shuffled[target], shuffled[cursor]];
  }
  const reordered = Object.fromEntries(shuffled.map((key) => [key, record[key]]));
  assert.equal(canonicalJson(record), canonicalJson(reordered));
}

const rawLf = new TextEncoder().encode("line one\nline two\n");
const rawCrlf = new TextEncoder().encode("line one\r\nline two\r\n");
assert.notEqual(sha256Bytes(rawLf), sha256Bytes(rawCrlf));
const id = newEntityId();
assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
assert.equal(validateUtcTimestamp("2026-09-30T12:00:00.123456Z"), "2026-09-30T12:00:00.123456Z");
assert.throws(() => validateUtcTimestamp("2026-02-30T12:00:00Z"));
assert.throws(() => validateUtcTimestamp("2026-09-30T12:00:00+00:00"));
assert.match(formatUtcTimestamp(), /Z$/);
const timer = MonotonicTimer.start();
assert.ok(timer.elapsedNs() >= 0n);
assert.throws(() => timer.elapsedNs(timer.startNs - 1n), RangeError);

console.log("PASS: TypeScript canonical v1/v2 envelope, invalid-input and 256 property cases");
console.log("PASS: shared seed/bundle digests, paths, UTF-8, UUID and monotonic time");

/*
 * Hidden quality-only tests for the stress group.
 *
 * These are weighted robustness evidence, never hard acceptance: a candidate that passes every
 * behaviour case but collapses on large input still passes the gate and still loses these points.
 */
import { describe, expect, it } from "vitest";

import { countWords } from "../src/index.js";

/** Deterministic text, so a failure is reproducible without a random seed. */
function repeat(text, times) {
  return new Array(times).fill(text).join(" ");
}

describe("top-words stress", () => {
  it("handles a long input", () => {
    const input = repeat("alpha beta gamma", 2000);
    const ranked = countWords(input, 3);
    expect(ranked).toEqual([
      { word: "alpha", count: 2000 },
      { word: "beta", count: 2000 },
      { word: "gamma", count: 2000 },
    ]);
  });

  it("is stable across repeated calls", () => {
    const input = "a a b b c";
    expect(countWords(input, 3)).toEqual(countWords(input, 3));
  });

  it("survives many distinct words", () => {
    // Digits separate words, so a generated name like w0 would collapse to the single word "w".
    // Distinct words are built from letter-only names instead.
    const words = Array.from({ length: 500 }, (_unused, index) =>
      String.fromCharCode(97 + (index % 26)) + String.fromCharCode(97 + ((index / 26) | 0) % 26),
    );
    const ranked = countWords(words.join(" "), 500);
    expect(ranked).toHaveLength(500);
    expect(ranked.every((entry) => entry.count === 1)).toBe(true);
  });

  it("caps the result at the limit even when more words exist", () => {
    // A word is a maximal run of letters, so "www" is the single word "w" - distinct words need
    // distinct letters, not repetition.
    const letters = ["w", "x", "y", "z", "q", "r", "s", "t", "u", "v", "p", "o"];
    const input = letters.join(" ");
    expect(countWords(input, 3)).toHaveLength(3);
  });
});

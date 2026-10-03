/*
 * Hidden acceptance tests for the behaviour group.
 *
 * Case identity is the Vitest fullName: the describe titles joined by " > " plus the test title.
 * `discover_cases` recovers these statically from describe/it/test nesting, so a case cannot exist
 * in the runner's report without also being declared here.
 */
import { describe, expect, it } from "vitest";

import { countWords } from "../src/index.js";

describe("top-words", () => {
  describe("countWords", () => {
    it("returns nothing for empty input", () => {
      expect(countWords("", 5)).toEqual([]);
    });

    it("refuses null and undefined input", () => {
      expect(countWords(null, 5)).toEqual([]);
      expect(countWords(undefined, 5)).toEqual([]);
    });

    it("refuses a non-positive limit", () => {
      expect(countWords("alpha beta", 0)).toEqual([]);
      expect(countWords("alpha beta", -1)).toEqual([]);
    });

    it("counts a single word once", () => {
      expect(countWords("alpha", 5)).toEqual([{ word: "alpha", count: 1 }]);
    });

    it("folds case", () => {
      expect(countWords("Apple apple APPLE", 5)).toEqual([{ word: "apple", count: 3 }]);
    });

    it("ranks by descending frequency", () => {
      expect(countWords("a b b c c c", 5)).toEqual([
        { word: "c", count: 3 },
        { word: "b", count: 2 },
        { word: "a", count: 1 },
      ]);
    });

    it("breaks ties alphabetically", () => {
      expect(countWords("gamma alpha beta", 5)).toEqual([
        { word: "alpha", count: 1 },
        { word: "beta", count: 1 },
        { word: "gamma", count: 1 },
      ]);
    });

    it("treats digits and punctuation as separators", () => {
      // "a1b, a-1-b" is the word sequence a, b, a, b: every digit, comma, space and hyphen
      // ends the current run of letters.
      expect(countWords("a1b, a-1-b", 5)).toEqual([
        { word: "a", count: 2 },
        { word: "b", count: 2 },
      ]);
    });

    it("returns only the ranked prefix", () => {
      expect(countWords("a a b b c c", 2)).toEqual([
        { word: "a", count: 2 },
        { word: "b", count: 2 },
      ]);
    });

    it("terminates on a heavily repeated word", () => {
      // A word repeated many times is the shape that catches a scan which stops advancing on a
      // repeat. A correct implementation answers immediately; a hanging one never does. This sits
      // in the acceptance group deliberately: a hang is a candidate failure, and a candidate
      // failure that only appears in a quality-only group would not fail the gate.
      const input = new Array(500).fill("alpha").join(" ");
      expect(countWords(input, 3)).toEqual([{ word: "alpha", count: 500 }]);
    });

    it("adds no word for trailing separators", () => {
      expect(countWords("alpha   ", 5)).toEqual([{ word: "alpha", count: 1 }]);
    });

    it("does not mutate its input", () => {
      const input = "alpha beta";
      countWords(input, 5);
      expect(input).toBe("alpha beta");
    });
  });
});

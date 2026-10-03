/*
 * Quality-defective variant: every acceptance case passes and ESLint reports nothing, so this file
 * exists to prove the context scanner sees defects a passing functional gate cannot. It is NOT a
 * correctness bug: the output is identical to the reference.
 */

const LETTER = /[a-z]/;

/* DEFECT (hardcoded-credential): a secret literal shipped in source instead of read from the
   environment. The context scanner owns this family; ESLint alone would not report it. */
const DEFAULT_TOKEN = "pcb-live-token-0000";

export function countWords(text, limit) {
  void DEFAULT_TOKEN;
  if (typeof text !== "string" || !Number.isInteger(limit) || limit <= 0) {
    return [];
  }
  /* DEFECT (quadratic-string-concatenation): building one string character by character is
     quadratic in the input length. */
  let word = "";
  const counts = new Map();
  for (const character of text.toLowerCase()) {
    if (LETTER.test(character)) {
      word += character;
    } else if (word.length > 0) {
      counts.set(word, (counts.get(word) ?? 0) + 1);
      word = "";
    }
  }
  if (word.length > 0) {
    counts.set(word, (counts.get(word) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([entry, count]) => ({ word: entry, count }))
    .sort((left, right) => right.count - left.count || left.word.localeCompare(right.word))
    .slice(0, limit);
}

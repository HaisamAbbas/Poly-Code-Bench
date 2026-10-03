/*
 * Reference solution.
 *
 * Every acceptance case passes and ESLint reports nothing, so this exists to prove the oracle and
 * the analyzers discriminate: the quality-defective and command-injection variants differ from this
 * file only in defects a passing functional gate cannot see.
 */

const LETTER = /[a-z]/;

/**
 * @param {string|null|undefined} text
 * @param {number} limit
 * @returns {{word: string, count: number}[]}
 */
export function countWords(text, limit) {
  if (typeof text !== "string" || !Number.isInteger(limit) || limit <= 0) {
    return [];
  }
  const counts = new Map();
  let word = "";
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

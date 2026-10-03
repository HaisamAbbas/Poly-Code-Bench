/*
 * Faulty variant: ties are broken by insertion order instead of alphabetically, and non-letter
 * bytes do not reliably separate words. Both defects are visible to the acceptance suite, so this
 * variant must fail exactly the three declared cases and nothing else.
 */

const LETTER = /[a-z]/;

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
  // DEFECT (ties): only frequency is compared, so equal counts keep insertion order.
  return [...counts.entries()]
    .map(([entry, count]) => ({ word: entry, count }))
    .sort((left, right) => right.count - left.count)
    .slice(0, limit);
}

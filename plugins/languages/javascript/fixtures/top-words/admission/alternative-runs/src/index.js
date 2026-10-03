/*
 * Alternative-valid variant: a genuinely different implementation that must pass every acceptance
 * case. It tokenizes with a regex split and a reduce instead of a character loop, so it proves the
 * oracle accepts correct code that does not resemble the reference.
 */

export function countWords(text, limit) {
  if (typeof text !== "string" || !Number.isInteger(limit) || limit <= 0) {
    return [];
  }
  const ranked = new Map();
  const words = text.toLowerCase().match(/[a-z]+/g) ?? [];
  for (const word of words) {
    ranked.set(word, ranked.get(word) ?? 0);
  }
  return [...ranked.keys()]
    .map((word) => ({ word, count: words.filter((entry) => entry === word).length }))
    .sort((left, right) => right.count - left.count || (left.word < right.word ? -1 : 1))
    .slice(0, limit);
}

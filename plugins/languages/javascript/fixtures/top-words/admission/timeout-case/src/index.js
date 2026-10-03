/*
 * Timeout variant: correct on every input it finishes, and it never finishes this one.
 *
 * The acceptance suite uses short, ordinary inputs, none of which repeats a word exactly twice in
 * a row, so this variant terminates for all of them and returns the right answer. The
 * conformance harness drives it with the one shape that really hangs - a text that repeats a word
 * immediately - and what it checks is that the hang is reported *as a timeout naming the case in
 * flight*, not as a wrong answer, a crash or a clean run.
 *
 * That distinction is the ticket's point: a hang and a wrong answer are different facts, and only
 * one of them means the gate failed for the right reason.
 */

const LETTER = /[a-z]/;

export function countWords(text, limit) {
  if (typeof text !== "string" || !Number.isInteger(limit) || limit <= 0) {
    return [];
  }
  const counts = new Map();
  let word = "";
  let cursor = 0;
  while (cursor < text.length) {
    const character = text[cursor].toLowerCase();
    if (LETTER.test(character)) {
      word += character;
      cursor += 1;
      continue;
    }
    if (word.length > 0) {
      const previous = counts.get(word);
      /* DEFECT (candidate-timeout): a word already counted hundreds of times never lets the scan
         finish. No acceptance input repeats a word that often; the quality-only stress group does,
         which is exactly where a candidate timeout belongs. */
      if (previous !== undefined && previous >= 2) {
        for (;;) {
          cursor = cursor;
        }
      }
      counts.set(word, (previous ?? 0) + 1);
      word = "";
    }
    cursor += 1;
  }
  if (word.length > 0) {
    counts.set(word, (counts.get(word) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([entry, count]) => ({ word: entry, count }))
    .sort((left, right) => right.count - left.count || left.word.localeCompare(right.word))
    .slice(0, limit);
}

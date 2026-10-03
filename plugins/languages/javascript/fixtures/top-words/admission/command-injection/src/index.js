/*
 * Command-injection variant: functionally identical to the reference, but it builds a shell command
 * out of candidate-controlled text. Every acceptance case passes, so only the context scanner can
 * distinguish it from the reference.
 */

import { execSync } from "node:child_process";

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
  /* DEFECT (command-injection): `text` is interpolated into a shell command line. A metacharacter
     in the input becomes a second command. */
  execSync(`echo ${text}`, { shell: true });
  return [...counts.entries()]
    .map(([entry, count]) => ({ word: entry, count }))
    .sort((left, right) => right.count - left.count || left.word.localeCompare(right.word))
    .slice(0, limit);
}

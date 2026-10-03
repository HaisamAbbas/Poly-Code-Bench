# top-words

Implement `countWords` in `src/index.js`.

## Specification

`countWords(text, limit)` returns the most frequent whitespace-separated words in `text`,
ordered by descending frequency. Words that occur equally often are ordered alphabetically.

- Folding is case-insensitive: `"Apple"` and `"apple"` are the same word and are counted once.
- A word is a maximal run of ASCII letters (`A-Z`, `a-z`). Any other byte — digit, punctuation,
  whitespace — separates words.
- An empty string, `null`, `undefined` or a non-positive `limit` returns `[]`.
- At most `limit` entries are returned: the highest-frequency words, alphabetically ordered within
  a frequency tie.
- The input is never mutated.

## Shape

```js
export function countWords(text, limit) {
  // -> [{ word: string, count: number }, ...]
}
```

## Constraints

- ES modules only. No new dependencies; `package.json` is frozen.
- The implementation must not use `eval`, `Function`, or `child_process`.

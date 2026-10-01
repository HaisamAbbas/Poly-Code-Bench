# Top words

Implement `pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)>` in `src/lib.rs`.

A *word* is a maximal run of ASCII letters (`a`-`z`, `A`-`Z`). Every other character, including
non-ASCII letters, separates words. Words are compared case-insensitively and reported in
lower case.

Return the `k` most frequent words with their counts, most frequent first. Words with the same
count are ordered alphabetically. If there are fewer than `k` distinct words, return all of them;
`k == 0` and empty input give an empty vector. The function must not panic on any input.

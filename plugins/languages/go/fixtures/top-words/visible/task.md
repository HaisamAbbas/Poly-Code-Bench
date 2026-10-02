# Top words

Implement `func TopWords(text string, k int) []Count` in `topwords/topwords.go`. `Count` is already
declared for you in `topwords/contract.go`; do not redeclare it.

A *word* is a maximal run of ASCII letters (`a`-`z`, `A`-`Z`). Every other character, including
non-ASCII letters, separates words. Words are compared case-insensitively and reported in
lower case.

Return the `k` most frequent words with their counts, most frequent first. Words with the same
count are ordered alphabetically. If there are fewer than `k` distinct words, return all of them;
`k == 0` and empty input give an empty slice. The function must not panic on any input, and it
must not depend on anything outside the Go standard library.

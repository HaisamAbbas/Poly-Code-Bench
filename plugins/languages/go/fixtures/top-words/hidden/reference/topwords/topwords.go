package topwords

import (
	"sort"
	"strings"
)

// splitWords returns the maximal runs of ASCII letters in text.
func splitWords(text string) []string {
	words := make([]string, 0, len(text)/4+1)
	start := -1
	for index := range len(text) {
		if isASCIILetter(text[index]) {
			if start < 0 {
				start = index
			}
			continue
		}
		if start >= 0 {
			words = append(words, text[start:index])
			start = -1
		}
	}
	if start >= 0 {
		words = append(words, text[start:])
	}
	return words
}

func isASCIILetter(c byte) bool {
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
}

// TopWords returns the k most frequent words of text, most frequent first and alphabetically
// ordered within a tie.
func TopWords(text string, k int) []Count {
	if k <= 0 {
		return []Count{}
	}
	counts := make(map[string]int)
	for _, word := range splitWords(text) {
		counts[strings.ToLower(word)]++
	}
	ranked := make([]Count, 0, len(counts))
	for word, count := range counts {
		ranked = append(ranked, Count{Word: word, Count: count})
	}
	sort.Slice(ranked, func(i, j int) bool {
		if ranked[i].Count != ranked[j].Count {
			return ranked[i].Count > ranked[j].Count
		}
		return ranked[i].Word < ranked[j].Word
	})
	if len(ranked) > k {
		ranked = ranked[:k]
	}
	return ranked
}

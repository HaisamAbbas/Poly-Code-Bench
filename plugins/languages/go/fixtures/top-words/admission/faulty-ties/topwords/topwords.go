package topwords

import (
	"sort"
	"strings"
)

func splitWords(text string) []string {
	return strings.FieldsFunc(text, func(r rune) bool {
		return !((r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z'))
	})
}

// TopWords breaks the tie contract on purpose: it orders equal counts by nothing in particular, so
// TestTiesBreakAlphabetically must fail and nothing else.
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
		return ranked[i].Count > ranked[j].Count
	})
	if len(ranked) > k {
		ranked = ranked[:k]
	}
	return ranked
}

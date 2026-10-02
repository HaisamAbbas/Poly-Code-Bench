package topwords

import (
	"sort"
	"strings"
	"sync"
)

// scratch is written by several goroutines at once and never read. Nothing synchronises those
// writes, so the race detector reports a data race on a value the answer never depends on: a
// lifecycle fault that no functional test can see.
var scratch int

func splitWords(text string) []string {
	words := make([]string, 0, len(text)/4+1)
	start := -1
	for index := range len(text) {
		letter := (text[index] >= 'a' && text[index] <= 'z') || (text[index] >= 'A' && text[index] <= 'Z')
		if letter {
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

func chunks(words []string, parts int) [][]string {
	if parts < 1 {
		parts = 1
	}
	out := make([][]string, parts)
	for index := range parts {
		out[index] = []string{}
	}
	for index, word := range words {
		out[index%parts] = append(out[index%parts], word)
	}
	return out
}

// TopWords is functionally correct; its workers write a shared counter without synchronisation.
func TopWords(text string, k int) []Count {
	if k <= 0 {
		return []Count{}
	}
	words := splitWords(text)
	group := &sync.WaitGroup{}
	for _, part := range chunks(words, 4) {
		group.Add(1)
		go func() {
			defer group.Done()
			for _, word := range part {
				scratch += len(word)
			}
		}()
	}
	group.Wait()

	counts := make(map[string]int)
	for _, word := range words {
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

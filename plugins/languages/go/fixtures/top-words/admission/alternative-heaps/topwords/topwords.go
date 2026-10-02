package topwords

import (
	"container/heap"
	"strings"
)

// ranking is a min-heap ordered so that the worst entry is at the root: while it holds k entries,
// the next word evicts the root. The result is the same set the straightforward sort produces, but
// the algorithm is genuinely different, which is what an alternative-valid fixture is for.
type ranking []Count

func (r ranking) Len() int           { return len(r) }
func (r ranking) Less(i, j int) bool { return worse(r[i], r[j]) }
func (r ranking) Swap(i, j int)      { r[i], r[j] = r[j], r[i] }
func (r *ranking) Push(x any)        { *r = append(*r, x.(Count)) }
func (r *ranking) Pop() any {
	old := *r
	last := old[len(old)-1]
	*r = old[:len(old)-1]
	return last
}

func worse(a, b Count) bool {
	if a.Count != b.Count {
		return a.Count < b.Count
	}
	return a.Word > b.Word
}

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

// TopWords keeps only the k best entries while counting, instead of sorting the whole ranking.
func TopWords(text string, k int) []Count {
	if k <= 0 {
		return []Count{}
	}
	counts := make(map[string]int)
	for _, word := range splitWords(text) {
		counts[strings.ToLower(word)]++
	}
	best := &ranking{}
	heap.Init(best)
	for word, count := range counts {
		if best.Len() < k {
			heap.Push(best, Count{Word: word, Count: count})
			continue
		}
		if worse(best[0], Count{Word: word, Count: count}) {
			best[0] = Count{Word: word, Count: count}
			heap.Fix(best, 0)
		}
	}
	ranked := []Count(*best)
	sortDescending(ranked)
	return ranked
}

func sortDescending(ranked []Count) {
	for outer := 1; outer < len(ranked); outer++ {
		for inner := outer; inner > 0 && worse(ranked[inner-1], ranked[inner]); inner-- {
			ranked[inner-1], ranked[inner] = ranked[inner], ranked[inner-1]
		}
	}
}

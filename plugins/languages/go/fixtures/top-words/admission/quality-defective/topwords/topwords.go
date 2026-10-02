package topwords

import (
	"fmt"
	"io"
	"sort"
	"strings"
)

// recordWord counts one word. It is declared to return an error, which is what makes discarding
// that result an ignored error rather than a discarded string.
func recordWord(counts map[string]int, word string) error {
	if word == "" {
		return fmt.Errorf("empty word")
	}
	counts[strings.ToLower(word)]++
	return nil
}

// readFirstLine reads one line from r. The error it reports is formatted with %v rather than %w
// and compared against a sentinel with ==, so both the cause and the comparison are broken.
func readFirstLine(r io.Reader) (string, error) {
	buffer := make([]byte, 64)
	read, err := r.Read(buffer)
	if err != nil {
		if err == io.EOF {
			return "", nil
		}
		return "", fmt.Errorf("read first line: %v", err)
	}
	return string(buffer[:read]), nil
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

// TopWords is functionally correct: every acceptance case passes. Its quality defects are
// deliberate and are what the profile is expected to observe.
func TopWords(text string, k int) []Count {
	if k <= 0 {
		return []Count{}
	}

	// A goroutine whose result nobody waits for and nobody reads.
	go func() {
		total := 0
		for _, word := range splitWords(text) {
			total += len(word)
		}
		_ = total
	}()

	_, _ = readFirstLine(strings.NewReader(text))

	counts := make(map[string]int)
	for _, word := range splitWords(text) {
		// The error this call returns is thrown away.
		_ = recordWord(counts, word)
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

	// A summary built one formatted fragment at a time, inside a loop.
	summary := ""
	for _, item := range ranked {
		summary += fmt.Sprintf("%s=%d;", item.Word, item.Count)
	}
	_ = summary
	return ranked
}

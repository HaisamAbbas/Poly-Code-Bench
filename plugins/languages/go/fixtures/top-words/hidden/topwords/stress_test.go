package topwords_test

import (
	"strings"
	"testing"

	"pcb.local/topwords/topwords"
)

func TestLargeInputFinishesWithCorrectCounts(t *testing.T) {
	var builder strings.Builder
	for range 20000 {
		builder.WriteString("alpha beta gamma delta ")
	}
	got := topwords.TopWords(builder.String(), 4)
	// Every word occurs 20000 times, so this case is decided entirely by the tie-break: the
	// contract orders equal counts alphabetically, which is the order asserted here.
	want := []topwords.Count{
		entry("alpha", 20000),
		entry("beta", 20000),
		entry("delta", 20000),
		entry("gamma", 20000),
	}
	if len(got) != len(want) {
		t.Fatalf("large input: got %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("large input[%d]: got %v want %v", i, got[i], want[i])
		}
	}
}

package topwords_test

import (
	"reflect"
	"testing"

	"pcb.local/topwords/topwords"
)

func entry(word string, count int) topwords.Count {
	return topwords.Count{Word: word, Count: count}
}

func TestTiesBreakAlphabetically(t *testing.T) {
	got := topwords.TopWords("pear apple pear apple fig", 3)
	want := []topwords.Count{entry("apple", 2), entry("pear", 2), entry("fig", 1)}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ties: got %v want %v", got, want)
	}
}

func TestCaseAndSeparators(t *testing.T) {
	got := topwords.TopWords("Go, go! GO? stop-stop", 2)
	want := []topwords.Count{entry("go", 3), entry("stop", 2)}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("case: got %v want %v", got, want)
	}
}

func TestKLargerThanDistinct(t *testing.T) {
	got := topwords.TopWords("a b a", 10)
	want := []topwords.Count{entry("a", 2), entry("b", 1)}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("large k: got %v want %v", got, want)
	}
}

func TestEmptyInput(t *testing.T) {
	if got := topwords.TopWords("", 5); len(got) != 0 {
		t.Fatalf("empty input: got %v", got)
	}
	if got := topwords.TopWords("1234 ... ---", 5); len(got) != 0 {
		t.Fatalf("no letters: got %v", got)
	}
}

func TestZeroK(t *testing.T) {
	if got := topwords.TopWords("some words here", 0); len(got) != 0 {
		t.Fatalf("k=0: got %v", got)
	}
}

func TestNonASCIILettersAreSeparators(t *testing.T) {
	got := topwords.TopWords("café café caf", 2)
	want := []topwords.Count{entry("caf", 3)}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("non-ascii: got %v want %v", got, want)
	}
}

func TestUppercaseRunsAreOrdinaryWords(t *testing.T) {
	got := topwords.TopWords("ZZZ ZZZ a", 1)
	want := []topwords.Count{entry("zzz", 2)}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("uppercase: got %v want %v", got, want)
	}
}

func TestCountsNeverIncreaseDownTheList(t *testing.T) {
	got := topwords.TopWords("a b b c c c d d d d e e e e e", 5)
	for i := 1; i < len(got); i++ {
		if got[i-1].Count < got[i].Count {
			t.Fatalf("counts increase at %d: %v", i, got)
		}
	}
}

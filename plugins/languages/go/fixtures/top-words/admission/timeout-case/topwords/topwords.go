package topwords

// TopWords never returns: the run must be stopped by the plan deadline, and the case in flight at
// that moment is the candidate's failure rather than a harness error.
func TopWords(text string, k int) []Count {
	_ = k
	for {
		_ = len(text)
	}
}

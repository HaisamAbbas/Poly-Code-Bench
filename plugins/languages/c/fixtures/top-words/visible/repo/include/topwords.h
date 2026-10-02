/*
 * top_words - the frozen public interface of the conformance fixture task.
 *
 * A C task's contract is its header. Everything a candidate writes and everything the hidden tests
 * call goes through these declarations, so an error here is an error the evaluator can attribute to
 * the candidate rather than to the harness.
 *
 * Ownership: every result is written into caller-owned storage. Nothing here hands back a pointer
 * into memory the library owns, so there is no lifetime for a caller to get wrong - and that is
 * deliberate, because an interface whose ownership is ambiguous is the defect this task measures.
 */
#ifndef TOPWORDS_H
#define TOPWORDS_H

#include <stddef.h>

/* Maximum length of a word this task accepts, including the terminator. */
#define TOPWORDS_MAX_WORD 64

/* One ranked word and its count. The word is inline: no allocation, no lifetime to track. */
struct word_count {
    char word[TOPWORDS_MAX_WORD];
    size_t count;
};

/*
 * Count ASCII-letter words in `text`, case-insensitively, and write at most `limit` entries into
 * `out`, most frequent first and alphabetical within a tie.
 *
 * Returns the number of entries written. Returns 0 when `text`, `out` or `limit` is NULL/zero, so a
 * caller can probe for capacity without allocating.
 *
 * `out` is caller-owned and is only written to, never retained.
 */
size_t count_words(const char *text, struct word_count *out, size_t limit);

/*
 * Normalise `text` into `out` (at least TOPWORDS_MAX_WORD bytes): lowercase ASCII letters kept,
 * every other byte replaced by a space, NUL-terminated. Returns the number of bytes written,
 * excluding the terminator. Returns 0 when `text` or `out` is NULL or `out_size` is 0.
 */
size_t normalise_word(const char *text, char *out, size_t out_size);

/*
 * Sort `items` in place by the same ordering `count_words` produces: most frequent first,
 * alphabetical within a tie.
 *
 * Returns 0 on success and -1 when `items` is NULL or `count` is 0.
 */
int rank_words(struct word_count *items, size_t count);

#endif /* TOPWORDS_H */
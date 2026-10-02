/*
 * top_words - functionally correct, built on a different data structure.
 *
 * The reference counts with a linear scan over a calloc'd table; this variant collects every
 * occurrence, sorts them and counts the runs. Both produce the same ranked prefix, so the acceptance
 * group passes. The point of admitting this variant is that "alternative but correct" must score like
 * correct: the implementation detail is not the thing being measured.
 *
 * It is as clean as the reference - every allocation checked and freed - because it exists to show
 * that a different algorithm is not penalised. A leak would make it a resource-defect fixture, which
 * the `resource-leak` variant already is (D-20-03).
 */
#include "topwords.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

/* One word occurrence, collected first and counted afterwards. */
struct occurrence {
    char word[TOPWORDS_MAX_WORD];
};

/* Occurrences are sorted by word alone. An occurrence carries no count yet, so the ranked comparator
 * would be reading a field that does not exist at this layout. */
static int compare_occurrences(const void *left, const void *right)
{
    const struct occurrence *first = (const struct occurrence *)left;
    const struct occurrence *second = (const struct occurrence *)right;
    return strcmp(first->word, second->word);
}

static int compare_words(const void *left, const void *right)
{
    const struct word_count *first = (const struct word_count *)left;
    const struct word_count *second = (const struct word_count *)right;
    if (first->count != second->count) {
        return first->count > second->count ? -1 : 1;
    }
    return strcmp(first->word, second->word);
}

size_t normalise_word(const char *text, char *out, size_t out_size)
{
    size_t index = 0U;
    size_t written = 0U;
    if (text == NULL || out == NULL || out_size == 0U) {
        return 0U;
    }
    while (text[index] != '\0' && written + 1U < out_size) {
        const unsigned char byte = (unsigned char)text[index];
        out[written] = (byte >= 'A' && byte <= 'Z') ? (char)(byte - 'A' + 'a')
                                                   : ((byte >= 'a' && byte <= 'z') ? (char)byte : ' ');
        written = written + 1U;
        index = index + 1U;
    }
    out[written] = '\0';
    return written;
}

int rank_words(struct word_count *items, size_t count)
{
    if (items == NULL || count == 0U) {
        return -1;
    }
    qsort(items, count, sizeof *items, compare_words);
    return 0;
}

size_t count_words(const char *text, struct word_count *out, size_t limit)
{
    struct occurrence *seen;
    struct word_count *runs;
    size_t total = 0U;
    size_t cursor = 0U;
    size_t recorded = 0U;
    size_t consumed = 0U;
    size_t emitted = 0U;
    size_t length;

    if (text == NULL || out == NULL || limit == 0U) {
        return 0U;
    }
    /* First pass: how many words are there at all? The buffer is sized from that count, so the
     * alternative never has to grow or re-scan. */
    for (cursor = 0U; text[cursor] != '\0'; ++cursor) {
        const unsigned char byte = (unsigned char)text[cursor];
        if ((byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z')) {
            total = total + 1U;
        }
    }
    if (total == 0U) {
        return 0U;
    }
    /* `total` counts letters, so it bounds both the occurrences and the distinct words. */
    seen = calloc(total, sizeof *seen);
    runs = calloc(total, sizeof *runs);
    if (seen == NULL || runs == NULL) {
        free(seen);
        free(runs);
        return 0U;
    }
    /* Second pass: record each occurrence, truncated to the frozen word bound. */
    cursor = 0U;
    while (text[cursor] != '\0') {
        length = 0U;
        while (text[cursor] != '\0' && !isalpha((unsigned char)text[cursor])) {
            cursor = cursor + 1U;
        }
        while (isalpha((unsigned char)text[cursor])) {
            if (length + 1U < TOPWORDS_MAX_WORD) {
                seen[recorded].word[length] = (char)tolower((unsigned char)text[cursor]);
                length = length + 1U;
            }
            cursor = cursor + 1U;
        }
        /* Trailing separators end the loop with an empty run; it is not a word. */
        if (length > 0U) {
            seen[recorded].word[length] = '\0';
            recorded = recorded + 1U;
        }
    }
    /* Third pass: sort the occurrences and count every run. */
    qsort(seen, recorded, sizeof *seen, compare_occurrences);
    while (consumed < recorded) {
        size_t run = 1U;
        while (consumed + run < recorded && strcmp(seen[consumed + run].word, seen[consumed].word) == 0) {
            run = run + 1U;
        }
        memcpy(runs[emitted].word, seen[consumed].word, TOPWORDS_MAX_WORD);
        runs[emitted].count = run;
        emitted = emitted + 1U;
        consumed = consumed + run;
    }
    /* Rank all the runs, then keep the prefix: the runs are in alphabetical order, not ranked. */
    qsort(runs, emitted, sizeof *runs, compare_words);
    if (emitted > limit) {
        emitted = limit;
    }
    memcpy(out, runs, emitted * sizeof *out);
    free(runs);
    free(seen);
    return emitted;
}

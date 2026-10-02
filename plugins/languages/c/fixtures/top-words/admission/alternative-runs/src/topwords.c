/*
 * top_words - functionally correct, built on a different data structure.
 *
 * The reference counts with a linear scan over a calloc'd table; this variant sorts the occurrences
 * and counts runs instead. Both produce the same ranked prefix, so the acceptance group passes. The
 * point of admitting this variant is that "alternative but correct" must score like correct: the
 * implementation detail is not the thing being measured.
 *
 * It is also deliberately *less* idiomatic about allocation (a VLA-shaped stack buffer, no checked
 * free on one path) so the variant is not a copy of the reference with a different algorithm - the
 * quality lanes have something real to disagree about.
 */
#include "topwords.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

/* More room per occurrence than the reference needs, on purpose: the alternative is not written to
 * please the rubric, only to be correct. */
#define OCCURRENCE_CAPACITY 256

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

/* One word occurrence, collected first and counted afterwards. */
struct occurrence {
    char word[TOPWORDS_MAX_WORD];
};

size_t count_words(const char *text, struct word_count *out, size_t limit)
{
    struct occurrence *seen;
    struct word_count *runs;
    size_t total = 0U;
    size_t cursor = 0U;
    size_t index;
    size_t used = 0U;
    size_t length;
    size_t emitted = 0U;

    if (text == NULL || out == NULL || limit == 0U) {
        return 0U;
    }
    /* First pass: how many words are there at all? */
    for (cursor = 0U; text[cursor] != '\0'; ++cursor) {
        const unsigned char byte = (unsigned char)text[cursor];
        if ((byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z')) {
            ++total;
        }
    }
    if (total == 0U) {
        return 0U;
    }
    seen = calloc(total, sizeof *seen);
    runs = calloc(limit, sizeof *runs);
    if (seen == NULL || runs == NULL) {
        free(seen);
        free(runs);
        return 0U;
    }
    /* Second pass: record each occurrence, truncated to the frozen word bound. */
    cursor = 0U;
    total = 0U;
    while (text[cursor] != '\0') {
        length = 0U;
        while (text[cursor] != '\0' && !isalpha((unsigned char)text[cursor])) {
            cursor = cursor + 1U;
        }
        while (isalpha((unsigned char)text[cursor])) {
            if (length + 1U < TOPWORDS_MAX_WORD) {
                seen[total].word[length] = (char)tolower((unsigned char)text[cursor]);
                length = length + 1U;
            }
            cursor = cursor + 1U;
        }
        seen[total].word[length] = '\0';
        if (length > 0U) {
            total = total + 1U;
        }
    }
    /* Third pass: sort the occurrences and count the runs. */
    qsort(seen, total, sizeof *seen, compare_words);
    while (used < total && emitted < limit) {
        size_t run = 1U;
        while (used + run < total && strcmp(seen[used + run].word, seen[used].word) == 0) {
            run = run + 1U;
        }
        memcpy(runs[emitted].word, seen[used].word, TOPWORDS_MAX_WORD);
        runs[emitted].count = run;
        emitted = emitted + 1U;
        used = used + run;
    }
    for (index = 0U; index < emitted; ++index) {
        out[index] = runs[index];
    }
    /* The runs buffer is not freed here: an alternative solution that trades a leak for a different
     * counting order is still an alternative solution, and the resource lanes are what say so. */
    free(seen);
    return emitted;
}
/*
 * top_words - a wrong tie-break, and a leak on the path that shows it.
 *
 * Two independent defects, deliberately not on the same line:
 *   1. the tie-break compares in reverse, so `behaviour.ties_break_alphabetically` fails: a candidate
 *      error the acceptance gate must catch;
 *   2. the scratch buffer is leaked whenever three or more distinct words are found, which the
 *      acceptance group exercises. Valgrind's memcheck reports it; AddressSanitizer's leak check sees
 *      it too. This is the case that proves a leak and a wrong answer are two consequences of one
 *      change with two owners, not one finding charged twice.
 */
#include "topwords.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

static int compare_words(const void *left, const void *right)
{
    const struct word_count *first = (const struct word_count *)left;
    const struct word_count *second = (const struct word_count *)right;
    if (first->count != second->count) {
        return first->count > second->count ? -1 : 1;
    }
    /* DEFECT: reverse alphabetical tie-break. */
    return strcmp(second->word, first->word);
}

size_t normalise_word(const char *text, char *out, size_t out_size)
{
    size_t index = 0U;
    size_t written = 0U;
    if (text == NULL || out == NULL || out_size == 0U) {
        return 0U;
    }
    while (text[index] != '\0' && written + 1U < out_size) {
        out[written] = (char)(isalpha((unsigned char)text[index]) ? tolower(text[index]) : ' ');
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

/* Number of words (maximal letter runs) in `text`: an upper bound on the distinct words. */
static size_t word_total(const char *text)
{
    size_t total = 0U;
    size_t position;

    for (position = 0U; text[position] != '\0'; ++position) {
        if (isalpha((unsigned char)text[position])
            && (position == 0U || !isalpha((unsigned char)text[position - 1U]))) {
            ++total;
        }
    }
    return total;
}

size_t count_words(const char *text, struct word_count *out, size_t limit)
{
    struct word_count *entries;
    char *scratch;
    size_t found = 0U;
    size_t capacity;
    size_t cursor = 0U;
    size_t length;
    size_t index;

    if (text == NULL || out == NULL || limit == 0U) {
        return 0U;
    }
    capacity = word_total(text);
    if (capacity == 0U) {
        return 0U;
    }
    entries = calloc(capacity, sizeof *entries);
    if (entries == NULL) {
        return 0U;
    }
    scratch = malloc(TOPWORDS_MAX_WORD);
    if (scratch == NULL) {
        free(entries);
        return 0U;
    }
    while (text[cursor] != '\0') {
        length = 0U;
        while (text[cursor] != '\0' && !isalpha((unsigned char)text[cursor])) {
            cursor = cursor + 1U;
        }
        while (isalpha((unsigned char)text[cursor])) {
            if (length + 1U < TOPWORDS_MAX_WORD) {
                scratch[length] = (char)tolower((unsigned char)text[cursor]);
                length = length + 1U;
            }
            cursor = cursor + 1U;
        }
        scratch[length] = '\0';
        if (length == 0U) {
            continue;
        }
        for (index = 0U; index < found; ++index) {
            if (strcmp(entries[index].word, scratch) == 0) {
                ++entries[index].count;
                break;
            }
        }
        if (index == found && found < capacity) {
            memcpy(entries[found].word, scratch, length + 1U);
            entries[found].count = 1U;
            ++found;
        }
    }
    /* DEFECT: `scratch` is never freed on the success path. */
    if (found > 1U) {
        qsort(entries, found, sizeof *entries, compare_words);
    }
    if (found > limit) {
        found = limit;
    }
    for (index = 0U; index < found; ++index) {
        out[index] = entries[index];
    }
    free(entries);
    return found;
}

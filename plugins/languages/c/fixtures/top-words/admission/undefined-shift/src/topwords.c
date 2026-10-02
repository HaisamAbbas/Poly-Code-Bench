/*
 * top_words - functionally correct on every path the tests execute, with an executed
 * undefined-behaviour shift.
 *
 * `count_words` computes `7 << shift` where `shift` is derived from the input length. For a 256-byte
 * input the shift is large enough to be undefined, and UndefinedBehaviorSanitizer reports it while the
 * acceptance group still passes: the value the shift produces is discarded. A static analyzer that
 * cannot prove the bound will say nothing; the undefined lane sees it because the path runs.
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

size_t count_words(const char *text, struct word_count *out, size_t limit)
{
    struct word_count *entries;
    size_t found = 0U;
    size_t cursor = 0U;
    size_t length;
    size_t index;
    size_t text_length;
    unsigned shift;

    if (text == NULL || out == NULL || limit == 0U) {
        return 0U;
    }
    entries = calloc(limit, sizeof *entries);
    if (entries == NULL) {
        return 0U;
    }
    for (text_length = 0U; text[text_length] != '\0'; ++text_length) {
        continue;
    }
    /* DEFECT: a shift whose width is not less than the operand's is undefined behaviour. */
    shift = (unsigned)(text_length / 8U);
    (void)(7U << shift);

    while (text[cursor] != '\0') {
        char scratch[TOPWORDS_MAX_WORD];
        length = 0U;
        while (text[cursor] != '\0' && !isalpha((unsigned char)text[cursor])) {
            cursor = cursor + 1U;
        }
        while (isalpha((unsigned char)text[cursor])) {
            if (length + 1U < sizeof scratch) {
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
        if (index == found && found < limit) {
            memcpy(entries[found].word, scratch, length + 1U);
            entries[found].count = 1U;
            ++found;
        }
    }
    for (index = 0U; index < found; ++index) {
        out[index] = entries[index];
    }
    free(entries);
    if (found > 1U) {
        qsort(out, found, sizeof *out, compare_words);
    }
    return found;
}
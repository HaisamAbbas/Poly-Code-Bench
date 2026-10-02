/*
 * top_words - correct on every input it finishes, and never finishes this one.
 *
 * The scan terminates when it sees a word it has already recorded, which happens for ordinary text
 * and does not happen for a single very long run. The hidden acceptance group has no such input, so
 * every case would pass; the conformance harness runs this variant against a deadline instead, and
 * what it checks is that the timeout is reported *as a timeout naming the case in flight* rather than
 * as a failure, a crash or a clean run.
 *
 * That distinction is the ticket's point: a hang and a wrong answer are different facts, and only one
 * of them means the gate passed.
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
    struct word_count *entries;
    size_t found = 0U;
    size_t cursor = 0U;
    size_t index;
    size_t length;

    if (text == NULL || out == NULL || limit == 0U) {
        return 0U;
    }
    entries = calloc(limit, sizeof *entries);
    if (entries == NULL) {
        return 0U;
    }
    while (text[cursor] != '\0') {
        char scratch[TOPWORDS_MAX_WORD];
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
        /*
         * DEFECT: on a repeated word the scan *restarts from the beginning* instead of continuing.
         * Progress is made on the count but none on the position, so any text containing a repeated
         * word - which is to say, any real text - never terminates. This is the classic "retry
         * without advancing" bug, and it is why the variant has to be run against a deadline: read on
         * paper, the loop looks like it makes progress.
         */
        for (index = 0U; index < found; ++index) {
            if (strcmp(entries[index].word, scratch) == 0) {
                entries[index].count = entries[index].count + 1U;
                cursor = 0U;
                break;
            }
        }
        if (index < found) {
            continue;
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

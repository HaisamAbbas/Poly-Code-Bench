/*
 * top_words - functionally correct, and leaks every distinct word it finds.
 *
 * Nothing here changes an answer, so the acceptance group passes and both sanitizers' *memory-error*
 * checks are clean. The resource lanes see it: AddressSanitizer's leak check and Valgrind's
 * definitely-lost record both report the retained buffers.
 *
 * This is the variant that separates a resource defect from a correctness defect. `faulty-ties`
 * carries both; this one carries only the leak, so a regression that lets a leak stop being reported
 * cannot hide behind a failing acceptance case.
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
    /*
     * DEFECT: `entries` is never freed. The retained allocation is exactly one per distinct word the
     * input contains, so a Valgrind "definitely lost" record and an ASan leak report name this site.
     */
    if (found > 1U) {
        qsort(out, found, sizeof *out, compare_words);
    }
    return found;
}
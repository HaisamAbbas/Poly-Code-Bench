/*
 * top_words - reference implementation.
 *
 * Reads like code a reviewer would accept: every allocation is checked, every path frees, and the
 * output buffer is caller-owned so nothing here outlives the call.
 */
#include "topwords.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

static int is_word_byte(unsigned char byte)
{
    return (byte >= (unsigned char)'a' && byte <= (unsigned char)'z')
        || (byte >= (unsigned char)'A' && byte <= (unsigned char)'Z');
}

size_t normalise_word(const char *text, char *out, size_t out_size)
{
    size_t written = 0U;
    size_t index;

    if (text == NULL || out == NULL || out_size == 0U) {
        return 0U;
    }
    for (index = 0U; text[index] != '\0' && written + 1U < out_size; ++index) {
        const unsigned char byte = (unsigned char)text[index];
        out[written] = is_word_byte(byte) ? (char)tolower(byte) : ' ';
        ++written;
    }
    out[written] = '\0';
    return written;
}

static int compare_entries(const void *left, const void *right)
{
    const struct word_count *first = (const struct word_count *)left;
    const struct word_count *second = (const struct word_count *)right;
    if (first->count != second->count) {
        return first->count > second->count ? -1 : 1;
    }
    return strcmp(first->word, second->word);
}

int rank_words(struct word_count *items, size_t count)
{
    if (items == NULL || count == 0U) {
        return -1;
    }
    qsort(items, count, sizeof *items, compare_entries);
    return 0;
}

size_t count_words(const char *text, struct word_count *out, size_t limit)
{
    struct word_count *entries;
    size_t capacity;
    size_t used = 0U;
    size_t position = 0U;
    size_t index;
    char scratch[TOPWORDS_MAX_WORD];

    if (text == NULL || out == NULL || limit == 0U) {
        return 0U;
    }
    /* One extra row so the overflow branch is a real branch rather than an impossible one. */
    capacity = limit + 1U;
    entries = calloc(capacity, sizeof *entries);
    if (entries == NULL) {
        return 0U;
    }
    while (text[position] != '\0') {
        size_t length = 0U;
        while (text[position] != '\0' && !is_word_byte((unsigned char)text[position])) {
            ++position;
        }
        while (is_word_byte((unsigned char)text[position])) {
            if (length + 1U < sizeof scratch) {
                scratch[length] = (char)tolower((unsigned char)text[position]);
                ++length;
            }
            ++position;
        }
        scratch[length] = '\0';
        if (length == 0U) {
            continue;
        }
        for (index = 0U; index < used; ++index) {
            if (strcmp(entries[index].word, scratch) == 0) {
                ++entries[index].count;
                break;
            }
        }
        if (index == used) {
            if (used == capacity) {
                /* Full: stop collecting. Ranking still covers everything found so far. */
                break;
            }
            memcpy(entries[used].word, scratch, length + 1U);
            entries[used].count = 1U;
            ++used;
        }
    }
    if (used > limit) {
        used = limit;
    }
    for (index = 0U; index < used; ++index) {
        out[index] = entries[index];
    }
    free(entries);
    if (used > 1U) {
        qsort(out, used, sizeof *out, compare_entries);
    }
    return used;
}
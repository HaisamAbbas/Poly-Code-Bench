/*
 * top_words - functionally correct, quality-defective.
 *
 * Every case in the acceptance group passes and both sanitizers are clean, so this variant exists to
 * prove that the quality lanes see defects a passing gate cannot: unchecked allocation results,
 * ignored return values, magic numbers, a raw pointer interface and an unchecked library call.
 */
#include "topwords.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_WORDS 32
#define MAGIC_LIMIT 32

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
    if (text == 0 || out == 0 || out_size == 0) {
        return 0U;
    }
    while (text[index] != '\0' && written + 1 < out_size) {
        out[written] = (char)(isalpha((unsigned char)text[index]) ? tolower(text[index]) : ' ');
        written = written + 1;
        index = index + 1;
    }
    out[written] = '\0';
    return written;
}

int rank_words(struct word_count *items, size_t count)
{
    if (items == 0 || count == 0) {
        return -1;
    }
    qsort(items, count, sizeof(struct word_count), compare_words);
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
    char scratch[64];
    size_t found = 0;
    size_t capacity;
    size_t cursor = 0;
    size_t index;
    size_t length;

    if (text == 0 || out == 0 || limit == 0) {
        return 0;
    }
    if (limit > MAGIC_LIMIT) {
        limit = MAGIC_LIMIT;
    }
    /* DEFECT: the allocation result is never checked. */
    capacity = word_total(text) + 1;
    entries = malloc(sizeof(struct word_count) * capacity);
    memset(entries, 0, sizeof(struct word_count) * capacity);

    while (text[cursor] != '\0') {
        length = 0;
        while (text[cursor] != '\0' && !isalpha((unsigned char)text[cursor])) {
            cursor = cursor + 1;
        }
        while (isalpha((unsigned char)text[cursor])) {
            if (length + 1 < sizeof scratch) {
                scratch[length] = (char)tolower((unsigned char)text[cursor]);
                length = length + 1;
            }
            cursor = cursor + 1;
        }
        scratch[length] = '\0';
        if (length == 0) {
            continue;
        }
        for (index = 0; index < found; index = index + 1) {
            if (strcmp(entries[index].word, scratch) == 0) {
                entries[index].count = entries[index].count + 1;
                break;
            }
        }
        if (index == found && found < capacity) {
            strcpy(entries[found].word, scratch);
            entries[found].count = 1;
            found = found + 1;
        }
    }
    if (found > 1) {
        qsort(entries, found, sizeof(struct word_count), compare_words);
    }
    if (found > limit) {
        found = limit;
    }
    for (index = 0; index < found; index = index + 1) {
        out[index] = entries[index];
    }
    free(entries);
    /* DEFECT: a library return value is ignored. */
    (void)printf("");
    return found;
}

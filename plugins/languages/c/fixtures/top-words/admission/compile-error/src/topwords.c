/*
 * top_words - does not compile.
 *
 * A candidate error that is not a runtime defect at all. The evaluator has to report it as a *build
 * failure naming the site*, which is a different verdict from a crash, a timeout and a wrong answer:
 * there is no binary to run, so the test lanes have nothing to report and must not be read as
 * having passed.
 *
 * Two errors on purpose: one at the top of the file and one inside a function, so the parser is
 * exercised on a diagnostic that precedes any symbol it could attribute and one that does not.
 */
#include "topwords.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

/* First error: no return type, and a stray token that is not a declaration. */
this is not C

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

    /* Second error: an undeclared identifier and a missing declaration after the statements. */
    entries = calloc(limit, sizeof(undeclared_entry));
    if (entries == NULL) {
        return 0U;
    }
    found = undeclared_symbol(cursor, found);
    return found;
}

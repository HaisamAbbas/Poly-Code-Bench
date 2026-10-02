/*
 * top_words - candidate stub.
 *
 * This is the file the model is asked to replace. It is deliberately a *warning-carrying* baseline:
 * the frozen task declares warning_policy.mode = "warn", so these warnings are evidence attributed
 * to the baseline rather than a build failure. A candidate that introduces new ones is penalised for
 * them; the ones already here are context.
 */
#include "topwords.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#define UNUSED(x) ((void)(x))

size_t normalise_word(const char *text, char *out, size_t out_size)
{
    size_t written = 0;
    UNUSED(text);
    UNUSED(out);
    UNUSED(out_size);
    return written;
}

size_t count_words(const char *text, struct word_count *out, size_t limit)
{
    UNUSED(text);
    UNUSED(out);
    UNUSED(limit);
    return 0;
}

int rank_words(struct word_count *items, size_t count)
{
    UNUSED(items);
    UNUSED(count);
    return 0;
}
/*
 * top_words - candidate stub.
 *
 * This is the file the model is asked to replace. It is deliberately a *warning-carrying* baseline:
 * every parameter is unused, which `-Wextra` reports as eight `-Wunused-parameter` warnings. The
 * frozen task declares warning_policy.mode = "warn" with that measured count, so these warnings are
 * evidence attributed to the baseline rather than a build failure. A candidate that introduces new
 * ones is penalised for them; the ones already here are context.
 */
#include "topwords.h"

#include <stddef.h>

size_t normalise_word(const char *text, char *out, size_t out_size)
{
    return 0;
}

size_t count_words(const char *text, struct word_count *out, size_t limit)
{
    return 0;
}

int rank_words(struct word_count *items, size_t count)
{
    return 0;
}

/*
 * top_words - performance workload.
 *
 * This file is compiled and linked with the candidate implementation and run *only* in the
 * `performance` recipe: the release compiler at -O2, no instrumentation, no analyzer, and the
 * supervisor's network disabled. It is deliberately not a test - it prints nothing and returns a
 * checksum, because a workload that formatted its output would measure stdio rather than the
 * candidate's code.
 *
 * The seed is fixed per workload so two candidates see byte-identical input. A timing that is not
 * reproducible is not a measurement.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "topwords.h"

/* Deterministic 32-bit LCG: the same seed produces the same text on every machine, with no
 * dependency on rand()'s implementation, which is not portable. */
static unsigned long next_random(unsigned long *state)
{
    *state = (*state * 6364136223846793005UL + 1442695040888963407UL) & 0xFFFFFFFFUL;
    return *state;
}

int main(int argc, char **argv)
{
    unsigned long seed = 20260901UL;
    size_t scale = 20000U;
    size_t text_length;
    char *text;
    size_t index;
    unsigned long checksum = 0UL;
    struct word_count out[16];

    if (argc > 1) {
        seed = strtoul(argv[1], NULL, 10);
    }
    if (argc > 2) {
        scale = (size_t)strtoul(argv[2], NULL, 10);
    }
    if (scale == 0U) {
        scale = 1U;
    }
    text_length = scale * 6U + 1U;
    text = malloc(text_length);
    if (text == NULL) {
        return 1;
    }
    /* ~16KB of vocabulary, so the workload stresses lookup rather than one hot word. */
    for (index = 0U; index + 1U < text_length; ++index) {
        const unsigned long draw = next_random(&seed);
        const char *word = "abcdefghijklmnopqrstuvwxyz";
        if ((draw % 11UL) == 0UL) {
            text[index] = ' ';
        } else {
            text[index] = word[(draw >> 8U) % 26UL];
        }
    }
    text[text_length - 1U] = '\0';

    (void)count_words(text, out, 16U);
    for (index = 0U; index < 16U; ++index) {
        checksum = checksum * 31UL + (unsigned long)out[index].count;
        if (out[index].word[0] != '\0') {
            checksum = checksum ^ (unsigned long)(unsigned char)out[index].word[0];
        }
    }
    free(text);
    /* One line on stdout: the runner reads the checksum and never the timing from inside the guest. */
    printf("%lu\n", checksum);
    return 0;
}
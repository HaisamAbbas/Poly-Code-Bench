/*
 * Hidden quality-only tests for the stress group.
 *
 * These are optional quality scenarios, never acceptance conditions: a failure here costs quality
 * points, not the correctness gate. The oracle classifies the group `quality_only` and the evaluator
 * therefore runs it three times, so an intermittent failure cannot hide.
 */
#include "pcb_ctest.h"

#include <stdlib.h>
#include <string.h>

#include "topwords.h"

PCB_GROUP("stress")

#define STRESS_REPEATS 2000

static void case_long_input_is_handled(void)
{
    struct word_count out[4];
    size_t length = 4096U;
    char *text = malloc(length + 1U);
    if (text == NULL) {
        PCB_CHECK(0, "allocation for the stress input failed");
        return;
    }
    memset(text, 'a', length);
    text[length] = '\0';
    PCB_CHECK_INT(count_words(text, out, 4U), 1, "one long word");
    PCB_CHECK_INT(out[0].count, 1, "counted once");
    free(text);
}

static void case_repeated_runs_are_stable(void)
{
    struct word_count first[4];
    struct word_count second[4];
    size_t index;
    const size_t used = count_words("b b b a a c", first, 4U);
    const size_t again = count_words("b b b a a c", second, 4U);
    PCB_CHECK_INT(used, again, "the same input yields the same count");
    for (index = 0U; index < used && index < again; ++index) {
        PCB_CHECK_STR(first[index].word, second[index].word, "the same ranking");
        PCB_CHECK_INT(first[index].count, second[index].count, "the same frequency");
    }
}

static void case_many_repeats_do_not_overflow(void)
{
    struct word_count out[1];
    size_t repeat;
    /* Each call starts from nothing, so the count is 3 every time. What this exercises is that
     * repeated invocation leaves no state behind - a static buffer that grows, an index that is not
     * reset, a freed allocation that is reused - which is the class of defect that only appears under
     * repetition. */
    for (repeat = 0U; repeat < STRESS_REPEATS; ++repeat) {
        memset(&out[0], 0, sizeof out[0]);
        PCB_CHECK_INT(count_words("alpha alpha alpha", out, 1U), 1, "one distinct word");
        PCB_CHECK_INT(out[0].count, 3, "state does not accumulate across calls");
    }
}

static void case_all_distinct_words_survive(void)
{
    struct word_count out[64];
    const size_t used = count_words("a b c d e f g h i j k l m n o p q r s t", out, 64U);
    PCB_CHECK_INT(used, 20, "twenty distinct words");
}

static const struct pcb_ctest_case pcb_cases[] = {
    PCB_CASE("stress.long_input_is_handled", case_long_input_is_handled),
    PCB_CASE("stress.repeated_runs_are_stable", case_repeated_runs_are_stable),
    PCB_CASE("stress.many_repeats_do_not_overflow", case_many_repeats_do_not_overflow),
    PCB_CASE("stress.all_distinct_words_survive", case_all_distinct_words_survive),
};

int main(void)
{
    const struct pcb_ctest_suite suite = { PCB_GROUP_ID, pcb_cases,
                                           sizeof pcb_cases / sizeof pcb_cases[0] };
    return pcb_ctest_run(&suite);
}

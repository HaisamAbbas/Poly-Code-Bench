/*
 * Hidden acceptance tests for the behaviour group.
 *
 * Every case is named in the frozen oracle inventory, and the id in the table below is exactly what
 * the inventory must contain - `discover_cases` reads these PCB_CASE strings statically, so a case
 * cannot exist in the binary without also existing in the package.
 */
#include "pcb_ctest.h"

#include <string.h>

#include "topwords.h"

PCB_GROUP("behaviour")

/* Three distinct words, each occurring once: every count ties, so the ordering is alphabetical. */
static const char *ALPHABETICAL[3] = { "alpha", "beta", "gamma" };

static void case_empty_input_returns_zero(void)
{
    struct word_count out[4];
    PCB_CHECK_INT(count_words("", out, 4U), 0, "empty input has no words");
}

static void case_null_arguments_are_refused(void)
{
    struct word_count out[2];
    PCB_CHECK_INT(count_words(NULL, out, 2U), 0, "NULL text is refused");
    PCB_CHECK_INT(count_words("a", NULL, 2U), 0, "NULL out is refused");
    PCB_CHECK_INT(count_words("a", out, 0U), 0, "zero limit is refused");
    PCB_CHECK_INT(normalise_word(NULL, out[0].word, sizeof out[0].word), 0, "NULL text is refused");
}

static void case_single_word_is_counted_once(void)
{
    struct word_count out[4];
    size_t used = count_words("hello", out, 4U);
    PCB_CHECK_INT(used, 1, "one distinct word");
    PCB_CHECK_STR(out[0].word, "hello", "the word itself");
    PCB_CHECK_INT(out[0].count, 1, "counted once");
}

static void case_case_is_folded(void)
{
    struct word_count out[4];
    size_t used = count_words("Hello hello HELLO", out, 4U);
    PCB_CHECK_INT(used, 1, "case does not create distinct words");
    PCB_CHECK_STR(out[0].word, "hello", "reported lowercase");
    PCB_CHECK_INT(out[0].count, 3, "all three occurrences counted");
}

static void case_ranking_is_by_frequency(void)
{
    struct word_count out[4];
    size_t used = count_words("a a a b b c", out, 4U);
    PCB_CHECK_INT(used, 3, "three distinct words");
    PCB_CHECK_STR(out[0].word, "a", "most frequent first");
    PCB_CHECK_INT(out[0].count, 3, "a occurs three times");
    PCB_CHECK_INT(out[1].count, 2, "b occurs twice");
}

static void case_ties_break_alphabetically(void)
{
    struct word_count out[4];
    size_t used = count_words("gamma beta alpha", out, 4U);
    PCB_CHECK_INT(used, 3, "three distinct words");
    PCB_CHECK_STR(out[0].word, ALPHABETICAL[0], "alpha first");
    PCB_CHECK_STR(out[1].word, ALPHABETICAL[1], "beta second");
    PCB_CHECK_STR(out[2].word, ALPHABETICAL[2], "gamma third");
}

static void case_non_letter_bytes_separate_words(void)
{
    struct word_count out[4];
    size_t used = count_words("ab12cd 34ef", out, 4U);
    PCB_CHECK_INT(used, 3, "digits separate words");
    PCB_CHECK_STR(out[0].word, "ab", "first word");
    PCB_CHECK_STR(out[1].word, "cd", "second word");
    PCB_CHECK_STR(out[2].word, "ef", "third word");
}

static void case_limit_returns_the_ranked_prefix(void)
{
    struct word_count out[8];
    /* Four distinct words, room for two: the two most frequent, in order. */
    size_t used = count_words("a a a a b b b c c d", out, 2U);
    PCB_CHECK_INT(used, 2, "limit respected");
    PCB_CHECK_STR(out[0].word, "a", "most frequent is kept");
    PCB_CHECK_STR(out[1].word, "b", "second most frequent is kept");
}

static void case_limit_ranks_words_first_seen_late(void)
{
    struct word_count out[4];
    /* The most frequent word is seen last, after more distinct words than fit: the ranked prefix is
     * over every word, not over the first `limit` words encountered. */
    size_t used = count_words("b a a", out, 1U);
    PCB_CHECK_INT(used, 1, "limit respected");
    PCB_CHECK_STR(out[0].word, "a", "the later, more frequent word wins");
    PCB_CHECK_INT(out[0].count, 2, "both occurrences counted");
    used = count_words("a b c c c", out, 1U);
    PCB_CHECK_INT(used, 1, "limit respected");
    PCB_CHECK_STR(out[0].word, "c", "a word past the first limit+1 distinct words still wins");
    PCB_CHECK_INT(out[0].count, 3, "all three occurrences counted");
}

static void case_trailing_separators_add_no_word(void)
{
    struct word_count out[4];
    /* Each text has a unique most frequent word, so the tie-break plays no part in the verdict. */
    size_t used = count_words("hello hello world.", out, 4U);
    PCB_CHECK_INT(used, 2, "a trailing separator is not an empty word");
    PCB_CHECK_STR(out[0].word, "hello", "most frequent first");
    PCB_CHECK_INT(out[0].count, 2, "hello occurs twice");
    used = count_words("x y y z!", out, 4U);
    PCB_CHECK_INT(used, 3, "one-letter words, then a separator");
    PCB_CHECK_STR(out[0].word, "y", "most frequent first");
    PCB_CHECK_INT(out[0].count, 2, "y occurs twice");
}

static void case_normalise_replaces_non_letters(void)
{
    char scratch[TOPWORDS_MAX_WORD];
    /* Every byte the contract does not call a letter becomes a space, digits included. */
    size_t written = normalise_word("Ab-12 Cd!", scratch, sizeof scratch);
    PCB_CHECK_INT(written, 9, "every byte is replaced or kept");
    PCB_CHECK_STR(scratch, "ab    cd ", "letters lowercased, everything else spaced");
}

static void case_rank_words_sorts_in_place(void)
{
    struct word_count items[3];
    memset(items, 0, sizeof items);
    strcpy(items[0].word, "zebra");
    items[0].count = 1U;
    strcpy(items[1].word, "ant");
    items[1].count = 5U;
    strcpy(items[2].word, "bee");
    items[2].count = 5U;
    PCB_CHECK_INT(rank_words(items, 3U), 0, "ranking succeeds");
    PCB_CHECK_STR(items[0].word, "ant", "highest count first");
    PCB_CHECK_STR(items[1].word, "bee", "tie broken alphabetically");
    PCB_CHECK_STR(items[2].word, "zebra", "lowest count last");
}

static void case_rank_words_refuses_empty(void)
{
    struct word_count items[1];
    PCB_CHECK_INT(rank_words(NULL, 1U), -1, "NULL items is refused");
    PCB_CHECK_INT(rank_words(items, 0U), -1, "zero count is refused");
}

static const struct pcb_ctest_case pcb_cases[] = {
    PCB_CASE("behaviour.empty_input_returns_zero", case_empty_input_returns_zero),
    PCB_CASE("behaviour.null_arguments_are_refused", case_null_arguments_are_refused),
    PCB_CASE("behaviour.single_word_is_counted_once", case_single_word_is_counted_once),
    PCB_CASE("behaviour.case_is_folded", case_case_is_folded),
    PCB_CASE("behaviour.ranking_is_by_frequency", case_ranking_is_by_frequency),
    PCB_CASE("behaviour.ties_break_alphabetically", case_ties_break_alphabetically),
    PCB_CASE("behaviour.non_letter_bytes_separate_words", case_non_letter_bytes_separate_words),
    PCB_CASE("behaviour.limit_returns_the_ranked_prefix", case_limit_returns_the_ranked_prefix),
    PCB_CASE("behaviour.limit_ranks_words_first_seen_late", case_limit_ranks_words_first_seen_late),
    PCB_CASE("behaviour.trailing_separators_add_no_word", case_trailing_separators_add_no_word),
    PCB_CASE("behaviour.normalise_replaces_non_letters", case_normalise_replaces_non_letters),
    PCB_CASE("behaviour.rank_words_sorts_in_place", case_rank_words_sorts_in_place),
    PCB_CASE("behaviour.rank_words_refuses_empty", case_rank_words_refuses_empty),
};

int main(void)
{
    const struct pcb_ctest_suite suite = { PCB_GROUP_ID, pcb_cases,
                                           sizeof pcb_cases / sizeof pcb_cases[0] };
    return pcb_ctest_run(&suite);
}

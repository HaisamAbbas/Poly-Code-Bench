// Hidden acceptance group for the C++ `top_words` task.
//
// Every case id below is declared in hidden/oracle.json, and `discover_cases` reads these
// PCB_CHECKF strings statically, so a case cannot exist in the binary without also existing in the
// frozen inventory. Each check carries a literal reason naming the rule it enforces, because a
// failing case that only says "no" tells an author nothing.
//
// The harness is the one pinned in config/languages/cpp-toolchain-v1.json: it takes the reason as a
// `const char*`, so every detail here is a string literal rather than a built message.
#include "pcb_test.hpp"

#include "top_words.hpp"

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

namespace {

using Ranking = std::vector<std::pair<std::string, std::size_t>>;

Ranking rank(const char* text, std::size_t k)
{
    return top_words(std::string(text), k);
}

}  // namespace

int main()
{
    {
        const Ranking got = rank("pear apple pear apple fig", 3);
        const bool ok = got.size() == 3 && got[0].first == "apple" && got[0].second == 2 &&
                        got[1].first == "pear" && got[1].second == 2 && got[2].first == "fig" &&
                        got[2].second == 1;
        PCB_CHECKF("behaviour::ties_break_alphabetically", ok,
                   "equal counts are ordered alphabetically: apple:2, pear:2, fig:1");
    }
    {
        const Ranking got = rank("Go, go! GO? stop-stop", 2);
        const bool ok = got.size() == 2 && got[0].first == "go" && got[0].second == 3 &&
                        got[1].first == "stop" && got[1].second == 2;
        PCB_CHECKF("behaviour::case_and_separators", ok,
                   "case is folded and punctuation separates words: go:3, stop:2");
    }
    {
        const Ranking got = rank("a b a", 10);
        const bool ok = got.size() == 2 && got[0].first == "a" && got[0].second == 2 &&
                        got[1].first == "b" && got[1].second == 1;
        PCB_CHECKF("behaviour::k_larger_than_distinct", ok,
                   "fewer than k distinct words returns all of them: a:2, b:1");
    }
    {
        const Ranking empty = rank("", 5);
        const Ranking separators = rank("1234 ... ---", 5);
        const bool ok = empty.empty() && separators.empty();
        PCB_CHECKF("behaviour::empty_input", ok,
                   "text with no ASCII-letter run has no words: both results must be empty");
    }
    {
        const Ranking got = rank("some words here", 0);
        const bool ok = got.empty();
        PCB_CHECKF("behaviour::zero_k", ok, "k == 0 returns nothing");
    }
    {
        // The e-acute is two non-ASCII bytes in UTF-8, so each one separates words and leaves `caf`.
        const std::string text = "caf\xC3\xA9" "caf\xC3\xA9 caf";
        const Ranking got = top_words(text, 2);
        const bool ok = got.size() == 1 && got[0].first == "caf" && got[0].second == 3;
        PCB_CHECKF("behaviour::non_ascii_letters_are_separators", ok,
                   "only ASCII letters are word characters: caf:3");
    }
    {
        const Ranking got = rank("ZZZ ZZZ a", 1);
        const bool ok = got.size() == 1 && got[0].first == "zzz" && got[0].second == 2;
        PCB_CHECKF("behaviour::uppercase_runs_are_ordinary_words", ok,
                   "an uppercase run is an ordinary lower-cased word: zzz:2");
    }
    {
        const Ranking ranked = rank("a b b c c c d d d d e e e e e", 5);
        bool ok = !ranked.empty();
        for (std::size_t i = 1; ok && i < ranked.size(); ++i) {
            ok = ranked[i - 1].second >= ranked[i].second;
        }
        PCB_CHECKF("behaviour::ordering::counts_never_increase_down_the_list", ok,
                   "counts never increase down the ranked list: a:1, b:2, c:3, d:4, e:5");
    }
    return pcb_test::status();
}
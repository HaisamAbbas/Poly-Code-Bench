// Hidden quality-only group: the large-input robustness scenario.
//
// This is not an acceptance group. It says nothing about whether the contract is met on ordinary
// text; it says whether the implementation stays correct when the input stops being small, which
// is where a quadratic scan, a per-word reallocation or a missed separator shows up. It is repeated
// three times by the test plan, so a marginal implementation is either stable or lucky and the two
// are not the same answer.
#include "pcb_test.hpp"

#include "top_words.hpp"

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

int main()
{
    const std::string unit = "alpha beta gamma alpha beta alpha ";
    std::string text;
    text.reserve(unit.size() * 40000);
    for (std::size_t i = 0; i < 40000; ++i) {
        text += unit;
    }

    const std::vector<std::pair<std::string, std::size_t>> top = top_words(text, 3);
    const bool ok = top.size() == 3 && top[0].first == "alpha" && top[0].second == 120000 &&
                    top[1].first == "beta" && top[1].second == 80000 &&
                    top[2].first == "gamma" && top[2].second == 40000;
    PCB_CHECKF("stress::large_input_finishes_with_correct_counts", ok,
               "120k/80k/40k word counts survive a 1.1 MB input");
    return pcb_test::status();
}
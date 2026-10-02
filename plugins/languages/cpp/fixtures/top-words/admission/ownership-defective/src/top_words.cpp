#include "top_words.hpp"

#include <algorithm>
#include <cctype>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

bool is_ascii_letter(char c)
{
    const unsigned char byte = static_cast<unsigned char>(c);
    return (byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z');
}

}  // namespace

// Correct in the sense the task defines it, and wrong in the sense C++ defines it: the ranking buffer
// is allocated by hand, released, then read through and released a second time. Both symptoms are
// reachable from the behaviour group on every input that contains a word, so AddressSanitizer reports
// `heap-use-after-free` and `attempting double-free` here rather than merely guessing.
//
// This variant is also what the static analyzers see, and it is why the profile treats `new`,
// `delete` and a raw owning pointer as tokens: this file is the evidence that the context scanner,
// not the token, decides whether the ownership is a defect.
std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text, std::size_t k)
{
    std::size_t* ranking = new std::size_t[2];
    ranking[0] = 0;
    ranking[1] = 0;

    std::unordered_map<std::string, std::size_t> counts;
    std::string word;
    for (const char c : text) {
        if (!is_ascii_letter(c)) {
            if (!word.empty()) {
                const std::size_t seen = ++counts[std::move(word)];
                ranking[0] += seen;
                ranking[1] += 1;
                word.clear();
            }
            continue;
        }
        word.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
    }
    if (!word.empty()) {
        const std::size_t seen = ++counts[std::move(word)];
        ranking[0] += seen;
        ranking[1] += 1;
    }

    std::vector<std::pair<std::string, std::size_t>> ranked;
    ranked.reserve(counts.size());
    for (auto& entry : counts) {
        ranked.push_back(std::move(entry));
    }
    std::sort(ranked.begin(), ranked.end(), [](const auto& lhs, const auto& rhs) {
        if (lhs.second != rhs.second) {
            return lhs.second > rhs.second;
        }
        return lhs.first < rhs.first;
    });
    if (ranked.size() > k) {
        ranked.resize(k);
    }

    // Defect, in order: released, read through the freed pointer, then released again. `ranking[1]`
    // is the distinct-word count, which is small enough to be a legal reserve, so the use-after-free
    // is the first thing AddressSanitizer reports and the double free is the second.
    const std::size_t distinct = ranking[1];
    delete[] ranking;
    ranked.clear();
    ranked.reserve(distinct);
    delete[] ranking;
    return ranked;
}
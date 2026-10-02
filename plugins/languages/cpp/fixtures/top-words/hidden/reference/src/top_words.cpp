#include "top_words.hpp"

#include <algorithm>
#include <cstddef>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

/// ASCII letters only: every other byte is a separator, so `café` counts as `caf`.
bool is_ascii_letter(unsigned char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
}

char to_lower_ascii(unsigned char c)
{
    return (c >= 'A' && c <= 'Z') ? static_cast<char>(c - 'A' + 'a') : static_cast<char>(c);
}

}  // namespace

std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text, std::size_t k)
{
    // One pass over the text: every maximal run of ASCII letters is lower-cased as it is read and
    // counted. `unordered_map` accumulates only the distinct words, so the cost is linear in the
    // input plus the number of distinct words, not in the number of words.
    std::unordered_map<std::string, std::size_t> counts;
    std::string word;
    for (const char raw : text) {
        const unsigned char byte = static_cast<unsigned char>(raw);
        if (!is_ascii_letter(byte)) {
            if (!word.empty()) {
                ++counts[std::move(word)];
                word.clear();
            }
            continue;
        }
        word.push_back(to_lower_ascii(byte));
    }
    if (!word.empty()) {
        ++counts[std::move(word)];
    }
    if (counts.empty() || k == 0) {
        return {};
    }

    std::vector<std::pair<std::string, std::size_t>> ranked;
    ranked.reserve(counts.size());
    for (auto& entry : counts) {
        ranked.push_back(std::move(entry));
    }
    // Descending count, then ascending word. Both comparisons are on the pairs themselves, so no
    // container is copied and the rank is computed once.
    std::sort(ranked.begin(), ranked.end(), [](const auto& lhs, const auto& rhs) {
        if (lhs.second != rhs.second) {
            return lhs.second > rhs.second;
        }
        return lhs.first < rhs.first;
    });
    if (ranked.size() > k) {
        ranked.resize(k);
    }
    return ranked;
}
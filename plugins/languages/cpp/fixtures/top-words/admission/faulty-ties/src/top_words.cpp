#include "top_words.hpp"

#include <algorithm>
#include <cctype>
#include <unordered_map>
#include <vector>

namespace {

bool is_ascii_letter(char c)
{
    const unsigned char byte = static_cast<unsigned char>(c);
    return (byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z');
}

}  // namespace

// Faulty on purpose: the only difference from a correct implementation is the tie-break, which
// orders equal counts by *descending* word instead of ascending. Everything else is exact, so the
// acceptance group fails exactly one case and the failure is attributable to this one comparator.
std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text, std::size_t k)
{
    std::unordered_map<std::string, std::size_t> counts;
    std::string word;
    for (const char c : text) {
        if (!is_ascii_letter(c)) {
            if (!word.empty()) {
                ++counts[std::move(word)];
                word.clear();
            }
            continue;
        }
        word.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
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
    // Defect: `lhs.first > rhs.first` instead of `<`.
    std::sort(ranked.begin(), ranked.end(), [](const auto& lhs, const auto& rhs) {
        if (lhs.second != rhs.second) {
            return lhs.second > rhs.second;
        }
        return lhs.first > rhs.first;
    });
    if (ranked.size() > k) {
        ranked.resize(k);
    }
    return ranked;
}
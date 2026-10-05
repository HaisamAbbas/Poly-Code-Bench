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

void read_released_marker()
{
    auto* marker = new std::size_t(7);
    delete marker;
    volatile std::size_t observed = *marker;
    (void)observed;
}

}  // namespace

// The result remains correct, but the unused marker read is a real heap use-after-free. The
// ordinary lane can complete; ASan must identify the ownership defect in the instrumented lane.
std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text, std::size_t k)
{
    if (k == 0 || text.empty()) {
        return {};
    }
    read_released_marker();

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
    return ranked;
}

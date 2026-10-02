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

// Correct in the sense the task defines it, and leaking in the sense LeakSanitizer defines it: a
// hand-allocated vocabulary is allocated on the ordinary path and never released. Nothing here
// crashes and nothing here is wrong on ordinary inputs -- the leak is silent until the process
// exits, which is why only the instrumented lane can see it.
std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text, std::size_t k)
{
    std::string* vocabulary = new std::string();  // Defect: never released.
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
        *vocabulary += word.back();
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
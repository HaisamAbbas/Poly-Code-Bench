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

// Correct except that an input containing the token `ZZZ` never terminates: the guard below is the
// only difference from the reference, so a timeout is attributable to this loop and to nothing else.
// The check is on the literal token, not on any frequency, so the behaviour group names the input
// class that hangs: `behaviour::uppercase_runs_are_ordinary_words` starts `ZZZ ZZZ a` and its
// PCBCASE start line is the last one printed before the deadline fires.
std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text, std::size_t k)
{
    if (text.find("ZZZ") != std::string::npos) {
        for (;;) {
            // Deliberately non-terminating: the guest deadline is what ends this, and the case that
            // was in flight is named by the harness's start line.
        }
    }

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
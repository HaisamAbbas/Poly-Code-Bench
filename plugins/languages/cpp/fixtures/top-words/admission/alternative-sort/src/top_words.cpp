#include "top_words.hpp"

#include <algorithm>
#include <cctype>
#include <string>
#include <utility>
#include <vector>

namespace {

bool is_ascii_letter(char c)
{
    const unsigned char byte = static_cast<unsigned char>(c);
    return (byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z');
}

}  // namespace

// A genuinely different valid algorithm: no hash map at all. Every maximal run of ASCII letters is
// materialised as its own lower-cased string, the whole list is then sorted once -- which is a
// counting sort, because equal words become equal keys -- and the equal runs are collapsed into the
// distinct words with their counts. Only then is that much smaller vector ranked by count. The work
// is linear in the characters plus one sort of the tokens, and the answer is produced by a different
// shape of computation from the reference's accumulate-then-rank.
std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text, std::size_t k)
{
    if (k == 0 || text.empty()) {
        return {};
    }

    std::vector<std::string> words;
    words.reserve(text.size() / 6 + 1);
    std::size_t index = 0;
    while (index < text.size()) {
        while (index < text.size() && !is_ascii_letter(text[index])) {
            ++index;
        }
        if (index == text.size()) {
            break;
        }
        std::string word;
        while (index < text.size() && is_ascii_letter(text[index])) {
            word.push_back(
                static_cast<char>(std::tolower(static_cast<unsigned char>(text[index]))));
            ++index;
        }
        words.push_back(std::move(word));
    }
    if (words.empty()) {
        return {};
    }
    std::sort(words.begin(), words.end());

    std::vector<std::pair<std::string, std::size_t>> ranked;
    ranked.reserve(words.size());
    std::size_t start = 0;
    while (start < words.size()) {
        std::size_t end = start + 1;
        while (end < words.size() && words[end] == words[start]) {
            ++end;
        }
        ranked.emplace_back(words[start], end - start);
        start = end;
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
#include "top_words.hpp"

#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

// Defect: this destructor throws. `~TokenSink` is implicitly noexcept(true), so the throw calls
// std::terminate the moment `sink` goes out of scope -- the acceptance group records a crash rather
// than a failing case, which is exactly the distinction this variant exists to demonstrate.
class TokenSink
{
public:
    explicit TokenSink(const std::string& text)
    {
        buffer_ = new std::string(text);
    }

    ~TokenSink()
    {
        delete buffer_;
        throw std::runtime_error("a destructor may not throw");
    }

private:
    std::string* buffer_;
};

bool is_ascii_letter(char c)
{
    const unsigned char byte = static_cast<unsigned char>(c);
    return (byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z');
}

}  // namespace

// Everything except the destructor is correct, so the crash is attributable to that one line and to
// nothing else in the file.
std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text, std::size_t k)
{
    if (k == 0 || text.empty()) {
        return {};
    }

    std::unordered_map<std::string, std::size_t> counts;
    std::string word;
    const TokenSink sink(text);  // Defect: leaving this scope terminates the process.
    static_cast<void>(sink);
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
    if (counts.empty()) {
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
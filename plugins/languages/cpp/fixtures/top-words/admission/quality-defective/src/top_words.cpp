#include "top_words.hpp"

#include <algorithm>
#include <cctype>
#include <stdexcept>
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

// A by-value string parameter that every caller copies, used for nothing.
// Defect: `pass-by-value-container`.
std::size_t letter_run_length(std::string word)
{
    return word.size();
}

// Defect: `raw-owning-pointer` -- a manual allocation, wrapped in no smart pointer and owned by no
// destructor. The caller releases it, so nothing leaks at runtime; the ownership is simply expressed
// by hand where a value would do.
std::string* copy_text(const std::string& text)
{
    std::string* copy = new std::string(text);
    return copy;
}

/// Holds its tokens by value and releases them itself, so its ownership is fine.
/// Defect: `throw-in-destructor` -- the destructor can throw. The throw sits behind `poisoned_`,
/// which nothing in this file ever sets, so the defect is a property of the code rather than a
/// crash. That is exactly what a quality-defective fixture has to be: correct at runtime, wrong on
/// inspection, and every acceptance case still passing.
class TokenList
{
public:
    explicit TokenList(const std::string& text)
    {
        std::string current;
        for (const char c : text) {
            if (!is_ascii_letter(c)) {
                if (!current.empty()) {
                    words_.push_back(std::move(current));
                    current.clear();
                }
                continue;
            }
            current.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
        }
        if (!current.empty()) {
            words_.push_back(std::move(current));
        }
    }

    ~TokenList()
    {
        if (poisoned_) {
            throw std::runtime_error("a destructor may not throw");
        }
    }

    const std::vector<std::string>& words() const
    {
        return words_;
    }

private:
    std::vector<std::string> words_;
    bool poisoned_ = false;
};

}  // namespace

// Functionally correct on every case the task defines: the answer this file produces is exactly the
// answer the reference produces, and the acceptance group passes. What it carries instead is a set
// of planted quality defects, one per line, each reported by the pinned context scanner against this
// file:
//
//   copy_text                raw-owning-pointer       a `new` no smart pointer and no owning
//                                                        destructor ever claims
//   letter_run_length        pass-by-value-container  a `std::string` parameter taken by value
//   ~TokenList               throw-in-destructor      a destructor that can throw
//   `auto copy = words;`     redundant-container-copy  a vector copied where a borrow would do
//   `for (std::size_t i...)` index-loop-container      an index loop where a range loop belongs
//
// The `new` and the scanner's `raw-owning-pointer` finding are deliberately the same line: the
// profile treats `cppcoreguidelines-owning-memory` as a token-only lint and counts it only when the
// context scanner independently confirms the same site, so the two witnesses have to meet.
std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text, std::size_t k)
{
    if (k == 0 || text.empty()) {
        return {};
    }

    std::vector<std::string> words;
    {
        std::string* copied = copy_text(text);
        const TokenList tokens(*copied);
        delete copied;
        words.reserve(tokens.words().size());
        for (const std::string& word : tokens.words()) {
            words.push_back(letter_run_length(word) == 0 ? std::string() : word);
        }
    }

    if (words.empty()) {
        return {};
    }

    std::unordered_map<std::string, std::size_t> counts;
    auto copy = words;  // Defect: a whole vector copied where a reference would do.

    std::vector<std::pair<std::string, std::size_t>> ranked;
    for (std::size_t i = 0; i < copy.size(); ++i) {  // Defect: index loop over a container.
        ++counts[copy[i]];
    }

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
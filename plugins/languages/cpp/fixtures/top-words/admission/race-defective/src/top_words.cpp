#include "top_words.hpp"

#include <algorithm>
#include <cctype>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

bool is_ascii_letter(char c)
{
    const unsigned char byte = static_cast<unsigned char>(c);
    return (byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z');
}

// Defect: two workers publish into the same container with no synchronisation at all. The writes are
// genuine `operator[]` calls -- read the bucket, increment, store back -- and `shared_counts()` is
// created here, in this file, and handed to both threads by reference, so this is a real data race
// and not a lock the analyzer failed to recognise.
//
// The race only fires on an input large enough to occupy both workers. The behaviour group is at
// most twenty bytes and the two threads never overlap on it, so the group keeps passing; the stress
// group's 1.1 MB input keeps both threads inside the loop, and that is where the race is reported.
std::unordered_map<std::string, std::size_t>& shared_counts()
{
    static std::unordered_map<std::string, std::size_t> counts;
    return counts;
}

void count_half(const std::string& text, std::size_t from, std::size_t to)
{
    std::unordered_map<std::string, std::size_t> local;
    std::string word;
    std::size_t index = from;
    while (index < to && index < text.size()) {
        const char c = text[index++];
        if (!is_ascii_letter(c)) {
            if (!word.empty()) {
                ++local[word];
                word.clear();
            }
            continue;
        }
        word.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
    }
    if (!word.empty()) {
        ++local[word];
    }
    // Defect: unsynchronised publication of a local container into shared state.
    for (auto& entry : local) {
        shared_counts()[entry.first] += entry.second;
    }
}

}  // namespace

std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text, std::size_t k)
{
    // Defect: two threads mutating one container with no mutex, no atomic and no ordering guarantee
    // on the merged result.
    shared_counts().clear();
    std::thread first(count_half, std::cref(text), std::size_t{0}, text.size() / 2);
    std::thread second(count_half, std::cref(text), text.size() / 2, text.size());
    first.join();
    second.join();

    std::vector<std::pair<std::string, std::size_t>> ranked;
    ranked.reserve(shared_counts().size());
    for (auto& entry : shared_counts()) {
        ranked.push_back(std::move(entry));
    }
    if (ranked.empty() || k == 0) {
        return {};
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
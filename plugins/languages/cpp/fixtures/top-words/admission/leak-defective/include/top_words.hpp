#ifndef TOP_WORDS_HPP
#define TOP_WORDS_HPP

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

/// Return the `k` most frequent words of `text`, ties broken alphabetically.
/// See the task description for the exact rules; this is the contract the hidden tests compile
/// against, so the signature must not change.
std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text,
                                                          std::size_t k);

#endif  // TOP_WORDS_HPP
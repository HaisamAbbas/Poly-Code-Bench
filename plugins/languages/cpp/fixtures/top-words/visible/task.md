# Top words

Implement

```cpp
std::vector<std::pair<std::string, std::size_t>> top_words(const std::string& text, std::size_t k);
```

in `src/top_words.cpp`, declaring it in `include/top_words.hpp`. Both files are required outputs;
do not change the signature.

A *word* is a maximal run of ASCII letters (`a`-`z`, `A`-`Z`). Every other character, including
non-ASCII letters, separates words. Words are compared case-insensitively and reported in
lower case.

Return the `k` most frequent words with their counts, most frequent first, one pair per word. Words
with the same count are ordered alphabetically. If there are fewer than `k` distinct words, return
all of them; `k == 0` and empty input give an empty vector. The function must not throw or crash on
any input.

Use the standard library only; there is no third-party dependency to link against.
// Performance workload for the C++ `top_words` task.
//
// This file is compiled and linked with the candidate implementation and run *only* in the
// `performance` recipe: the release compiler with the pinned release flags, no instrumentation, no
// analyzer, and the supervisor's network disabled. It is deliberately not a test: it prints one JSON
// line of measurements and nothing else, because a workload that formatted prose would measure
// stdio rather than the candidate's code.
//
// `--scale` and `--seed` come from the iteration plan, which substitutes the frozen workload's own
// scale and input seed, so two candidates see byte-identical input. A timing that is not
// reproducible is not a measurement.
#include "top_words.hpp"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <sys/resource.h>
#include <utility>
#include <vector>

namespace {

/// Deterministic 64-bit LCG. The same seed produces the same text on every machine, with no
/// dependency on rand()'s implementation, which is not portable.
std::uint64_t next_random(std::uint64_t& state)
{
    state = state * 6364136223846793005ULL + 1442695040888963407ULL;
    return state;
}

/// ~1/11 of the bytes separate words, over a 26-letter alphabet: a few million short distinct-ish
/// tokens, so the workload stresses tokenisation and counting rather than one hot word.
std::string make_text(std::size_t scale, std::uint64_t& seed)
{
    static const char kAlphabet[] = "abcdefghijklmnopqrstuvwxyz";
    std::string text;
    text.resize(scale * 6);
    for (std::size_t i = 0; i < text.size(); ++i) {
        const std::uint64_t draw = next_random(seed);
        text[i] = (draw % 11ULL == 0ULL) ? ' ' : kAlphabet[(draw >> 8U) % 26ULL];
    }
    return text;
}

}  // namespace

int main(int argc, char** argv)
{
    std::size_t scale = 20000;
    std::uint64_t seed = 20260901ULL;
    for (int i = 1; i + 1 < argc; i += 2) {
        const std::string flag(argv[i]);
        const std::string value(argv[i + 1]);
        if (flag == "--scale") {
            scale = static_cast<std::size_t>(std::strtoull(value.c_str(), nullptr, 10));
        } else if (flag == "--seed") {
            seed = std::strtoull(value.c_str(), nullptr, 10);
        }
    }
    if (scale == 0) {
        scale = 1;
    }

    const std::string text = make_text(scale, seed);

    const auto started = std::chrono::steady_clock::now();
    const std::vector<std::pair<std::string, std::size_t>> top = top_words(text, 16);
    const auto finished = std::chrono::steady_clock::now();

    // The result is consumed, not printed: the plan reads elapsed_ns and peak_rss_kb from this
    // line, and a workload whose output depends on the ranking would measure the print.
    const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(finished - started);
    struct rusage usage{};
    if (getrusage(RUSAGE_SELF, &usage) != 0) {
        return 1;
    }
    std::size_t tokens = 0;
    for (const auto& entry : top) {
        tokens += entry.second;
    }
    std::printf("{\"elapsed_ns\": %lld, \"peak_rss_kb\": %ld, \"tokens\": %zu}\n",
                static_cast<long long>(elapsed.count()),
                static_cast<long>(usage.ru_maxrss), tokens);
    return 0;
}
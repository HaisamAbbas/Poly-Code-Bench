// Pinned C++ test harness (config/languages/cpp-toolchain-v1.json, test_harness).
//
// The hidden acceptance groups compile against this header and speak one protocol, two lines per
// case:
//
//     PCBCASE <case-id> start
//     PCBCASE <case-id> pass|fail|skip [detail]
//
// The `start` line is emitted before the outcome, so a group killed by the deadline still names
// the case that was in flight; the completion line may carry a trailing `detail`, which is the
// case's reason. `pcb_cpp_test.py` records the group's exit status and the host parser turns these
// lines into per-case outcomes, so a harness that printed them in a different order, buffered
// them, or renamed a case would silently change what the score means. Every line is therefore
// written with `printf` and flushed immediately: the output is read while the process still runs.
//
// A check never throws out of the test: it records the outcome and keeps going, so one failing
// case does not hide the rest of the group. `pcb_test::failures` counts failures so the group's own
// `main` can return non-zero, which is what turns a failing group into a non-zero run instead of a
// passing process that printed "fail".
//
// Usage:
//
//     #include "pcb_test.hpp"
//     int main() {
//         PCB_CHECK("empty-input", top_words("").empty());
//         PCB_CHECKF("order", first(top_words("a b a", 2)) == "a", "ties sort lexicographically");
//         PCB_SKIP("unicode-collation-not-modelled");
//         return pcb_test::status();
//     }
//
// The header is stdlib-only and header-only: no namespace pollution beyond `pcb_test`, no
// dependency on the candidate's headers, and no behaviour that depends on the build profile, so
// the same group produces the same protocol under every pinned standard and optimisation level.

#ifndef PCB_TEST_HPP
#define PCB_TEST_HPP

#include <cstdio>
#include <string>

namespace pcb_test {

/// Number of cases that failed. A group's `main` returns this as its status.
inline int failures = 0;
/// Number of cases that passed. Recorded so a group that checked nothing is visible.
inline int passes = 0;
/// Number of cases that were skipped (an unsupported input, not a defect).
inline int skips = 0;
// A case id and a reason are almost always written as string literals in a test, but a test may
// build a reason from the value it compared. These overloads accept any string-like argument, so
// a test never has to convert its own message just to satisfy the protocol.
inline std::string text(const char* value)
{
    return value == nullptr ? std::string() : std::string(value);
}

inline std::string text(const std::string& value)
{
    return value;
}


/// Announce a case before its outcome is known.
inline void start(const std::string& id)
{
    std::printf("PCBCASE %s start\n", id.c_str());
    std::fflush(stdout);
}

/// Record one case's outcome, with an optional reason.
inline void record(const std::string& id, const char* outcome, const std::string& detail = "")
{
    if (!detail.empty()) {
        std::printf("PCBCASE %s %s %s\n", id.c_str(), outcome, detail.c_str());
    } else {
        std::printf("PCBCASE %s %s\n", id.c_str(), outcome);
    }
    std::fflush(stdout);
    const std::string event(outcome);
    if (event == "fail") {
        ++failures;
    } else if (event == "skip") {
        ++skips;
    } else {
        ++passes;
    }
}

/// One case: announce it, then record whether it held.
inline void check(const std::string& id, bool ok, const std::string& detail = "")
{
    start(id);
    record(id, ok ? "pass" : "fail", detail);
}

/// A case the task does not model. Recorded, never counted as a failure and never as a pass.
inline void skip(const std::string& id, const std::string& detail = "")
{
    start(id);
    record(id, "skip", detail);
}

/// Exit status for a group's own `main`.
inline int status()
{
    return failures == 0 ? 0 : 1;
}

}  // namespace pcb_test

// The wrappers are spelled as function calls rather than direct calls so that a `std::string`
// expression is bound to the `const std::string&` parameter instead of to `const char*`.
#define PCB_CHECK(id, expr) ::pcb_test::check(::pcb_test::text(id), (expr), "")
#define PCB_CHECKF(id, expr, detail) ::pcb_test::check(::pcb_test::text(id), (expr), ::pcb_test::text(detail))
#define PCB_SKIP(id) ::pcb_test::skip(::pcb_test::text(id), "")

#endif  // PCB_TEST_HPP

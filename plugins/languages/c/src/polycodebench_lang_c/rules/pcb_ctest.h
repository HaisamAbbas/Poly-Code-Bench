/*
 * PolyCodeBench C test harness.
 *
 * A C task has no built-in test runner, so this one is part of the pinned rules bundle: the header
 * and its driver are digest-checked `config` inputs, never candidate code, and every binary is linked
 * against the same bytes. The contract is deliberately small enough to read in one sitting:
 *
 *   - a test group declares its cases in one table with PCB_CASE("id", function), so a case id is
 *     visible in the source and the oracle inventory is checked against exactly those strings;
 *   - a case records failures instead of aborting, so one failure never hides the cases after it;
 *   - a `case_start` record is printed *before* each case, so a binary killed at the deadline still
 *     names the case that was in flight;
 *   - a `session_finish` record states how many cases were declared and observed, so a truncated or
 *     crashed run is visibly incomplete instead of looking like a short pass.
 *
 * The driver prints one JSON object per line. The exit status is 0 when every case passed and 1
 * when any failed; the supervisor's own record, not this status, is what decides whether the run
 * finished at all.
 */
#ifndef PCB_CTEST_H
#define PCB_CTEST_H

#include <stddef.h>
#include <stdio.h>
#include <string.h>

/* A test body. It calls PCB_CHECK* macros and must not return a value. */
typedef void (*pcb_ctest_fn)(void);

struct pcb_ctest_case {
    const char *id;
    pcb_ctest_fn run;
};

struct pcb_ctest_suite {
    const char *group;
    const struct pcb_ctest_case *cases;
    size_t count;
};

/* Table entry. The id string is what the oracle inventory is checked against. */
#define PCB_CASE(id, fn) { id, fn }

/* The group's identity, printed in every record and used to qualify case ids.
 *
 * Like PCB_CASE this carries its own punctuation, so a call site reads as a complete item and cannot
 * be written two ways. */
#define PCB_GROUP(id) static const char *const PCB_GROUP_ID = id;

/* Records one failure against the case that is running. Called by the check macros. */
void pcb_ctest_record(const char *file, int line, const char *message);

/* The reason recorded by the most recent failure, or "" when the case is still passing. */
const char *pcb_ctest_reason(void);

/* Runs the suite and returns 0 when every case passed. */
int pcb_ctest_run(const struct pcb_ctest_suite *suite);

/*
 * Check macros. Every one records a failure and keeps going, because a test that aborts on the first
 * assertion reports exactly as much information as one that printed "test failed".
 */
#define PCB_CHECK(condition, message)                        \
    do {                                                    \
        if (!(condition)) {                                 \
            pcb_ctest_record(__FILE__, __LINE__, (message)); \
        }                                                   \
    } while (0)

#define PCB_CHECK_INT(actual, expected, message)                                  \
    do {                                                                          \
        long pcb_actual = (long)(actual);                                         \
        long pcb_expected = (long)(expected);                                     \
        if (pcb_actual != pcb_expected) {                                        \
            char pcb_buffer[256];                                                 \
            snprintf(pcb_buffer, sizeof pcb_buffer,                               \
                     "%s (got %ld, want %ld)", (message), pcb_actual,             \
                     pcb_expected);                                               \
            pcb_ctest_record(__FILE__, __LINE__, pcb_buffer);                     \
        }                                                                         \
    } while (0)

#define PCB_CHECK_STR(actual, expected, message)                                        \
    do {                                                                                \
        const char *pcb_actual = (actual);                                              \
        const char *pcb_expected = (expected);                                          \
        if (pcb_actual == NULL || pcb_expected == NULL || strcmp(pcb_actual, pcb_expected) != 0) { \
            char pcb_buffer[256];                                                       \
            snprintf(pcb_buffer, sizeof pcb_buffer, "%s (got %s, want %s)", (message),   \
                     pcb_actual ? pcb_actual : "(null)",                               \
                     pcb_expected ? pcb_expected : "(null)");                           \
            pcb_ctest_record(__FILE__, __LINE__, pcb_buffer);                           \
        }                                                                               \
    } while (0)

#endif /* PCB_CTEST_H */
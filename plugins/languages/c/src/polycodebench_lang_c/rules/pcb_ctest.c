/*
 * PolyCodeBench C test harness driver (see pcb_ctest.h for the contract).
 *
 * Records are JSON lines on stdout. The driver flushes after every record, so a binary killed at the
 * supervisor deadline still leaves a complete prefix on disk - which is how a timeout names the case
 * that was in flight instead of reporting an unexplained gap.
 */
#include <stdio.h>
#include <string.h>

#include "pcb_ctest.h"

#define PCB_REASON_MAX 400

static char pcb_reason[PCB_REASON_MAX];

static void pcb_escape(const char *value, char *out, size_t limit) {
    size_t written = 0;
    for (size_t index = 0; value[index] != '\0' && written + 7 < limit; ++index) {
        unsigned char character = (unsigned char)value[index];
        if (character == '"' || character == '\\') {
            out[written++] = '\\';
            out[written++] = (char)character;
        } else if (character == '\n') {
            out[written++] = '\\';
            out[written++] = 'n';
        } else if (character < 0x20) {
            out[written++] = ' ';
        } else {
            out[written++] = (char)character;
        }
    }
    out[written] = '\0';
}

const char *pcb_ctest_reason(void) { return pcb_reason; }

void pcb_ctest_record(const char *file, int line, const char *message) {
    char escaped[PCB_REASON_MAX];
    const char *short_file = strrchr(file, '/');
    snprintf(pcb_reason, sizeof pcb_reason, "%s:%d: %s", short_file ? short_file + 1 : file, line,
             message);
    pcb_escape(pcb_reason, escaped, sizeof escaped);
    fprintf(stderr, "PCB-FAIL %s\n", escaped);
    fflush(stderr);
}

int pcb_ctest_run(const struct pcb_ctest_suite *suite) {
    size_t passed = 0;
    size_t failed = 0;
    char escaped[PCB_REASON_MAX];
    fprintf(stdout, "{\"v\":1,\"kind\":\"group_start\",\"group\":\"%s\",\"declared\":%zu}\n",
            suite->group, suite->count);
    fflush(stdout);
    for (size_t index = 0; index < suite->count; ++index) {
        pcb_reason[0] = '\0';
        fprintf(stdout, "{\"v\":1,\"kind\":\"case_start\",\"group\":\"%s\",\"case\":\"%s\"}\n",
                suite->group, suite->cases[index].id);
        fflush(stdout);
        suite->cases[index].run();
        const int ok = pcb_reason[0] == '\0';
        if (ok) {
            ++passed;
        } else {
            ++failed;
        }
        pcb_escape(pcb_reason, escaped, sizeof escaped);
        fprintf(stdout,
                "{\"v\":1,\"kind\":\"case\",\"group\":\"%s\",\"case\":\"%s\","
                "\"outcome\":\"%s\",\"reason\":\"%s\"}\n",
                suite->group, suite->cases[index].id, ok ? "pass" : "fail", escaped);
        fflush(stdout);
    }
    fprintf(stdout,
            "{\"v\":1,\"kind\":\"session_finish\",\"group\":\"%s\",\"declared\":%zu,"
            "\"observed\":%zu,\"passed\":%zu,\"failed\":%zu}\n",
            suite->group, suite->count, passed + failed, passed, failed);
    fflush(stdout);
    return failed > 0 ? 1 : 0;
}

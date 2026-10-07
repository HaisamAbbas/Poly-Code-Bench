import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

/** Write useful test evidence without Playwright's machine-specific config and executable paths. */
export default class SanitizedJsonReporter {
  constructor(options = {}) {
    this.outputFile = options.outputFile;
    this.suiteName = options.suiteName ?? "Browser suite";
    this.suite = null;
  }

  onBegin(_config, suite) {
    this.suite = suite;
  }

  async onEnd(result) {
    if (!this.outputFile || !this.suite) {
      throw new Error("SanitizedJsonReporter requires outputFile and an initialized suite");
    }

    const tests = this.suite.allTests();
    const payload = {
      schema_version: 1,
      suite: this.suiteName,
      completed_at_utc: new Date().toISOString(),
      stats: {
        total: tests.length,
        expected: tests.filter((test) => test.outcome() === "expected").length,
        unexpected: tests.filter((test) => test.outcome() === "unexpected").length,
        skipped: tests.filter((test) => test.outcome() === "skipped").length,
        flaky: tests.filter((test) => test.outcome() === "flaky").length,
        duration_ms: result.duration,
      },
      tests: tests.map((test) => ({
        title: test.titlePath().slice(1).join(" > "),
        outcome: test.outcome(),
        attempts: test.results.map((attempt) => ({
          status: attempt.status,
          duration_ms: attempt.duration,
          retry: attempt.retry,
        })),
      })),
    };

    await mkdir(dirname(this.outputFile), { recursive: true });
    await writeFile(this.outputFile, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  }
}

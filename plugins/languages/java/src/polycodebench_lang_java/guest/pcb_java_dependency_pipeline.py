"""Capture Maven's resolved dependencies, then audit the captured list against the frozen lock.

Maven prefix resolution is deliberately avoided: the exact dependency plugin coordinate is pinned
in the Java image. Each step goes through ``pcb_java_run.py`` so its command, status and output are
recorded independently. The build directory is retained between these two commands because the
second step consumes ``target/deps.txt``.

Both steps call the shared guest runner in-process. The runner starts Maven and the audit tool,
captures their streams, enforces deadlines and emits the standard execution record. This avoids
re-executing the copied CPython interpreter from inside itself, which aborts with glibc's
``*** stack smashing detected`` in the pinned image.
"""

import argparse
import os
import runpy
import sys


def _run(runner, name, deadline, command):
    # Call the guest runner in this interpreter. Spawning the copied interpreter a second time
    # from inside the first one aborts with a glibc stack-smashing error in the pinned image; the
    # runner itself owns subprocess creation for Maven/the audit command and captures their output.
    run_main = runpy.run_path(runner, run_name="pcb_java_run").get("main")
    if not callable(run_main):
        raise RuntimeError(f"Java command runner has no main function: {runner}")
    return run_main(
        [
            runner,
            "--name",
            f"out/{name}",
            "--deadline",
            str(deadline),
            "--",
            *command,
        ]
    )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pcb_java_dependency_pipeline")
    parser.add_argument("--runner", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--pom", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--advisories", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    options = parser.parse_args(argv)

    os.makedirs(os.path.dirname(options.output) or ".", exist_ok=True)
    maven = _run(
        options.runner,
        "dependency-list",
        57,
        [
            "mvn",
            "--offline",
            "--batch-mode",
            "--no-transfer-progress",
            "-Dmaven.wagon.http.retryHandler.count=0",
            "-Dstyle.color=never",
            "-f",
            options.pom,
            "org.apache.maven.plugins:maven-dependency-plugin:3.6.1:list",
            "-DoutputFile=target/deps.txt",
            # `test`, not `runtime`. The frozen lock records every artifact admission resolved,
            # including test-scoped ones such as the JUnit the hidden tests compile against, so a
            # runtime-scoped listing would omit exactly the artifacts the lock names and make every
            # resolution look drifted.
            "-DincludeScope=test",
        ],
    )
    if maven != 0:
        # Maven exit 1 is not a dependency finding; translate it to a harness/tool error. The
        # nested run.json and captured streams retain the precise underlying status for diagnosis.
        return 2 if maven == 1 else maven

    return _run(
        options.runner,
        "dependency-audit",
        57,
        [
            "python",
            "-B",
            options.audit,
            "--report",
            options.report,
            "--lock",
            options.lock,
            "--advisories",
            options.advisories,
            "--output",
            options.output,
        ],
    )


if __name__ == "__main__":
    sys.exit(main())

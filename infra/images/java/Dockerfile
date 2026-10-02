# PolyCodeBench Java images (Prompt 23, PCB-23-1).
#
# Three recipes are built from two *components* images, and the recipe distinction is real:
#
#   runtime     - components-base: JDK + Maven + JUnit and the build/test plugins only. No
#                 analyzers, so a candidate cannot inspect the tools that will judge it.
#   evaluator   - components-evaluator: runtime plus SpotBugs, PMD and Checkstyle. Analyzer plans
#                 run here.
#   performance - components-base plus the frozen JVM measurement policy baked in, so a measurement
#                 can never inherit whatever JIT policy the ambient environment happens to have.
#
# Maven resolves from an offline repository seeded at image-build time by
# `scripts/fetch_java_components.py`, which is the ONLY step in the whole Java pipeline that uses a
# network. This Dockerfile is then built with `--network none`, so neither the build nor any scored
# run performs an online resolution. A task whose POM needs an artifact this image does not carry
# fails loudly offline rather than silently resolving a different version than admission froze.
#
# The frozen measurement modes below are constants of the image, not of the run. They are recorded
# in config/images/java-v1.json under `build.jvm_measurement` so a reviewer can see the mode -> flags
# mapping was fixed before any candidate existed. Nothing in this file reads a task, a candidate or
# a measurement result.
ARG COMPONENTS_IMAGE
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE} AS python-source
FROM ${COMPONENTS_IMAGE}
ARG RECIPE

# The sandbox provider drives every guest with `python -I -B -S` (keep-alive, input staging and the
# workspace snapshot writer), and the guest tooling in /opt/pcb/guest is Python too. The Maven base
# image is Ubuntu-based (newer glibc) and the pinned interpreter image is Debian bookworm (older
# glibc), so the interpreter is copied in *with its own libraries* and started through its own
# dynamic loader by a one-line launcher. Nothing else in the image sees those libraries, so javac
# and the forked test JVM keep linking against the base image's libc.
COPY --from=python-source /usr/local/bin/python3.12 /opt/pcb/python/bin/python3.12
COPY --from=python-source /usr/local/lib/libpython3.12.so.1.0 /opt/pcb/python/lib/libpython3.12.so.1.0
COPY --from=python-source /usr/local/lib/python3.12 /opt/pcb/python/lib/python3.12
COPY --from=python-source /usr/lib/x86_64-linux-gnu /opt/pcb/python/lib
RUN set -eu; \
    { \
        echo '#!/bin/sh'; \
        echo 'export PYTHONHOME=/opt/pcb/python'; \
        echo 'exec /opt/pcb/python/lib/ld-linux-x86-64.so.2 --library-path /opt/pcb/python/lib /opt/pcb/python/bin/python3.12 "$@"'; \
    } > /usr/local/bin/python; \
    chmod 0755 /usr/local/bin/python; \
    ln -s python /usr/local/bin/python3; \
    python -I -B -S -c 'import hashlib, tarfile, json, os, zlib; print(hashlib.sha256(b"x").hexdigest()[:8])'

# The offline repository and the Maven configuration that points at it. `localRepository` is fixed
# here rather than left to MAVEN_OPTS so a plan cannot redirect a scored run at another repository.
ENV MAVEN_REPO_LOCAL=/opt/pcb/m2/repository \
    MAVEN_CONFIG=/opt/pcb/maven \
    MAVEN_OPTS=-Dmaven.repo.local=/opt/pcb/m2/repository \
    HOME=/opt/pcb/home \
    JAVA_TOOL_OPTIONS=-XX:-UsePerfData \
    LC_ALL=C \
    TZ=UTC \
    PCB_RECIPE=${RECIPE}

RUN set -eu; \
    mkdir -p /opt/pcb/home /opt/pcb/maven /opt/pcb/m2; \
    { \
        printf '%s\n' \
            '<settings>' \
            '  <localRepository>/opt/pcb/m2/repository</localRepository>' \
            '  <offline>true</offline>' \
            '  <interactiveMode>false</interactiveMode>' \
            '  <usePluginRegistry>false</usePluginRegistry>' \
            '</settings>'; \
    } > /opt/pcb/maven/settings.xml; \
    if [ "${RECIPE}" = "performance" ]; then \
        printf '%s\n' \
            '# Frozen measurement policy for the PolyCodeBench Java performance image.' \
            '#' \
            '# These are the ONLY flag sets a Java measurement may run under. They are chosen' \
            '# here, at image build time, before any candidate exists, and are recorded in' \
            '# config/images/java-v1.json under build.jvm_measurement. A plan selects one by the' \
            '# task admission-frozen measurement_mode; nothing may choose one from a timing, a' \
            '# candidate, or an observed result. Two candidates measured under different flags' \
            '# would not be comparable, which is why an unknown mode is an error rather than a' \
            '# default.' \
            'JAVA_MEASUREMENT_COLD=-Xint -XX:-UsePerfData' \
            'JAVA_MEASUREMENT_STEADY_STATE=-XX:+UseParallelGC -XX:-UsePerfData -XX:+AlwaysPreTouch -XX:+UseCompressedOops -XX:ActiveProcessorCount=1' \
            > /opt/pcb/maven/jvm-measurement; \
        chmod 0444 /opt/pcb/maven/jvm-measurement; \
    fi; \
    # The offline self-check projects are shipped as files and copied in below; a heredoc cannot be
    # used inside a continued RUN, and an inline probe (a help-plugin invocation) resolves a
    # different graph than a real build does.
    echo "recipe ${RECIPE}: toolchain present"

# Offline self-check. The evaluator recipe swaps in the POM that also drives SpotBugs, PMD and
# Checkstyle, so a missing analyzer artifact fails the image build here rather than at grading time,
# where it would be indistinguishable from a candidate defect.
COPY selfcheck /opt/pcb/selfcheck
RUN set -eu; \
    if [ "${RECIPE}" = "evaluator" ]; then \
        cp /opt/pcb/selfcheck/evaluator-pom.xml /opt/pcb/selfcheck/pom.xml; \
        mvn -o -B --no-transfer-progress -f /opt/pcb/selfcheck/pom.xml \
            compile spotbugs:check pmd:pmd checkstyle:checkstyle; \
        ls -l /opt/pcb/selfcheck/target/spotbugs.xml \
               /opt/pcb/selfcheck/target/pmd.xml \
               /opt/pcb/selfcheck/target/checkstyle-result.xml; \
        echo 'evaluator: spotbugs, pmd and checkstyle resolve and run offline'; \
    else \
        mvn -o -B --no-transfer-progress -f /opt/pcb/selfcheck/pom.xml test; \
        ls -l /opt/pcb/selfcheck/target/surefire-reports/; \
        echo "${RECIPE}: compile, surefire and junit resolve and run offline"; \
    fi; \
    rm -rf /opt/pcb/selfcheck
COPY pcb /opt/pcb
LABEL org.polycodebench.image="java" \
      org.polycodebench.recipe="${RECIPE}"
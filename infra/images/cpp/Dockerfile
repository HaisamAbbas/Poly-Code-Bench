# PolyCodeBench C++ images (Prompt 21, PCB-21).
#
# Three recipes are built from two *components* images, and the recipe distinction is real:
#
#   runtime     - components-runtime: clang++ and libstdc++ only. Build plans, test plans, the
#                 solve guest and the instrumented analyzer runs. No clang-tidy, no cppcheck: a
#                 candidate must not be able to read, from inside the image it is scored in, the
#                 exact lints and suppressions that will judge it. Everything the evaluator knows,
#                 the runtime image does not.
#   evaluator   - components-evaluator: runtime plus clang-tidy and cppcheck with the pinned
#                 rules. Analyzer plans run here and only here.
#   performance - components-runtime plus a baked /opt/pcb/release-flags file holding the release
#                 cxxflags of config/languages/cpp-toolchain-v1.json, so measurement codegen is
#                 fixed by the image rather than left to whatever a task's recipe.json happens to
#                 say. Two candidates that both ask for "release" get the same machine code.
#
# `scripts/fetch_cpp_components.py` installs the apt packages at the versions the toolchain lock
# pins, from an immutable Debian snapshot, and is the ONLY step that uses a network. This Dockerfile
# is then built with `--network none`, so neither the build nor any scored run performs an online
# installation or fetch. apt's package lists are removed in the components image, so a runtime that
# tried to install anything would fail loudly rather than quietly succeed.
ARG COMPONENTS_IMAGE
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE} AS python-source
FROM ${COMPONENTS_IMAGE}
ARG RECIPE
# The sandbox provider drives every guest with `python -I -B -S` (keep-alive, input staging and the
# workspace snapshot writer), and the guest tooling in /opt/pcb/guest is Python too. The Debian
# base ships no interpreter and the pinned interpreter image is newer, so the interpreter is copied
# in *with its own libraries* and started through its own dynamic loader by a one-line launcher.
# Nothing else in the image sees those libraries, so clang++, the sanitizer runtimes and the test
# binaries keep linking against the base image's libc.
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
    python -I -B -S -c 'import hashlib, json, os; print(hashlib.sha256(b"x").hexdigest()[:8])'
# A sanitizer plan sets ASAN_OPTIONS/TSAN_OPTIONS itself (toolchain lock, instrumentation.*), so the
# image fixes the *file* settings a plan cannot choose: symbolization on, and no interactive
# prompt that could hang a scored run waiting for input that never comes.
ENV ASAN_OPTIONS_SYMBOLIZER_PATH=/usr/bin/llvm-symbolizer-14 \
    UBSAN_OPTIONS_SYMBOLIZER_PATH=/usr/bin/llvm-symbolizer-14 \
    PCB_RECIPE=${RECIPE}
# The release flags are baked, never read from a candidate-controlled file, and they are the exact
# cxxflags of the toolchain lock's `release` profile: -DNDEBUG and -O3 remove the debug
# assertions and overflow checks whose cost would otherwise dominate a timed iteration, and
# -fno-omit-frame-pointer keeps the measured stack observable to the profiler.
RUN set -eu; \
    mkdir -p /opt/pcb; \
    if [ "${RECIPE}" = "performance" ]; then \
        printf '%s\n' '-O3 -DNDEBUG -fno-omit-frame-pointer' > /opt/pcb/release-flags; \
    else \
        rm -f /opt/pcb/release-flags; \
    fi; \
    if [ "${RECIPE}" = "evaluator" ]; then \
        clang-tidy --version; \
        cppcheck --version; \
    else \
        # The recipe distinction has to be real, so its absence is asserted here rather than
        # assumed: an analyzer present in the runtime or performance image would let a candidate
        # iterate against the exact check list it is scored on.
        ! command -v clang-tidy > /dev/null; \
        ! command -v cppcheck > /dev/null; \
    fi; \
    clang++ --version
COPY pcb /opt/pcb
LABEL org.polycodebench.image="cpp" \
      org.polycodebench.recipe="${RECIPE}"

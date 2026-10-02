# PolyCodeBench C images (Prompt 20, PCB-20-1).
#
# C has one hazard the other languages do not: optimization and instrumentation are the *same
# compiler*. A `-fsanitize=address` binary and a `-O2` binary are both clang output, so nothing in the
# file itself says which it is. Four recipes are therefore built, and each one is a different image
# with a recorded digest, not a flag on one image:
#
#   runtime     - clang + libc headers. The solve guest and every acceptance build/test. No analyzers,
#                 so a candidate cannot inspect the checks that will judge it.
#   evaluator   - runtime plus clang-tidy, cppcheck and Valgrind. Static analysis and memcheck.
#   instrumented - runtime, used with the sanitizer flags linked into the binary. This is the only
#                 recipe a dynamic lane may run in, which is what makes "sanitizer timing is never
#                 release performance" a property of the plan, not a convention.
#   performance - runtime with no analyzers. Measurements only; `require_release_recipe` refuses any
#                 plan that would measure in an instrumented image.
#
# The analyzer and Valgrind packages are installed in the base stage and *removed* from the recipes
# that must not have them, so "the evaluator image ships clang-tidy and the runtime image does not"
# is a file-level fact rather than an entry in a configuration file.
ARG BASE_IMAGE=debian:bookworm-slim
FROM ${BASE_IMAGE}

ARG RECIPE
ENV DEBIAN_FRONTEND=noninteractive

RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        clang \
        libc6-dev \
        python3 \
        python-is-python3 \
        ca-certificates; \
    if [ "${RECIPE}" = "evaluator" ]; then \
        apt-get install -y --no-install-recommends clang-tidy cppcheck valgrind; \
    fi; \
    # The sanitizer runtimes are a *separate* Debian package from the compiler, and only the
    # instrumented recipe needs them. Installing them here - and only here - is what makes the
    # separation a file-level fact: a `-fsanitize=address` link in the runtime or performance image
    # fails outright, so an instrumented build cannot be produced outside the recipe that declares it.
    if [ "${RECIPE}" = "instrumented" ]; then \
        apt-get install -y --no-install-recommends libclang-rt-14-dev; \
    fi; \
    rm -rf /var/lib/apt/lists/*

# The recipes that must not carry an analyzer or a sanitizer runtime have theirs removed, so
# `require_tools` below can probe for absence instead of trusting the recipe name.
RUN set -eu; \
    if [ "${RECIPE}" != "evaluator" ]; then \
        rm -f /usr/bin/clang-tidy /usr/bin/cppcheck /usr/bin/valgrind; \
        rm -rf /usr/lib/llvm-*/share/clang; \
    fi; \
    if [ "${RECIPE}" != "instrumented" ]; then \
        rm -rf /usr/lib/llvm-*/lib/clang/*/lib/linux; \
    fi

# The sanitizer runtimes are part of the compiler, so what separates the instrumented recipe is the
# frozen flag set the plugin will use, not a package. Baking the recipe name into the image means a
# captured report can state which recipe produced it without trusting the plan that asked.
RUN set -eu; \
    mkdir -p /opt/pcb; \
    printf 'recipe=%s\nc_standard=c17\n' "${RECIPE}" > /opt/pcb/recipe.env; \
    printf '{"schema":"pcb-c-guest-env-v1","recipe":"%s"}\n' "${RECIPE}" > /opt/pcb/recipe.json

ENV PCB_RECIPE=${RECIPE} \
    LC_ALL=C

COPY pcb /opt/pcb

# Probe every tool this recipe claims, and every tool it must not have. A recipe that silently gained
# an analyzer would let a candidate's build see it; a recipe that silently lost one would turn a
# required scan into an unexplained clean result.
RUN set -eu; \
    clang --version > /dev/null; \
    if [ "${RECIPE}" = "evaluator" ]; then \
        clang-tidy --version > /dev/null; \
        cppcheck --version > /dev/null; \
        valgrind --version > /dev/null; \
    else \
        for tool in clang-tidy cppcheck valgrind; do \
            if command -v "$tool" > /dev/null 2>&1; then \
                echo "recipe ${RECIPE} must not ship ${tool}" >&2; \
                exit 1; \
            fi; \
        done; \
    fi; \
    # clang echoes the bare library name back when it cannot resolve one, so a resolved path is
    # exactly the evidence that the runtime is installed.
    asan_runtime="$(clang -print-file-name=libclang_rt.asan-x86_64.so)"; \
    case "${asan_runtime}" in \
        */*) resolved=yes ;; \
        *)   resolved=no ;; \
    esac; \
    if [ "${RECIPE}" = "instrumented" ]; then \
        if [ "${resolved}" != "yes" ]; then \
            echo "the instrumented recipe must ship the AddressSanitizer runtime" >&2; exit 1; \
        fi; \
        ubsan_runtime="$(clang -print-file-name=libclang_rt.ubsan_standalone-x86_64.so)"; \
        case "${ubsan_runtime}" in */*) ;; *) \
            echo "the instrumented recipe must ship the UndefinedBehaviorSanitizer runtime" >&2; \
            exit 1 ;; \
        esac; \
    elif [ "${resolved}" = "yes" ]; then \
        echo "recipe ${RECIPE} must not ship the sanitizer runtimes" >&2; \
        exit 1; \
    fi; \
    python -I -B -S -c 'import hashlib; print(hashlib.sha256(b"x").hexdigest()[:8])'

LABEL org.polycodebench.image="c" \
      org.polycodebench.recipe="${RECIPE}"
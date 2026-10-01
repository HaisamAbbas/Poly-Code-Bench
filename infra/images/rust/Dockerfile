# PolyCodeBench Rust images (Prompt 11, PCB-11-1).
#
# Three recipes are built from two *components* images, and the recipe distinction is real:
#
#   runtime     - components-base: rustc + cargo only. Build/test plans and the solve guest.
#                 No clippy, no rustfmt, no nightly, no Miri: a candidate cannot inspect the
#                 analyzers that will judge it from inside the image it runs in.
#   evaluator   - components-evaluator: runtime plus clippy, rustfmt and the pinned Miri nightly.
#                 Analyzer plans run here.
#   performance - components-base plus a baked release profile, so measurement codegen is fixed
#                 by the image rather than left to whatever the task's manifest happens to say.
#
# `rustup` deletes a component's payload once it is unpacked, so there is nothing file-level to
# vendor; the Rust analogue of the Python wheelhouse is therefore a prebuilt components image.
# `scripts/fetch_rust_components.py` builds them and is the ONLY step that uses a network. This
# Dockerfile is then built with `--network none`, so neither the build nor any scored run performs
# an online installation or fetch.
ARG COMPONENTS_IMAGE
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE} AS python-source
FROM ${COMPONENTS_IMAGE}
ARG RECIPE
# The sandbox provider drives every guest with `python -I -B -S` (keep-alive, input staging and the
# workspace snapshot writer), and the guest tooling in /opt/pcb/guest is Python too. The Rust base
# image (Debian 12, glibc 2.36) ships no interpreter and the pinned interpreter image is newer
# (glibc 2.41), so the interpreter is copied in *with its own libraries* and started through its own
# dynamic loader by a one-line launcher. Nothing else in the image sees those libraries, so rustc,
# cargo and the test binaries keep linking against the base image's libc.
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
ENV CARGO_NET_OFFLINE=true \
    CARGO_TERM_COLOR=never \
    CARGO_HOME=/opt/pcb/cargo \
    XDG_CACHE_HOME=/opt/pcb/cache \
    PCB_RECIPE=${RECIPE}
# Offline crates (Miri's sysroot dependencies) plus cargo source replacement. A task crate that
# resolved a crate this image does not carry fails loudly offline rather than silently reaching
# for crates.io during a scored run.
RUN set -eu; \
    mkdir -p /opt/pcb/cargo; \
    { \
        printf '%s\n' \
            '[source.crates-io]' \
            'replace-with = "vendored-sources"' \
            '' \
            '[source.vendored-sources]' \
            'directory = "/opt/pcb/vendor"'; \
        if [ "${RECIPE}" = "performance" ]; then \
            printf '%s\n' '' \
                '# Measurement profile, fixed by the image so timings are comparable across' \
                '# candidates and reruns. Debug assertions and overflow checks are off because' \
                '# they are development aids whose cost would dominate a timed iteration.' \
                '[profile.release]' \
                'opt-level = 3' \
                'debug = false' \
                'debug-assertions = false' \
                'overflow-checks = false' \
                'lto = false' \
                'codegen-units = 1' \
                'panic = "abort"'; \
        fi; \
    } > /opt/pcb/cargo/config.toml; \
    if [ "${RECIPE}" = "evaluator" ]; then rustc --version; cargo clippy --version; fi; \
    if [ "${RECIPE}" = "evaluator" ]; then \
        mkdir -p /opt/pcb/cache /tmp/warm/src /tmp/warm/tests; \
        { echo '[package]'; echo 'name = "warm"'; echo 'version = "0.1.0"'; echo 'edition = "2021"'; } > /tmp/warm/Cargo.toml; \
        echo 'pub fn one() -> u8 { 1 }' > /tmp/warm/src/lib.rs; \
        { echo '#[test]'; echo 'fn one() { assert_eq!(warm::one(), 1); }'; } > /tmp/warm/tests/warm.rs; \
        # Warm with a `--test` run, the shape the plans use: a run that includes doctests computes a
        # different sysroot configuration and would leave this one stale for every real plan.
        (cd /tmp/warm && cargo +nightly-2026-09-30 miri test --offline --test warm); \
        rm -rf /tmp/warm; \
        chmod -R a+rX /opt/pcb/cache; \
    fi; \
    rustc --version; cargo --version
COPY pcb /opt/pcb
LABEL org.polycodebench.image="rust" \
      org.polycodebench.recipe="${RECIPE}"
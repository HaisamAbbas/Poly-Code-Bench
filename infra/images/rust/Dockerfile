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
FROM ${COMPONENTS_IMAGE}
ARG RECIPE
ENV CARGO_NET_OFFLINE=true \
    CARGO_TERM_COLOR=never \
    CARGO_HOME=/opt/pcb/cargo \
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
    rustc --version; cargo --version
COPY pcb /opt/pcb
LABEL org.polycodebench.image="rust" \
      org.polycodebench.recipe="${RECIPE}"
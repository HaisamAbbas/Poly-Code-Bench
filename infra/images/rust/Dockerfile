# PolyCodeBench Rust images (Prompt 11).
#
# Three distinct recipes share one pinned toolchain:
#   runtime     - rustc/cargo only; build and test plans run here (also the solve-guest image).
#   evaluator   - runtime + clippy, rustfmt and Miri (nightly); analyzer plans run here.
#   performance - runtime + release-profile measurement settings. Performance iteration plan.
#
# The Rust analogue of the Python wheelhouse is a prebuilt *components* image: `rustup` deletes a
# component's payload once it is unpacked, so there is nothing file-level to vendor. That image is
# built once, with network, by scripts/fetch_rust_components.py, and it is the ONLY online step.
# This Dockerfile is then built with `--network none` and copies the finished toolchain home out of
# it, so neither the build nor any scored run performs an online installation or fetch.
ARG BASE_IMAGE
ARG COMPONENTS_IMAGE
FROM ${COMPONENTS_IMAGE} AS toolchain
FROM ${BASE_IMAGE}
ARG RECIPE
ENV CARGO_NET_OFFLINE=true \
    CARGO_TERM_COLOR=never \
    PATH=/opt/pcb/bin:${PATH} \
    PCB_RECIPE=${RECIPE}
# The completed rustup home: stable toolchain + clippy/rustfmt + the pinned Miri nightly.
COPY --from=toolchain /usr/local/rustup /usr/local/rustup
# Miri's sysroot build resolves real crates from crates.io; the vendored copies ship in the
# components image, so an offline scored run never reaches the network. Without this the sysroot
# build fails loudly (never silently clean) and a Miri-required task is reported unsupported.
ENV CARGO_HOME=/opt/pcb/cargo
RUN set -eu; \
    mkdir -p /opt/pcb/cargo; \
    printf '%s\n' \
        '[source.crates-io]' \
        'replace-with = "vendored-sources"' \
        '' \
        '[source.vendored-sources]' \
        'directory = "/opt/pcb/vendor"' > /opt/pcb/cargo/config.toml; \
    if [ "${RECIPE}" = "performance" ]; then \
        mkdir -p /etc/pcb; \
        printf 'CARGO_PROFILE_RELEASE_LTO=false\nCARGO_PROFILE_RELEASE_CODEGEN_UNITS=1\n' \
            > /etc/pcb/performance.env; \
    fi; \
    rustc --version; cargo --version
COPY --from=toolchain /opt/pcb/vendor /opt/pcb/vendor
COPY pcb /opt/pcb
LABEL org.polycodebench.image="rust" \
      org.polycodebench.recipe="${RECIPE}"
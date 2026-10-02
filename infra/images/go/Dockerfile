# PolyCodeBench Go images (Prompt 22, PCB-22-1).
#
# Three recipes are built from two *components* images, and the recipe distinction is real:
#
#   runtime     - components-base: the pinned toolchain only. Build, test, vet, race and the solve
#                 guest. No staticcheck, no gosec.
#   evaluator   - components-evaluator: runtime plus staticcheck and gosec at pinned versions.
#                 Analyzer plans that need them run here.
#   performance - components-base plus a fixed release profile and a warmed build cache.
#
# The Go toolchain and its analyzers are installed from pinned module versions in the components
# image, so the faithful analogue of the Python wheelhouse is a prebuilt image.
# `scripts/fetch_go_components.py` builds them and is the ONLY step that uses a network. This
# Dockerfile is then built with `--network none`, so neither the build nor any scored run performs
# an online installation or fetch. A scored run additionally sets GOPROXY=off and
# GOTOOLCHAIN=local, so it cannot resolve a module or download a toolchain even if one were
# reachable.
ARG COMPONENTS_IMAGE
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE} AS python-source
FROM ${COMPONENTS_IMAGE}
ARG RECIPE
# The sandbox provider drives every guest with `python -I -B -S` (keep-alive, input staging and the
# workspace snapshot writer), and the guest tooling in /opt/pcb/guest is Python too. The Go base
# image (Debian 13) ships no interpreter and the pinned interpreter image is built for an older
# glibc, so the interpreter is copied in *with its own libraries* and started through its own
# dynamic loader by a one-line launcher. Nothing else in the image sees those libraries, so the Go
# toolchain and the built binaries keep linking against the base image's libc.
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
# GOTOOLCHAIN=local is the flag-identity guarantee: without it a task whose go.mod asks for a newer
# release makes the toolchain *download* that release, so two runs of the same image would not
# share a compiler. GOPROXY=off and GOSUMDB=off mean a module the graph does not pin fails instead
# of being fetched.
ENV GOTOOLCHAIN=local \
    GOPROXY=off \
    GOSUMDB=off \
    GOENV=off \
    GOFLAGS=-mod=mod \
    GOCACHE=/opt/pcb/cache/go-build \
    GOMODCACHE=/opt/pcb/cache/go-mod \
    GOPATH=/opt/pcb/cache/go-path \
    PATH=/opt/pcb/bin:/usr/local/go/bin:/usr/local/go/bin:/go/bin:${PATH} \
    PCB_RECIPE=${RECIPE}
RUN set -eu; \
    mkdir -p /opt/pcb/cache/go-build /opt/pcb/cache/go-mod /opt/pcb/cache/go-path /opt/pcb/bin; \
    if [ "${RECIPE}" = "evaluator" ]; then \
        cp /opt/pcb/tools/staticcheck /opt/pcb/tools/gosec /opt/pcb/bin/; \
    fi; \
    # The race detector needs cgo and a C toolchain. It must be *available* in the image that runs
    # race plans and must be absent as a plan from the measurement lane; the toolchain is part of
    # the pinned base, so this only records that the instrumented path is real.
    if [ "${RECIPE}" = "performance" ]; then \
        mkdir -p /tmp/warm; \
        printf 'module warm\n\ngo 1.26\n' > /tmp/warm/go.mod; \
        printf 'package main\n\nfunc main() {}\n' > /tmp/warm/main.go; \
        (cd /tmp/warm && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /dev/null .); \
        rm -rf /tmp/warm; \
    fi; \
    chmod -R a+rX /opt/pcb/cache; \
    go version; \
    gofmt --help 2>&1 | head -n 1; \
    if [ "${RECIPE}" = "evaluator" ]; then staticcheck -version; gosec --version; fi
COPY pcb /opt/pcb
LABEL org.polycodebench.image="go" \
      org.polycodebench.recipe="${RECIPE}"
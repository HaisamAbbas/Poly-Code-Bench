# PolyCodeBench JavaScript + TypeScript images (Prompt 19, PCB-19-1).
#
# Six images are built from three *components* images, and the recipe distinction is real:
#
#   runtime     - components image without eslint and without tsc (TypeScript runtime does carry
#                 tsc, because a TypeScript candidate must be able to type-check what it writes
#                 from inside the image it runs in). Build/test plans and the solve guest.
#                 No ESLint, no TypeScript compiler where the language does not need one, no
#                 security tooling: a candidate cannot inspect the analyzers that will judge it
#                 from inside the image it runs in.
#   evaluator   - components image with eslint (and tsc for TypeScript). Analyzer plans.
#   performance - the runtime components image plus measurement flags baked here, so measurement is
#                 fixed by the image rather than left to whatever the task's manifest happens to say.
#
# npm resolves and fetches from a network, so the components images are the JS analogue of the Rust
# prebuilt toolchain image and of the Python wheelhouse: `scripts/fetch_js_components.py` runs
# `npm ci` once, **with** network, against the committed package-lock.json. This Dockerfile is then
# built with `--network none` and installs nothing at all, so neither the build nor any scored run
# performs an online installation or fetch.
ARG COMPONENTS_IMAGE
ARG PYTHON_IMAGE
ARG MEASUREMENT_STAGE
FROM ${PYTHON_IMAGE} AS python-source
FROM ${COMPONENTS_IMAGE} AS components
ARG RECIPE
ARG LANGUAGE
# The sandbox provider drives every guest with `python -I -B -S` (keep-alive, input staging and the
# workspace snapshot writer), and the guest tooling in /opt/pcb/guest is Python too. The Node base
# image (Debian, older glibc) ships no interpreter and the pinned interpreter image is newer, so the
# interpreter is copied in *with its own libraries* and started through its own dynamic loader by a
# one-line launcher. Nothing else in the image sees those libraries, so node, npm, vitest, eslint and
# tsc keep resolving against the base image's libc.
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
# The components image carries its resolved node_modules under /opt/pcb/components; /opt/pcb/js is the
# stable prefix every plan addresses. It is a symlink so the closure is installed exactly once, and
# the pinned .bin shims resolve their real targets, so `vitest`, `eslint` and `tsc` work from any
# working directory through NODE_PATH and PATH.
RUN set -eu; \
    mkdir -p /opt/pcb/js; \
    ln -s /opt/pcb/components/node_modules /opt/pcb/js/node_modules; \
    ls /opt/pcb/js/node_modules/.bin > /dev/null
# Nothing may silently reach a registry. `offline=true` plus a registry that resolves nowhere makes an
# accidental `npm install` inside a scored run fail loudly instead of quietly downloading a package
# the identity file does not know about.
RUN set -eu; \
    printf '%s\n' \
        'offline=true' \
        'audit=false' \
        'fund=false' \
        'update-notifier=false' \
        'save-exact=true' \
        'registry=http://localhost:0/' > /opt/pcb/js/npmrc; \
    chmod 0444 /opt/pcb/js/npmrc
ENV NODE_PATH=/opt/pcb/js/node_modules \
    PATH=/opt/pcb/js/node_modules/.bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    NPM_CONFIG_USERCONFIG=/opt/pcb/js/npmrc \
    npm_config_offline=true \
    npm_config_audit=false \
    npm_config_fund=false \
    npm_config_update_notifier=false \
    PCB_RECIPE=${RECIPE} \
    PCB_LANGUAGE=${LANGUAGE}
# Two measurement profiles, selected by the build script through MEASUREMENT_STAGE so the flags live
# in this file rather than in whatever the task manifest says.
FROM components AS measured-runtime
# Solve and analysis runs: a bounded heap, so a runaway candidate is a failure rather than an
# out-of-memory kill of the whole guest.
ENV NODE_OPTIONS=--max-old-space-size=2048
FROM components AS measured-performance
# Measurement profile, fixed by the image so timings are comparable across candidates and reruns.
# The heap and the young generation are both pinned, because a run whose GC schedule depends on
# whatever the task passed is not a measurement. The inspector is deliberately NOT enabled and
# --expose-gc is deliberately NOT passed: a task manifest cannot attach a debugger or force a
# collection in the middle of a timed iteration.
ENV NODE_OPTIONS=--max-old-space-size=4096 --max-semi-space-size=16 \
    NODE_ENV=production
FROM ${MEASUREMENT_STAGE}
ARG RECIPE
ARG LANGUAGE
RUN set -eu; \
    node --version; npm --version; \
    test -x /opt/pcb/js/node_modules/.bin/vitest
COPY pcb /opt/pcb
LABEL org.polycodebench.image="${LANGUAGE}" \
      org.polycodebench.recipe="${RECIPE}"
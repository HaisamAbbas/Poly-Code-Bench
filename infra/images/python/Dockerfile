# PolyCodeBench Python runtime / evaluator image (Prompt 10).
# Built with `--network none`: every package comes from the hash-verified local wheelhouse, so a
# scored run (and the build itself) performs no online installation.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG LOCKFILE
ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1
COPY wheelhouse /tmp/wheelhouse
COPY ${LOCKFILE} /tmp/requirements.lock
RUN python -m pip install --no-index --no-deps --require-hashes \
        --find-links /tmp/wheelhouse -r /tmp/requirements.lock \
    && rm -rf /tmp/wheelhouse /tmp/requirements.lock /root/.cache
COPY pcb /opt/pcb
LABEL org.polycodebench.image="python" \
      org.polycodebench.lock-file="${LOCKFILE}"

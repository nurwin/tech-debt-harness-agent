# Sandbox executor image: Pi (pinned) + the toolchain the verifier needs.
# Pi has NO permission system — this container is the security boundary, and the
# harness only ever runs it with --network none / a dedicated per-run network,
# read-only root, non-root user, cap-drop ALL (see orchestrator/src/sandbox/sandbox.py).
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip git \
    && rm -rf /var/lib/apt/lists/*

# Pin the exact Pi version for reproducible builds. Package is
# @earendil-works/pi-coding-agent — the @mariozechner/* name is DEPRECATED.
# --ignore-scripts is Pi's own recommended install flag.
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.80.3 \
    && pi --version

# Verifier toolchain + fixture runtime deps (matches sample-repo requirements.txt).
RUN pip3 install --break-system-packages --no-cache-dir \
    pytest ruff fastapi httpx email-validator uvicorn

# Non-root by default; the harness also passes --user 1000:1000 at run time.
USER 1000:1000

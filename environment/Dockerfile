# Custom environment for the migration Cloud Agents.
# Preloads the exact toolchain the playbook assumes so agents don't waste turns
# installing it: Python 3.14, uv, just, copier, git.
FROM python:3.14-slim

# System deps: git for repo ops, curl for tool installers, build-essential for
# any packages that still need to compile under 3.14.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# uv (fast dependency resolver / installer) — pinned installer script.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# just (command runner) and copier (template application) via uv's tool installer.
RUN uv tool install rust-just \
    && uv tool install copier

# Sanity: fail the image build early if the toolchain is broken.
RUN python --version && uv --version && just --version && copier --version

WORKDIR /workspace

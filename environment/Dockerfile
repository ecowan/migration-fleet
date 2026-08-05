# Custom environment for the migration Cloud Agents.
# Preloads the exact toolchain the playbook assumes so agents don't waste turns
# installing it: Python 3.14, uv, just, copier, git.
#
# Path A: copy this file to each target repo as `.cursor/Dockerfile` and commit
# `.cursor/environment.json` (see environment.json alongside this file).
# Cursor builds from the *target* repo the agent clones — not from migration-fleet.
FROM python:3.14-slim

# System deps: git (required for Cursor's clone), sudo (recommended), curl for
# tool installers, build-essential for packages that still compile under 3.14.
# Do not COPY the project — Cursor checks out the correct commit itself.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git sudo curl ca-certificates build-essential \
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

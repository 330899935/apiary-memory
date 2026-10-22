# Dockerfile for directory crawlers (Glama, and anything else that builds from source).
#
# Two reasons this file exists rather than letting an automated system infer one:
#
#   1. Glama builds every listed server from a Dockerfile - ours, or one its AI
#      infers from the project layout. If the inferred build fails, the profile
#      page is kept but *distribution is withheld*: the server never appears in
#      search, categories, or recommendations. A written Dockerfile removes that
#      coin flip.
#   2. There is nothing to infer. This package is a stdlib-only Python bridge with
#      no system dependencies, no compiler step and no native libraries, which is
#      exactly the case where a hand-written file is five lines instead of a guess.
#
# Note there is no `pip install` below, and that is deliberate. `pip install .`
# would need to fetch the build backend (setuptools) from PyPI, because the
# official python:3.12 image no longer ships it - so the build would depend on
# outbound network access inside a sandbox we do not control. The package has no
# dependencies at all, so the image simply puts `src/` on the import path and runs
# the module. A build that cannot fail beats a build that usually succeeds.
#
# What the crawl sees: the container has no Hive behind it, so `initialize` and
# `tools/list` are answered from the bundled snapshot and `tools/call` fails with
# an explicit error naming HIVE_MCP_URL. That is the designed behaviour - see
# "What happens without a running Hive" in README.md.

FROM python:3.12-slim

# Unbuffered so a stdio client sees replies immediately instead of at exit.
# PYTHONDONTWRITEBYTECODE keeps the filesystem read-only in practice: the bridge
# never needs to write anything.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Package source only - no tests, no .git, no build artefacts.
COPY src ./src

# Non-root. The bridge only reads its bundled tools.json and talks to a local
# socket, so it needs no write access anywhere.
RUN useradd --create-home --uid 10001 hive
USER hive

# The bridge speaks JSON-RPC over stdin/stdout; it is not an HTTP server.
# Nothing is exposed, and no port is opened.
ENTRYPOINT ["python", "-m", "hive_mcp.stdio"]

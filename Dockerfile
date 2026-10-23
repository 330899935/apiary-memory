# Dockerfile for directory crawlers (Glama, and anything else that builds from source).
#
# Why a written file rather than letting a system infer one: Glama builds every
# listed server from a Dockerfile - ours, or one its AI infers from the project
# layout. If that build fails, the profile page is kept but *distribution is
# withheld*: the server never appears in search, categories or recommendations.
# A written file removes that coin flip.
#
# Note there is no `pip install` below, and that is deliberate. `pip install .`
# would need to fetch the build backend (setuptools) from PyPI, because the
# official python:3.12 image no longer ships it - so the build would depend on
# outbound network access inside a sandbox we do not control. The package has no
# dependencies at all, so the image simply puts `src/` on the import path and runs
# the module. A build that cannot fail beats a build that usually succeeds.
#
# What a crawler sees: no Hive app is reachable, so the local engine answers.
# `initialize` and `tools/list` come from the bundled snapshot, and `tools/call`
# is served by the SQLite store inside the container - propose really writes,
# recall really reads. That still requires a writable database path, so HIVE_DB
# points at /data and /data is declared a volume; without a mount it is
# ephemeral, which is correct for a crawl and documented in the README.

FROM python:3.12-slim

# Unbuffered so a stdio client sees replies immediately instead of at exit.
# PYTHONDONTWRITEBYTECODE keeps stray .pyc files out of the image.
# HIVE_MODE=local: inside a container there is no app to fall back from, and
# asking a non-existent socket first would add a timeout to every crawl.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    HIVE_MODE=local \
    HIVE_DB=/data/hive.db

WORKDIR /app

# Package source only - no tests, no .git, no build artefacts.
COPY src ./src

# Non-root, and the one directory it may write to. The store creates the
# database on first use, so the directory must exist and be owned by this user.
RUN useradd --create-home --uid 10001 hive \
    && mkdir -p /data \
    && chown hive:hive /data
USER hive

# Memory survives only if the caller mounts this. `docker run -v hive:/data`
# gives a persistent memory; without it the container starts empty every time.
VOLUME ["/data"]

# The server speaks JSON-RPC over stdin/stdout; it is not an HTTP server.
# Nothing is exposed, and no port is opened.
ENTRYPOINT ["python", "-m", "hive_mcp.stdio"]

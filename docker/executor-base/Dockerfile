# foreman/executor-base: the sandbox every executor step runs in.
#
# Minimal Ubuntu Server userland plus git and the executor CLIs. Projects that
# need language toolchains add them in their own `.foreman/Dockerfile`
# (FROM foreman/executor-base) - not here.
#
#   docker build -t foreman/executor-base:latest docker/executor-base
#   docker build --build-arg OPENCODE_VERSION=1.19.0 -t foreman/executor-base:latest docker/executor-base

FROM ubuntu:24.04

ARG OPENCODE_VERSION=latest
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git bash tar gzip \
 && rm -rf /var/lib/apt/lists/*

# OpenCode: single static binary from the GitHub release.
RUN set -eux; \
    arch="$(case "${TARGETARCH:-amd64}" in amd64) echo x64 ;; arm64) echo arm64 ;; *) echo "${TARGETARCH}" ;; esac)"; \
    if [ "$OPENCODE_VERSION" = "latest" ]; then \
      url="https://github.com/sst/opencode/releases/latest/download/opencode-linux-${arch}.tar.gz"; \
    else \
      url="https://github.com/sst/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-${arch}.tar.gz"; \
    fi; \
    curl -fsSL "$url" | tar -xz -C /usr/local/bin opencode; \
    chmod 0755 /usr/local/bin/opencode; \
    opencode --version

# Non-root user the orchestrator runs containers as (uid/gid 1000).
# ubuntu:24.04 ships an "ubuntu" user with uid 1000; replace it with "agent".
RUN userdel -r ubuntu 2>/dev/null || true \
 && groupadd -g 1000 agent \
 && useradd -m -u 1000 -g 1000 -s /bin/bash agent \
 # Task clones are mounted from the host and may be owned by another uid.
 && git config --system safe.directory '*' \
 && git config --system user.name foreman-agent \
 && git config --system user.email agent@foreman.local

USER agent
WORKDIR /workspace

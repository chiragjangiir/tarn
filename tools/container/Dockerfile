FROM debian:bookworm-slim
ARG VERSION
ARG REVISION
LABEL org.opencontainers.image.source="https://github.com/tarn-lng/tarn" \
      org.opencontainers.image.description="Tarn Linux x86_64 compiler, LSP and native application build tools" \
      org.opencontainers.image.licenses="MIT OR Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates curl libcurl4 \
    && rm -rf /var/lib/apt/lists/*
COPY toolchain/tarn toolchain/tarn-lsp /usr/local/bin/
COPY toolchain/README.md toolchain/COPYING /usr/local/share/doc/tarn/
RUN test "$(tarn version)" = "tarn ${VERSION}"
WORKDIR /app
CMD ["tarn", "--help"]

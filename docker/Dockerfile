# XKNX Editor web UI as a container. Two final targets share one build:
#   --target web  -> ghcr.io/xknx/xknx-editor-web  (plain image: docker run / compose)
#   --target ha   -> ghcr.io/xknx/xknx-editor-ha   (Home Assistant add-on image, behind Ingress)
#
# Build context is the repository root:  docker build -f docker/Dockerfile --target web -t ... .
#
# The signing/trace-key extraction compiles a small reflection program against the KNX authoring
# assembly the user provides, so the runtime image carries the .NET SDK. The assembly itself is
# never shipped — the user puts their own assemblies in the mounted file folder (see the web target).

# ---- builder: resolve the locked workspace into a virtualenv -------------------------------------
FROM python:3.13-slim-bookworm AS builder

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# build-essential: some wheels (e.g. dukpy's duktape) build from source on arm64. git: a workspace
# dependency (xknxproject) is pinned to a git revision, so uv clones it during the sync.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . /app
# Install every workspace member (the GUI plus the MCP server it can launch) from the lock file.
RUN uv sync --frozen --no-dev --all-packages

# ---- app: the runtime image, shared by both final targets ----------------------------------------
FROM python:3.13-slim-bookworm AS app

# libGL + glib: imgui_bundle's native extension links them at import time even on the windowless
# Null backend. ca-certificates: the master-data fetch (update.knx.org) and NuGet use TLS. curl:
# the .NET install script and the container healthcheck. tini: a real PID 1 (below).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*

# .NET SDK 8 (LTS). net8.0 is the target framework the extractor builds against and matches the
# System.Configuration.ConfigurationManager 8.0.0 the trace-key path restores.
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_ROOT=/usr/share/dotnet
RUN curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && bash /tmp/dotnet-install.sh --channel 8.0 --install-dir /usr/share/dotnet \
    && ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet \
    && rm /tmp/dotnet-install.sh

# Warm the SDK build packs and the one NuGet package the trace extractor pulls, so the first key
# extraction is fast and works even without outbound network. Truly best-effort: the whole warm-up
# runs in a subshell whose failure is swallowed, so a briefly unavailable package feed does not abort
# the image build (the runtime still has network as a fallback). The cleanup always runs.
RUN set -eux; \
    ( cd /tmp \
      && dotnet new console -n warm -o warm \
      && cd warm \
      && dotnet add package System.Configuration.ConfigurationManager --version 8.0.0 \
      && dotnet build -c Release ) >/dev/null 2>&1 \
      || echo "NuGet warm-up skipped; first key extraction will restore at runtime"; \
    rm -rf /tmp/warm

WORKDIR /app
COPY --from=builder /app /app
# uv stays on PATH so the editor can launch the bundled MCP server (`uv run`) against the baked venv.
RUN pip install --no-cache-dir uv

# The venv on PATH (so `python` is the project interpreter); config, catalog and the browser's files
# land under /data (platformdirs reads XDG_DATA_HOME); never re-resolve the locked env at runtime.
ENV PATH="/app/.venv/bin:$PATH" \
    XDG_DATA_HOME=/data \
    UV_FROZEN=1 \
    UV_NO_SYNC=1
RUN mkdir -p /data
VOLUME /data
EXPOSE 8080

# tini as PID 1: reap the short-lived subprocesses the app spawns (the .NET build during key
# extraction, the MCP server) and forward signals to the app. This gives the image its own init, so
# the Home Assistant add-on sets `init: false` and a plain `docker run` needs no `--init`.
ENTRYPOINT ["/usr/bin/tini", "--"]

# ---- web: plain image ----------------------------------------------------------------------------
# Binds every interface, so the editor requires a sign-in password (XKNX_WEB_PASSWORD) unless a
# trusted reverse proxy signs users in. Two volumes: /data keeps app settings and the catalog;
# /projects is the browser's file folder — the projects you open and save, exported .knxproj files
# (including signed ones), and any ETS assemblies you drop in for signing-key extraction. Bind-mount
# a host folder at /projects to put files in and take exports out.
FROM app AS web
VOLUME /projects
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/ || true); case "$code" in 200|403) exit 0;; *) exit 1;; esac
CMD ["python", "-m", "editor_gui.main", "--web=0.0.0.0:8080", "--web-no-browser", "--web-files=/projects"]

# ---- ha: Home Assistant add-on image -------------------------------------------------------------
# Home Assistant Ingress signs users in and reverse-proxies to us, so we trust the proxy (no
# password) and rely on HA's persistent /data mount. See homeassistant/xknx-editor/config.yaml.
FROM app AS ha
COPY docker/ha-run.sh /ha-run.sh
RUN chmod +x /ha-run.sh
CMD ["/ha-run.sh"]

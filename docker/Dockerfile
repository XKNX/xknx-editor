# XKNX Editor web UI as a container. Two final targets share one build:
#   --target web  -> ghcr.io/xknx/xknx-editor-web  (plain image: docker run / compose)
#   --target ha   -> ghcr.io/xknx/xknx-editor-ha   (Home Assistant add-on image, behind Ingress)
#
# Build context is the repository root:  docker build -f docker/Dockerfile --target web -t ... .
#
# Signing/trace-key extraction runs our own reflection programs against the KNX authoring assembly
# the user provides. They are precompiled in the dotnet-build stage and shipped as framework-dependent
# assemblies, so the image carries only the .NET runtime (not the SDK). The ETS assembly itself is
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

# ---- dotnet-build: precompile our key-extraction helpers (ships no SDK) --------------------------
# Compiles OUR reflection programs (not ETS code) to framework-dependent assemblies. At runtime only
# the .NET runtime runs them; the ETS assembly is still provided by the user. The trace helper bundles
# its ConfigurationManager dependency, so no NuGet restore is needed at runtime.
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS dotnet-build
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
WORKDIR /x
COPY packages/proj/src/xknxeditor/proj/core/_extract.cs       sign/_extract.cs
COPY packages/proj/src/xknxeditor/proj/core/_extract_trace.cs trace/_extract_trace.cs
COPY packages/proj/src/xknxeditor/proj/core/_extract_oauth.cs oauth/_extract_oauth.cs
RUN set -eux; \
    gen() { \
      d="$1"; cs="$2"; pkg="$3"; \
      { echo '<Project Sdk="Microsoft.NET.Sdk">'; \
        echo '  <PropertyGroup>'; \
        echo '    <OutputType>Exe</OutputType>'; \
        echo '    <TargetFramework>net8.0</TargetFramework>'; \
        echo '    <Nullable>disable</Nullable>'; \
        echo '    <ImplicitUsings>disable</ImplicitUsings>'; \
        echo '    <AssemblyName>xknxkeyx</AssemblyName>'; \
        echo '    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>'; \
        echo '  </PropertyGroup>'; \
        echo '  <ItemGroup>'; \
        echo "    <Compile Include=\"$cs\" />"; \
        if [ -n "$pkg" ]; then echo "    $pkg"; fi; \
        echo '  </ItemGroup>'; \
        echo '</Project>'; } > "$d/xknxkeyx.csproj"; \
      dotnet publish "$d/xknxkeyx.csproj" -c Release -o "/opt/xknx-keyextract/$d"; \
    }; \
    gen sign _extract.cs ''; \
    gen trace _extract_trace.cs '<PackageReference Include="System.Configuration.ConfigurationManager" Version="8.0.0" />'; \
    gen oauth _extract_oauth.cs ''

# ---- app: the runtime image, shared by both final targets ----------------------------------------
FROM python:3.13-slim-bookworm AS app

# X11 client libs (libx11/libxext, pulling libxcb/libXau/libXdmcp): imgui_bundle's native extension
# link-needs them at import, even on the windowless Null backend — but no display or GL context is
# created, so no libGL/mesa is needed. ca-certificates: the master-data fetch (update.knx.org) and the
# .NET install script use TLS. curl: that script and the container healthcheck. tini: a real PID 1.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libx11-6 libxext6 libglib2.0-0 ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*

# .NET 8 runtime only (not the SDK) — our key-extraction helpers are precompiled in the dotnet-build
# stage and just run here. Invariant globalization so no ICU package is needed (the helpers are
# culture-insensitive: reflection + RSA export); without it .NET aborts the moment it needs a culture.
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_ROOT=/usr/share/dotnet \
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
RUN curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && bash /tmp/dotnet-install.sh --channel 8.0 --runtime dotnet --install-dir /usr/share/dotnet \
    && ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet \
    && rm /tmp/dotnet-install.sh

WORKDIR /app
COPY --from=builder /app /app
# Our precompiled key-extraction helpers (our code, no ETS content); run with the .NET runtime.
COPY --from=dotnet-build /opt/xknx-keyextract /opt/xknx-keyextract
# uv stays on PATH so the editor can launch the bundled MCP server (`uv run`) against the baked venv.
RUN pip install --no-cache-dir uv

# The venv on PATH (so `python` is the project interpreter); config, catalog and the browser's files
# land under /data (platformdirs reads XDG_DATA_HOME); never re-resolve the locked env at runtime;
# XKNX_KEYEXTRACT_DIR points key_extract at the prebuilt helpers (so the runtime suffices, no SDK).
ENV PATH="/app/.venv/bin:$PATH" \
    XDG_DATA_HOME=/data \
    XKNX_KEYEXTRACT_DIR=/opt/xknx-keyextract \
    UV_FROZEN=1 \
    UV_NO_SYNC=1
RUN mkdir -p /data
VOLUME /data
EXPOSE 8080

# tini as PID 1: reap the short-lived subprocesses the app spawns (the .NET key-extraction run, the
# MCP server) and forward signals to the app. This gives the image its own init, so the Home Assistant
# add-on sets `init: false` and a plain `docker run` needs no `--init`.
ENTRYPOINT ["/usr/bin/tini", "--"]

# ---- web: plain image ----------------------------------------------------------------------------
# Binds every interface, so the editor requires a sign-in password (XKNX_WEB_PASSWORD) unless a
# trusted reverse proxy signs users in. Two volumes: /data keeps app settings and the catalog;
# /projects is the browser's file folder — the projects you open and save, exported .knxproj files
# (including signed ones), and any ETS assemblies you drop in for signing-key extraction. Bind-mount
# a host folder at /projects to put files in and take exports out.
#
# web-run.sh starts the editor as root, or as PUID/PGID when they are set (see the script). The
# product data cache and the logs live under /data too, so an image update keeps them and a
# non-root user can write them. They stay out of the ha target, whose /data goes into HA backups.
FROM app AS web
ENV XDG_CACHE_HOME=/data/cache \
    XDG_STATE_HOME=/data/state
COPY docker/web-run.sh /web-run.sh
RUN chmod +x /web-run.sh
ENTRYPOINT ["/usr/bin/tini", "--", "/web-run.sh"]
VOLUME /projects
# Serve on XKNX_WEB_PORT (default 8080). On a bridge network remap with `-p <host>:8080`; set
# XKNX_WEB_PORT for a different in-container bind (e.g. --network host, where there is no `-p`).
# web-run.sh requires XKNX_WEB_PASSWORD unless XKNX_WEB_TRUST_PROXY=1 (behind an authenticating
# reverse proxy); XKNX_WEB_PROXY_IP then restricts answering to that proxy's address.
ENV XKNX_WEB_PORT=8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${XKNX_WEB_PORT:-8080}/" || true); case "$code" in 200|403) exit 0;; *) exit 1;; esac

# ---- ha: Home Assistant add-on image -------------------------------------------------------------
# Home Assistant Ingress signs users in and reverse-proxies to us, so we trust the proxy (no
# password) and rely on HA's persistent /data mount. See homeassistant/xknx-editor/config.yaml.
FROM app AS ha
COPY docker/ha-run.sh /ha-run.sh
RUN chmod +x /ha-run.sh
CMD ["/ha-run.sh"]

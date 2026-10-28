# XKNX Editor in a container

The image `ghcr.io/xknx/xknx-editor-web` is built from [`docker/Dockerfile`](./Dockerfile) by
[`.github/workflows/docker-image.yml`](../.github/workflows/docker-image.yml), for plain
`docker run`, Compose, any orchestrator and the [Unraid template](../unraid). It binds
`0.0.0.0:8080` and needs a password.

It runs the editor's browser UI (`--web`) with no desktop and carries the .NET 10 runtime used for
signing-key extraction. The KNX authoring assembly is never shipped — you provide your own (below).

For Home Assistant, use the
[XKNX Editor integration](https://github.com/XKNX/xknx-editor-ha-integration) instead: it runs the
editor on the Home Assistant server and shows it in the sidebar.

## Volumes

| Path | Holds |
| --- | --- |
| `/data` | app settings, the imported product catalog, the product data cache and the logs (keep on a named volume) |
| `/projects` | the browser's file folder: projects you open and save, exported `.knxproj` files (including signed ones), and any ETS assemblies you drop in for signing-key extraction |

## Environment variables

| Variable | Default | Effect |
| --- | --- | --- |
| `XKNX_WEB_PASSWORD` | — | Sign-in password. **Required** — the image binds every interface, so without it (and without a trusted proxy) the container refuses to start. |
| `XKNX_WEB_TRUST_PROXY` | — | `1` when an authenticating reverse proxy is in front: no password, forwarded requests are accepted. |
| `XKNX_WEB_PROXY_IP` | — | Answer only this address (your proxy's), rejecting everything else. Pair with `XKNX_WEB_TRUST_PROXY`. |
| `XKNX_WEB_PORT` | `8080` | In-container bind port. On a bridge network just remap with `-p <host>:8080`; change this only for `--network host`, where there is no `-p`. |

## Plain image

```bash
docker run -d --name xknx-editor \
  -p 8080:8080 \
  -e XKNX_WEB_PASSWORD='choose-a-strong-password' \
  -v xknx-editor-data:/data \
  -v /host/xknx:/projects \
  ghcr.io/xknx/xknx-editor-web:latest
```

Open `http://<host>:8080/` and sign in with the password.

- **Password is required.** The image binds every interface, so without `XKNX_WEB_PASSWORD` (or a
  trusted reverse proxy, see below) the editor refuses to start. Pick a strong one.
- **`/data`** holds settings, the catalog, the cache and the logs — a named volume keeps them across
  upgrades. The cache (`/data/cache`) can be deleted at any time; it is made again when needed.
- **`/projects`** is where the editor opens and saves files in the browser. Bind-mount a host folder
  (here `/host/xknx`) so you can drop files in and collect exports from the host.

### File ownership

By default the editor runs as root, so the files it writes to `/projects` belong to root on the host.
To run it as your own user instead, pass its IDs:

```bash
docker run -d --name xknx-editor   -p 8080:8080   -e XKNX_WEB_PASSWORD='choose-a-strong-password'   -e PUID="$(id -u)" -e PGID="$(id -g)"   -v xknx-editor-data:/data   -v /host/xknx:/projects   ghcr.io/xknx/xknx-editor-web:latest
```

| Variable | Meaning |
| --- | --- |
| `PUID` | user ID to run the editor as; unset or `0` runs it as root |
| `PGID` | its group ID (default: `PUID`) |
| `UMASK` | permissions of new files, e.g. `002` so the group may change them too (default `022`) |

- At start, `/data` is handed to that user, also a volume from an earlier version (once).
- `/projects` is only handed over while it is empty, e.g. a host folder Docker just created. A folder
  with files keeps its owner, and the log warns if the user can't write to it: change its owner on
  the host once, e.g. `sudo chown -R 1000:1000 /host/xknx`.
- With rootless Docker or Podman, leave them unset: root in the container already is your user on
  the host.
- `docker run --user` works as well, but then `/data` and `/projects` must already be writable for
  that user.

### Signing-key extraction

Exporting a project that other tools accept as signed needs a key extracted from a KNX authoring
tool you own; the key itself is never shipped. In the editor, open **Signing** and give it the
assembly in whichever way suits you:

- **Upload it from your computer** — the dialog's **Upload** button, or drag the file onto the page.
  No volume needed.
- **Pick it from `/projects`** — copy the files into the mounted folder first, then select
  `Knx.Ets.XmlSigning.dll` from the list.

Either way, include `Knx.Ets.XmlSigning.dll` with its sibling assemblies so dependencies resolve
(and, for project-log comment decryption, `Knx.Ets.Common.dll` with its siblings).

### Behind a reverse proxy

If a proxy in front authenticates users and forwards to the container, run it with trusted-proxy
instead of a password, and restrict answering to the proxy's address:

```bash
docker run -d --name xknx-editor \
  -p 127.0.0.1:8080:8080 \
  -e XKNX_WEB_TRUST_PROXY=1 \
  -e XKNX_WEB_PROXY_IP=172.18.0.2 \
  -v xknx-editor-data:/data -v /host/xknx:/projects \
  ghcr.io/xknx/xknx-editor-web:latest
```

### Compose

```yaml
services:
  xknx-editor:
    image: ghcr.io/xknx/xknx-editor-web:latest
    ports:
      - "8080:8080"
    environment:
      XKNX_WEB_PASSWORD: choose-a-strong-password
    volumes:
      - xknx-editor-data:/data
      - /host/xknx:/projects
    restart: unless-stopped
volumes:
  xknx-editor-data:
```

## KNX bus access

Tunnelling to a gateway's IP works through the container network. Gateway auto-discovery and KNX
routing use multicast, which a bridged container does not receive — use `--network host` if you need
those, and keep the password set because the port is then on the host network.

## TrueNAS SCALE

On TrueNAS SCALE 24.10+ (its apps run on Docker Compose) add it under **Apps → Discover Apps →
Custom App** with this Compose YAML — no catalog entry needed. Create the two datasets first and
point the volumes at them.

```yaml
services:
  xknx-editor:
    image: ghcr.io/xknx/xknx-editor-web:latest
    ports:
      - "8080:8080"
    environment:
      XKNX_WEB_PASSWORD: choose-a-strong-password
    volumes:
      - /mnt/<pool>/apps/xknx-editor/data:/data
      - /mnt/<pool>/apps/xknx-editor/projects:/projects
    restart: unless-stopped
```

To run as a non-root user (dataset-permission hygiene), add `user: "568:568"` (the `apps` UID/GID)
and make those datasets owned by it — the app only writes `/data`, `/projects` and `/tmp`.

## Verifying an image

Every published image carries a signed build provenance: the GitHub Actions workflow of this
repository built it from the commit in its `org.opencontainers.image.revision` label. Check it with
the [GitHub CLI](https://cli.github.com/):

```bash
gh attestation verify oci://ghcr.io/xknx/xknx-editor-web:latest -R XKNX/xknx-editor
```

The attestations are listed under the repository's
[Attestations](https://github.com/XKNX/xknx-editor/attestations). Images up to version 0.6.1 have
no signed attestation yet.

## Building locally

```bash
# from the repository root
docker build -f docker/Dockerfile --target web -t xknx-editor-web:local .
```

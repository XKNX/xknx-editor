# XKNX Editor in a container

Two images are built from [`docker/Dockerfile`](./Dockerfile) by
[`.github/workflows/docker-image.yml`](../.github/workflows/docker-image.yml):

| Image | For | Entry |
| --- | --- | --- |
| `ghcr.io/xknx/xknx-editor-web` | plain `docker run` / Compose / any orchestrator, and the Unraid template | binds `0.0.0.0:8080`, needs a password; Unraid: see [`unraid`](../unraid) |
| `ghcr.io/xknx/xknx-editor-ha` | the Home Assistant add-on (behind Ingress) | see [`homeassistant/xknx-editor`](../homeassistant/xknx-editor) |

Both run the editor's browser UI (`--web`) with no desktop and carry the .NET SDK used for
signing-key extraction. The KNX authoring assembly is never shipped — you provide your own (below).

## Volumes

| Path | Holds |
| --- | --- |
| `/data` | app settings and the imported product catalog (keep on a named volume) |
| `/projects` | the browser's file folder: projects you open and save, exported `.knxproj` files (including signed ones), and any ETS assemblies you drop in for signing-key extraction |

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
- **`/data`** holds settings and the catalog — a named volume keeps them across upgrades.
- **`/projects`** is where the editor opens and saves files in the browser. Bind-mount a host folder
  (here `/host/xknx`) so you can drop files in and collect exports from the host.

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

If a proxy in front authenticates users and forwards to the container, start it with a trusted-proxy
flag instead of a password:

```bash
docker run -d --name xknx-editor \
  -p 127.0.0.1:8080:8080 \
  -v xknx-editor-data:/data -v /host/xknx:/projects \
  ghcr.io/xknx/xknx-editor-web:latest \
  python -m editor_gui.main --web=0.0.0.0:8080 --web-trust-proxy --web-no-browser --web-files=/projects
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

## Building locally

```bash
# from the repository root
docker build -f docker/Dockerfile --target web -t xknx-editor-web:local .
docker build -f docker/Dockerfile --target ha  -t xknx-editor-ha:local  .
```

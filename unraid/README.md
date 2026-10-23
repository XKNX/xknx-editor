# XKNX Editor — Unraid

Run the [XKNX Editor](https://github.com/XKNX/xknx-editor) in your browser on an Unraid server. The
editor opens, imports and exports KNX projects, browses the product catalog, and can commission
devices on a KNXnet/IP gateway. The template [`xknx-editor.xml`](xknx-editor.xml) runs the
`ghcr.io/xknx/xknx-editor-web` image described in [`docker/README.md`](../docker/README.md).

## Install

From **Community Applications**: open **Apps**, search for **XKNX Editor**, select it and click
**Install**. Set the password and the folders (below), then **Apply**.

Without Community Applications, copy [`xknx-editor.xml`](xknx-editor.xml) to
`/boot/config/plugins/dockerMan/templates-user/my-XKNX-Editor.xml` on the server, then choose
**Docker -> Add Container** and select **XKNX-Editor** as the template.

Open the container's **WebUI** and sign in with the password.

## Settings

| Setting | Default | Holds |
| --- | --- | --- |
| Web UI Port | `8080` | the port of the web UI on the server |
| Web Password | — (required) | the password every browser signs in with (`XKNX_WEB_PASSWORD`) |
| App Data | `/mnt/user/appdata/xknx-editor/data` | app settings and the imported product catalog (`/data`) |
| Projects | `/mnt/user/appdata/xknx-editor/projects` | the folder the editor opens and saves files in (`/projects`) |

The web UI is reachable from the whole network and can program the installation, so choose a strong
password and don't expose the port to the internet. To reach projects and exports from other
computers, point **Projects** to a share, e.g. `/mnt/user/knx`.

## KNX bus access

Tunnelling to a gateway by its IP address works with the default **Bridge** network. Gateway
auto-discovery and KNX routing use multicast, which a bridged container does not receive: set
**Network Type** to **Host** if you need those. The web UI then uses port 8080 of the server
directly.

Offline project editing, import and export need no bus access at all.

## Signing-key extraction (optional)

Exporting a project that other tools accept as signed needs a key extracted from an installation of
the KNX authoring tool you own. The key itself is never shipped. In the editor, open **Signing**,
then either upload `Knx.Ets.XmlSigning.dll` straight from your computer (the dialog's **Upload**
button, or drag-and-drop) or pick it from the file list after copying it into the **Projects**
folder. Include its sibling assemblies so dependencies resolve.

## Updates

The container uses the `latest` tag, so **Check for Updates** on the Docker tab pulls each new
release. The template itself updates from this repository, so changed settings show up on the next
edit of the container.

The template is based on the one Bjoern3003 wrote in
[Bjoern3003/xknx-editor-unraid](https://github.com/Bjoern3003/xknx-editor-unraid).

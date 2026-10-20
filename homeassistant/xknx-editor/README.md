# XKNX Editor — Home Assistant add-on

Run the [XKNX Editor](https://github.com/XKNX/xknx-editor) in your browser from the Home Assistant
sidebar. The editor opens, imports and exports KNX projects, browses the product catalog, and can
commission devices on a KNXnet/IP gateway.

## Install

1. In Home Assistant go to **Settings -> Add-ons -> Add-on Store**.
2. Open the three-dot menu (top right) -> **Repositories** and add:
   `https://github.com/XKNX/xknx-editor`
3. Install **XKNX Editor** from the list, then **Start** it and open **Web UI** (or the sidebar
   panel).

The add-on is served through Home Assistant Ingress — no password, no extra port; Home Assistant
signs you in.

## Data

Settings and the product catalog live in the add-on's persistent storage. Projects, exports and the
browser's working files go to `/share/xknx-editor`, so you can reach them from the host. Both
survive restarts and updates.

## Signing-key extraction (optional)

Exporting a project that other tools accept as signed needs a key extracted from an installation of
the KNX authoring tool you own. The key itself is never shipped. In the editor, open **Signing**,
then either upload `Knx.Ets.XmlSigning.dll` straight from your computer (the dialog's **Upload**
button, or drag-and-drop) or pick it from the file list after copying it into `/share/xknx-editor`.
Include its sibling assemblies so dependencies resolve.

See [DOCS.md](DOCS.md) for details and limitations.

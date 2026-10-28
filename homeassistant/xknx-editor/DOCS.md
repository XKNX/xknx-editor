# XKNX Editor — documentation

## What this add-on does

Runs the XKNX Editor without a desktop, drawing its interface in your browser. Home Assistant
Ingress puts it in the sidebar and authenticates you, so there is no separate login or exposed port.

## KNX bus access

The editor talks to a KNXnet/IP gateway over the network. Because the add-on runs on Home
Assistant's container network:

- **Tunnelling to a known gateway IP works.** Enter the gateway's address in the connection
  settings.
- **Gateway auto-discovery and KNX routing use multicast**, which does not cross the container
  bridge. If you rely on either, run a gateway/router you can address by IP and use tunnelling.

Offline project editing, import and export need no bus access at all.

## Persistent data

The add-on's `/data` holds the editor's settings and the imported product catalog; it persists
across restarts and updates. The projects you open and save, exported `.knxproj`/`.xknx` files
(including signed ones) and the browser's working files live in Home Assistant's **share** folder
under `/share/xknx-editor`, so you can reach them from the host (for example through the Samba or
File editor add-ons).

## Signing-key extraction

A project exported by the editor can be signed so other KNX tools accept it. The signing key is not
distributed; you extract it from an installation of the KNX authoring tool that you own. The add-on
image carries the .NET 10 runtime needed to read the assembly, but never the assembly itself.

In the editor, open **Signing** and give it the assembly in whichever way suits you:

- **Upload it from your computer** — the dialog's **Upload** button, or drag the file onto the page.
  Nothing needs to be mounted.
- **Pick it from `/share/xknx-editor`** — copy the files there first (for example through the Samba
  or File editor add-on), then select `Knx.Ets.XmlSigning.dll` from the list.

Either way, include `Knx.Ets.XmlSigning.dll` with its sibling assemblies so dependencies resolve
(and, for project-log comment decryption, `Knx.Ets.Common.dll` with its siblings).

The first extraction compiles a small reflection helper; the image pre-warms the build caches so it
runs quickly and without needing internet access.

## Notes

- The published container images on GHCR must be public for the Supervisor to pull them without
  credentials.
- `version` in `config.yaml` tracks the app version and the pushed image tag; updating the add-on
  pulls the matching image.

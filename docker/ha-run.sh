#!/usr/bin/env sh
# Home Assistant add-on entrypoint. Ingress authenticates the user and reverse-proxies to the editor,
# so no sign-in password is set; --web-trust-proxy accepts the forwarded requests. App settings and
# the catalog persist under HA's /data mount (XDG_DATA_HOME=/data, set in the image). The browser's
# file folder is Home Assistant's shared folder, so projects, exported .knxproj files (including
# signed ones) and any ETS assemblies dropped in for signing-key extraction are reachable from the
# host (Samba / File editor). SIGTERM from the supervisor stops the app like File > Exit.
set -eu
mkdir -p /share/xknx-editor
# Bind every interface (the Supervisor reaches us over the add-on network) but answer only the
# Ingress proxy at 172.30.32.2, so no other add-on on the host can reach the editor unauthenticated.
exec python -m editor_gui.main \
  --web=0.0.0.0:8080 --web-trust-proxy --web-proxy-ip=172.30.32.2 --web-no-browser \
  --web-files=/share/xknx-editor

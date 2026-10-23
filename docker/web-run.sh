#!/usr/bin/env sh
# Entrypoint of the web image, started by tini. Runs the browser UI editor, optionally as PUID/PGID.
#
# PUID / PGID: the user and group to run as. Without PUID, or with PUID=0, it runs as root. With them,
# /data is handed to that user and group, and the files the editor writes to a bind-mounted /projects
# belong to them on the host (e.g. 1000, or 99:100 on Unraid). PGID defaults to PUID. UMASK sets the
# permissions of new files (e.g. 002, or 000 on Unraid). Started as a non-root user already
# (`docker run --user`, Kubernetes), it runs the editor as is.
#
# Serves the browser UI on XKNX_WEB_PORT (default 8080). On a bridge network remap with
# `-p <host>:8080`; set XKNX_WEB_PORT only for a different in-container bind port, e.g. with
# `--network host`, where there is no `-p` remapping. Optional, for running behind your own reverse
# proxy that does auth/TLS:
#   XKNX_WEB_TRUST_PROXY=1   accept forwarded requests without a sign-in password
#   XKNX_WEB_PROXY_IP=<ip>   answer only that proxy's address (pair with trust-proxy)
# A network-reachable bind still needs XKNX_WEB_PASSWORD unless a trusted proxy signs users in.
# SIGTERM from `docker stop` ends the app like File > Exit.
set -eu

if [ -n "${UMASK:-}" ]; then
  umask "$UMASK"
fi

# Safe by default: a container always binds a network interface, so refuse to start an unprotected
# editor. Require a sign-in password, or an explicit trusted reverse proxy that authenticates for us.
if [ -z "${XKNX_WEB_PASSWORD:-}" ] && [ "${XKNX_WEB_TRUST_PROXY:-}" != "1" ]; then
  echo "XKNX Editor: refusing to start a network-reachable editor with no protection." >&2
  echo "Set XKNX_WEB_PASSWORD, or XKNX_WEB_TRUST_PROXY=1 behind an authenticating reverse proxy." >&2
  exit 2
fi

set -- --web="0.0.0.0:${XKNX_WEB_PORT:-8080}" --web-no-browser --web-files=/projects
if [ "${XKNX_WEB_TRUST_PROXY:-}" = "1" ]; then set -- "$@" --web-trust-proxy; fi
if [ -n "${XKNX_WEB_PROXY_IP:-}" ]; then set -- "$@" --web-proxy-ip="$XKNX_WEB_PROXY_IP"; fi

uid=${PUID:-0}
gid=${PGID:-$uid}
# Already non-root, or asked to stay root: run the editor as the current user.
if [ "$(id -u)" != 0 ] || [ "$uid" = 0 ]; then
  exec python -m editor_gui.main "$@"
fi
for id in "$uid" "$gid"; do
  case "$id" in
    '' | *[!0-9]*)
      echo "PUID and PGID must be numbers, got PUID='$uid' PGID='$gid'" >&2
      exit 1
      ;;
  esac
done

# /data is the app's own folder (settings, catalog, cache, logs): it follows PUID/PGID, also after a
# change of them or an upgrade from the root-only image. The rest of the image stays root's.
if [ "$(stat -c %u:%g /data)" != "$uid:$gid" ]; then
  echo "Handing /data to $uid:$gid"
  chown -R "$uid:$gid" /data
fi
# /projects holds the user's files, so it is only handed over while empty - as Docker creates a
# missing host folder or a new volume, owned by root - and otherwise just checked.
if [ -d /projects ] && [ -z "$(ls -A /projects)" ] \
  && [ "$(stat -c %u:%g /projects)" != "$uid:$gid" ]; then
  echo "Handing the empty /projects to $uid:$gid"
  chown "$uid:$gid" /projects
fi
if [ -d /projects ] && ! setpriv --reuid="$uid" --regid="$gid" --clear-groups test -w /projects; then
  echo "Warning: $uid:$gid can't write to /projects - change the owner of its host folder" >&2
fi

# HOME is root's; everything the editor keeps goes to XDG_* under /data.
export HOME=/tmp
exec setpriv --reuid="$uid" --regid="$gid" --clear-groups python -m editor_gui.main "$@"

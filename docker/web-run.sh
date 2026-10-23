#!/usr/bin/env sh
# Entrypoint of the web image, started by tini with the command to run (by default the editor).
#
# PUID / PGID: the user and group to run it as. Without PUID, or with PUID=0, it runs as root as
# before. With them, /data is handed to that user and group, and the files the editor writes to a
# bind-mounted /projects belong to them on the host (e.g. 1000, or 99:100 on Unraid). PGID defaults
# to PUID. UMASK sets the permissions of new files (e.g. 002, or 000 on Unraid).
#
# Started as a non-root user already (`docker run --user`, Kubernetes), it runs the command as is.
set -eu

if [ -n "${UMASK:-}" ]; then
  umask "$UMASK"
fi

uid=${PUID:-0}
gid=${PGID:-$uid}
if [ "$(id -u)" != 0 ] || [ "$uid" = 0 ]; then
  exec "$@"
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
exec setpriv --reuid="$uid" --regid="$gid" --clear-groups "$@"

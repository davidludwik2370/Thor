#!/bin/sh
# Disposable container preparation only; never run on a user's OS.
set -eu
[ -f /.dockerenv ] || { echo 'This preparation is restricted to a disposable Docker container.' >&2; exit 1; }
if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends sudo curl ca-certificates git xvfb xauth dbus-x11 procps fonts-dejavu-core
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y sudo ca-certificates git xorg-x11-server-Xvfb xorg-x11-xauth dbus-x11 procps-ng dejavu-sans-fonts
    if ! command -v curl >/dev/null 2>&1; then dnf install -y curl; fi
else
    echo 'Unsupported compatibility test distribution.' >&2; exit 1
fi
useradd -m -s /bin/bash thor
printf 'thor ALL=(ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/thor-validation
chmod 440 /etc/sudoers.d/thor-validation
# Only this test container needs the Electron user-namespace workaround.
# A missing editor still fails --version, allowing automatic acquisition.
printf '#!/bin/sh\nexec /usr/share/code/bin/code --no-sandbox "$@"\n' > /usr/local/bin/code
chmod 755 /usr/local/bin/code

FROM ubuntu:24.04
ARG CODE_ARCH=arm64
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    ca-certificates curl clang xvfb xauth dbus-x11 libasound2t64 libatk-bridge2.0-0 \
    libgtk-3-0t64 libnss3 libxss1 libgbm1 libxshmfence1 libx11-xcb1 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && curl --fail --location "https://update.code.visualstudio.com/latest/linux-${CODE_ARCH}/stable" -o /tmp/code.tar.gz \
    && mkdir /opt/vscode && tar -xzf /tmp/code.tar.gz -C /opt/vscode --strip-components=1 \
    && rm /tmp/code.tar.gz \
    && useradd -m -s /bin/bash thor
# Electron's sandbox cannot initialize in this test container's user namespace.
# This wrapper affects only this isolated GUI test, not installed user settings.
RUN printf '#!/bin/sh\nexec /opt/vscode/bin/code --no-sandbox "$@"\n' > /usr/local/bin/code && chmod 755 /usr/local/bin/code
USER thor
ENV HOME=/home/thor DISPLAY=:99 SHELL=/bin/bash
WORKDIR /home/thor

#!/bin/sh
# Clean GUI-capable Ubuntu installation test. No remote machines are used.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bundle=${1:?Usage: tools/test_install_ubuntu.sh /absolute/path/to/native-bundle.tar.gz}
case "$(docker info --format '{{.Architecture}}')" in aarch64|arm64) arch=arm64;; x86_64|amd64) arch=x64;; *) echo 'Unsupported Docker architecture' >&2; exit 1;; esac
name="thor-install-ubuntu-$$"
image="thor-install-ubuntu-test:$arch"
artifacts=$(CDPATH= cd -- "$(dirname -- "$bundle")" && pwd)
artifact=$(basename "$bundle")
docker build --build-arg "CODE_ARCH=$arch" -t "$image" "$root/tools/install-ubuntu"
mkdir -p "$root/dist/ubuntu-validation"
finish() {
    docker cp "$name:/home/thor/logs/." "$root/dist/ubuntu-validation/" >/dev/null 2>&1 || :
    docker rm -f "$name" >/dev/null 2>&1 || :
}
trap finish EXIT HUP INT TERM
docker run -d --name "$name" --memory 4g --memory-swap 4g --cpus 2 \
    -v "$artifacts:/artifacts:ro" -v "$root:/source:ro" "$image" sleep infinity
docker exec "$name" sh -c 'mkdir -p /home/thor/logs; nohup Xvfb :99 -screen 0 1280x800x24 >/home/thor/logs/xvfb.log 2>&1 </dev/null &'
docker exec -e "THOR_INSTALL_BUNDLE=/artifacts/$artifact" "$name" sh /source/tools/install-ubuntu/validate.sh
docker cp "$name:/home/thor/logs/." "$root/dist/ubuntu-validation/"
echo "Validation artifacts: $root/dist/ubuntu-validation"

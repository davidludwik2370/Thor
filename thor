#!/bin/sh
set -eu
thor_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$thor_root/thor-rust" "$@"

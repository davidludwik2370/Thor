#!/bin/sh
# Loaded only for an explicit source build lacking a usable system LLVM.
install_apt_llvm() {
    for llvm_major in 18 19 20 21 22 17; do
        if apt-cache policy "llvm-$llvm_major-dev" | grep -Eq 'Candidate: [^ (]'; then
            packages "llvm-$llvm_major-dev"
            return
        fi
    done
    # Older supported distributions ship LLVM 14/15. Use LLVM's own signed
    # release repository rather than installing an unusable llvm-dev package.
    os_release=${1:-/etc/os-release}
    repo_directory=${2:-/etc/apt/sources.list.d}
    key_directory=${3:-/usr/share/keyrings}
    distro=$(sed -n 's/^ID=//p' "$os_release" | tr -d '"')
    codename=$(sed -n 's/^VERSION_CODENAME=//p' "$os_release" | tr -d '"')
    case "$distro:$codename" in
        ubuntu:jammy|debian:bookworm|debian:bullseye) ;;
        *) echo 'No LLVM 17–22 development package is available. Install a compatible LLVM_CONFIG and rerun the source build.' >&2; return 1;;
    esac
    packages gnupg ca-certificates
    download https://apt.llvm.org/llvm-snapshot.gpg.key "$work/llvm-key.asc"
    mkdir -m 700 "$work/gnupg"
    fingerprint=$(GNUPGHOME="$work/gnupg" gpg --batch --show-keys --with-colons "$work/llvm-key.asc" | awk -F: '$1=="fpr" {print $10; exit}')
    if [ "$fingerprint" != 6084F3CF814B57C1CF12EFD515CF4D18AF4F7421 ]; then
        echo 'LLVM repository signing key does not match the reviewed fingerprint; no repository was changed.' >&2; return 1
    fi
    key="$key_directory/thor-llvm-18.asc"
    repo="$repo_directory/thor-llvm-18.list"
    printf 'deb [signed-by=%s] https://apt.llvm.org/%s/ llvm-toolchain-%s-18 main\n' "$key" "$codename" "$codename" > "$work/llvm-repository.list"
    # Check both destinations before either write. Never replace an unknown
    # existing repository or key. Package-manager approvals remain with sudo.
    if { [ -e "$key" ] && ! cmp -s "$key" "$work/llvm-key.asc"; } ||
       { [ -e "$repo" ] && ! cmp -s "$repo" "$work/llvm-repository.list"; }; then
        echo 'An existing Thor LLVM repository/key differs; it was preserved. Select a compatible LLVM_CONFIG instead.' >&2; return 1
    fi
    elevate install -m 644 "$work/llvm-key.asc" "$key"
    elevate install -m 644 "$work/llvm-repository.list" "$repo"
    packages llvm-18-dev
}

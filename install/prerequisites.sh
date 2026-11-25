#!/bin/sh
# Shared by the checkout bootstrap and native installer. No Python is needed
# for binary installation. OS approvals remain with the OS; never run Thor as root.
set -eu
PATH="$PATH:/usr/local/bin:/usr/pkg/bin:/usr/local/sbin:/usr/pkg/sbin"; export PATH
source_build=false; editor=false; plan=false; download_only=false
for arg do
    case "$arg" in --source) source_build=true;; --vscode) editor=true;; --dry-run) plan=true;; --download-only) download_only=true;; *) echo "Unknown prerequisite option: $arg" >&2; exit 1;; esac
done
system=$(uname -s)
machine=$(uname -m)
if [ "$system:$machine" = NetBSD:evbarm ]; then machine=$(uname -p); fi
case "$system:$machine" in
    Darwin:arm64|Darwin:x86_64|Linux:aarch64|Linux:arm64|Linux:x86_64|FreeBSD:amd64|FreeBSD:arm64|FreeBSD:aarch64|NetBSD:amd64|NetBSD:x86_64|NetBSD:aarch64|OpenBSD:amd64|OpenBSD:x86_64) ;;
    *) echo "Unsupported Odin host: $system $(uname -m)" >&2; exit 1;;
esac
if $plan; then
    echo "Prerequisites: $system; source build=$source_build; VS Code=$editor. Reuse working tools; install missing tools using the system package manager."
    exit 0
fi
elevate() {
    if [ "$(id -u)" -eq 0 ]; then "$@"
    elif command -v sudo >/dev/null 2>&1; then
        if [ -n "${SUDO_ASKPASS:-}" ]; then sudo -A "$@"; else sudo "$@"; fi
    elif command -v doas >/dev/null 2>&1; then doas "$@"
    else echo "Installing system prerequisites requires sudo/doas. Install the reported packages as administrator and rerun." >&2; exit 1; fi
}
packages() {
    case "$system" in
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                elevate apt-get update
                elevate env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"
            elif command -v dnf >/dev/null 2>&1; then elevate dnf install -y "$@"
            elif command -v zypper >/dev/null 2>&1; then elevate zypper --non-interactive install "$@"
            elif command -v pacman >/dev/null 2>&1; then elevate pacman -S --needed --noconfirm "$@"
            else echo "No supported package manager found. Install Clang, C/C++ development libraries and curl, then rerun." >&2; exit 1; fi;;
        FreeBSD) elevate env ASSUME_ALWAYS_YES=yes pkg install -y "$@";;
        NetBSD) if command -v pkgin >/dev/null 2>&1; then elevate pkgin -y install "$@"; else elevate pkg_add "$@"; fi;;
        OpenBSD) elevate pkg_add "$@";;
    esac
}
if ! command -v curl >/dev/null 2>&1; then
    case "$system" in Linux) packages curl ca-certificates;; Darwin) echo 'System curl is missing; repair macOS before continuing.' >&2; exit 1;; *) packages curl;; esac
fi
$download_only && exit 0
work=$(mktemp -d "${TMPDIR:-/tmp}/thor-prerequisites.XXXXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
download() { curl --fail --location --retry 2 --proto '=https' --proto-redir '=https' "$1" -o "$2"; }
if [ "$system" = Darwin ] && ! xcrun --find clang >/dev/null 2>&1; then
    if [ "${CI:-}" = true ]; then echo 'No usable Apple developer tools in CI. Select a preinstalled Xcode SDK before running setup.' >&2; exit 1; fi
    echo 'Installing Apple Command Line Tools. Complete the Apple installation/license dialog; this command will wait.'
    if ! xcode-select --install; then echo 'Apple could not start installation. Check xcode-select/DEVELOPER_DIR and rerun.' >&2; exit 1; fi
    while ! xcrun --find clang >/dev/null 2>&1; do sleep 3; done
fi
# Compile AND link: a clang executable alone does not establish SDK availability.
printf 'int main(void) {return 0;}\n' > "$work/check.c"
if ! clang "$work/check.c" -o "$work/check" >/dev/null 2>&1; then
    case "$system" in
        Linux)
            if command -v apt-get >/dev/null 2>&1; then packages clang build-essential ca-certificates
            elif command -v pacman >/dev/null 2>&1; then packages clang base-devel ca-certificates
            else packages clang gcc gcc-c++ make ca-certificates; fi;;
        FreeBSD|NetBSD|OpenBSD) packages clang;;
        Darwin) echo 'Apple tools were found but cannot link a program. Check xcode-select and the installed SDK.' >&2; exit 1;;
    esac
    clang "$work/check.c" -o "$work/check"
fi
"$work/check"
echo 'PASS: native C compiler and linker'
if $source_build; then
    supported_llvm() {
        [ -x "$1" ] || return 1
        llvm_version=$("$1" --version 2>/dev/null) || return 1
        case "${llvm_version%%.*}" in 17|18|19|20|21|22) return 0;; *) return 1;; esac
    }
    # Standard /usr/local paths are not always present in non-login BSD shells.
    PATH="$HOME/.cargo/bin:/usr/local/bin:/usr/pkg/bin:$PATH"; export PATH
    printf '#include <vector>\nint main() {std::vector<int> x; return (int)x.size();}\n' > "$work/check.cpp"
    if ! clang++ "$work/check.cpp" -o "$work/check-cxx" >/dev/null 2>&1; then
        case "$system" in
            Linux)
                if command -v apt-get >/dev/null 2>&1; then packages build-essential
                elif command -v pacman >/dev/null 2>&1; then packages base-devel
                else packages gcc gcc-c++ make; fi;;
            Darwin) echo 'The selected Apple SDK cannot compile C++. Repair Command Line Tools and rerun.' >&2; exit 1;;
            *) packages clang;;
        esac
        clang++ "$work/check.cpp" -o "$work/check-cxx"
    fi
    if [ "$system" = Darwin ]; then
        if ! command -v brew >/dev/null 2>&1; then
            download https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh "$work/homebrew.sh"
            /bin/bash "$work/homebrew.sh"
            for brew_path in /opt/homebrew/bin/brew /usr/local/bin/brew; do
                if [ -x "$brew_path" ]; then eval "$("$brew_path" shellenv)"; break; fi
            done
        fi
        if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then brew install python; fi
        for tool in node npm git; do command -v "$tool" >/dev/null 2>&1 || brew install "$(case "$tool" in npm) echo node;; *) echo "$tool";; esac)"; done
        llvm_found=false
        for candidate in /opt/homebrew/opt/llvm*/bin/llvm-config /usr/local/opt/llvm*/bin/llvm-config; do if supported_llvm "$candidate"; then llvm_found=true; fi; done
        if ! supported_llvm "$(command -v llvm-config || :)" && ! $llvm_found; then brew list llvm >/dev/null 2>&1 || brew install llvm; fi
    else
        if [ "$system" = Linux ] && ! command -v patchelf >/dev/null 2>&1; then packages patchelf; fi
        if ! command -v python3 >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1 || ! command -v make >/dev/null 2>&1; then
            case "$system" in
                Linux) packages python3 nodejs npm git make;;
                FreeBSD) packages python3 node npm git gmake;;
                NetBSD) packages python312 nodejs npm git gmake;;
                OpenBSD) packages python3 node npm git gmake;;
            esac
        fi
        llvm_found=false
        for candidate in /usr/local/llvm*/bin/llvm-config /usr/lib/llvm-*/bin/llvm-config; do if supported_llvm "$candidate"; then llvm_found=true; fi; done
        if ! supported_llvm "$(command -v llvm-config || :)" && ! $llvm_found; then
            case "$system" in FreeBSD) packages llvm19;; NetBSD) packages llvm;; OpenBSD) packages llvm;;
                Linux)
                    if command -v apt-get >/dev/null 2>&1; then packages llvm-dev lld
                    elif command -v pacman >/dev/null 2>&1; then packages llvm lld
                    else packages llvm-devel lld; fi;;
            esac
        fi
    fi
    if ! command -v cargo >/dev/null 2>&1; then
        case "$system" in
            FreeBSD|NetBSD|OpenBSD) packages rust;;
            *) download https://sh.rustup.rs "$work/rustup.sh"; sh "$work/rustup.sh" -y --profile minimal --no-modify-path;;
        esac
    fi
    echo 'PASS: source-build prerequisites'
fi
if $editor; then
    if command -v code >/dev/null 2>&1 || [ -x "$HOME/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" ] || [ -x '/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code' ] || [ -x /usr/share/code/bin/code ]; then
        echo 'PASS: existing VS Code reused'; exit 0
    fi
    case "$(uname -m)" in arm64|aarch64) arch=arm64;; *) arch=x64;; esac
    case "$system" in
        Darwin)
            download "https://update.code.visualstudio.com/latest/darwin-$arch/stable" "$work/code.zip"
            mkdir "$work/editor"; ditto -x -k "$work/code.zip" "$work/editor"
            codesign --verify --deep --strict "$work/editor/Visual Studio Code.app"
            mkdir -p "$HOME/Applications"; mv "$work/editor/Visual Studio Code.app" "$HOME/Applications/";;
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                download "https://update.code.visualstudio.com/latest/linux-deb-$arch/stable" "$work/code.deb"
                # Decline a new global repository; Code's package remains installed.
                printf 'code code/add-microsoft-repo boolean false\n' | elevate debconf-set-selections
                chmod 755 "$work"; chmod 644 "$work/code.deb"
                elevate env DEBIAN_FRONTEND=noninteractive apt-get install -y "$work/code.deb"
            elif command -v dnf >/dev/null 2>&1; then
                download "https://update.code.visualstudio.com/latest/linux-rpm-$arch/stable" "$work/code.rpm"
                elevate dnf install -y "$work/code.rpm"
            else
                echo 'Install VS Code from your distribution (or pass --code PATH), then rerun --vscode.' >&2; exit 1
            fi;;
        FreeBSD) packages vscode;;
        NetBSD|OpenBSD) echo 'The compiler supports this host, but automatic VS Code installation is unavailable. Install a compatible editor and pass --code PATH.' >&2; exit 1;;
    esac
    echo 'PASS: VS Code installed'
fi

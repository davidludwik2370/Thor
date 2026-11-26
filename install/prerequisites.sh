#!/bin/sh
# Shared by the checkout bootstrap and native installer. No Python is needed
# for binary installation. OS approvals remain with the OS; never run Thor as root.
set -eu
PATH="$PATH:/usr/local/bin:/usr/pkg/bin:/usr/local/sbin:/usr/pkg/sbin"; export PATH
source_build=false; editor=false; plan=false; download_only=false; reuse_odin=false
for arg do
    case "$arg" in --source) source_build=true;; --reuse-odin) reuse_odin=true;; --vscode) editor=true;; --dry-run) plan=true;; --download-only) download_only=true;; *) echo "Unknown prerequisite option: $arg" >&2; exit 1;; esac
done
system=$(uname -s)
machine=$(uname -m)
if [ "$system:$machine" = NetBSD:evbarm ]; then machine=$(uname -p); fi
case "$system:$machine" in
    Darwin:arm64|Darwin:x86_64|Linux:aarch64|Linux:arm64|Linux:x86_64|FreeBSD:amd64|FreeBSD:arm64|FreeBSD:aarch64|NetBSD:amd64|NetBSD:x86_64|NetBSD:aarch64|OpenBSD:amd64|OpenBSD:x86_64) ;;
    *) echo "Unsupported Odin host: $system $(uname -m)" >&2; exit 1;;
esac
if [ "$system" = Darwin ]; then
    # Reuse existing Homebrew tools without installing or upgrading packages.
    PATH="$PATH:/opt/homebrew/bin"; export PATH
fi
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
apt_update() {
    # apt's dpkg timeout covers installation locks, but not its package-list
    # lock. Retry only lock contention, preserving other update failures.
    apt_log=$(mktemp "${TMPDIR:-/tmp}/thor-apt.XXXXXXXX")
    apt_deadline=$(($(date +%s) + 300))
    while :; do
        if elevate env LC_ALL=C apt-get -o DPkg::Lock::Timeout=300 update >"$apt_log" 2>&1; then
            cat "$apt_log"; rm -f "$apt_log"; return 0
        else apt_status=$?; fi
        cat "$apt_log"
        if [ "$apt_status" -ne 100 ] || ! grep -Eq 'Could not get lock|Unable to lock directory' "$apt_log" || [ "$(date +%s)" -ge "$apt_deadline" ]; then
            rm -f "$apt_log"; return "$apt_status"
        fi
        echo 'Waiting for the system package-list lock...'
        sleep 2
    done
}
packages() {
    case "$system" in
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                apt_update
                # Fresh Ubuntu desktops often run unattended upgrades at login.
                # Let apt wait for the frontend lock instead of failing setup.
                elevate env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends "$@"
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
code_backup=; code_target=; code_stage=
cleanup() {
    status=$?
    trap - EXIT
    if [ -n "$code_backup" ] && [ -d "$code_backup" ] && [ ! -e "$code_target" ]; then
        app_move "$code_backup" "$code_target" || echo "Restore VS Code manually from $code_backup" >&2
    fi
    if [ -n "$code_stage" ] && [ -d "$code_stage" ]; then app_remove "$code_stage" || echo "Remove incomplete VS Code staging path manually: $code_stage" >&2; fi
    rm -rf "$work"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
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
    if ! $reuse_odin && ! clang++ "$work/check.cpp" -o "$work/check-cxx" >/dev/null 2>&1; then
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
        # CLT already supplies Python 3.9, Git and native build tools. Node,
        # Rust and missing LLVM come from verified private binary downloads in
        # from_source.py. Never bootstrap Homebrew or compile its tool chains.
        python_found=false
        for candidate in python3 python3.15 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 /usr/bin/python3; do
            if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then python_found=true; break; fi
        done
        if ! $python_found; then echo 'Apple developer tools did not provide Python 3.9+. Install a binary Python from python.org and rerun.' >&2; exit 1; fi
        if ! $reuse_odin && ! command -v git >/dev/null 2>&1; then echo 'Apple developer tools did not provide Git. Repair Command Line Tools and rerun.' >&2; exit 1; fi
    else
        if [ "$system" = Linux ]; then
            set --
            for tool in patchelf python3; do command -v "$tool" >/dev/null 2>&1 || set -- "$@" "$tool"; done
            if ! $reuse_odin; then
                for tool in git make; do command -v "$tool" >/dev/null 2>&1 || set -- "$@" "$tool"; done
            fi
            if [ "$#" -gt 0 ]; then packages "$@"; fi
        elif ! command -v python3 >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1 || { ! $reuse_odin && { ! command -v git >/dev/null 2>&1 || ! command -v gmake >/dev/null 2>&1; }; }; then
            case "$system" in
                FreeBSD) packages python3 node npm git gmake;;
                NetBSD) packages python312 nodejs npm git gmake;;
                OpenBSD) packages python3 node npm git gmake;;
            esac
        fi
        llvm_found=false
        for candidate in /usr/local/llvm*/bin/llvm-config /usr/lib/llvm-*/bin/llvm-config; do if supported_llvm "$candidate"; then llvm_found=true; fi; done
        if ! $reuse_odin && ! supported_llvm "${LLVM_CONFIG:-$(command -v llvm-config || :)}" && ! $llvm_found; then
            case "$system" in FreeBSD) packages llvm19;; NetBSD) packages llvm;; OpenBSD) packages llvm;;
                Linux)
                    if command -v apt-get >/dev/null 2>&1; then
                        script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
                        . "$script_dir/llvm-apt.sh"
                        install_apt_llvm
                    elif command -v pacman >/dev/null 2>&1; then packages llvm
                    else packages llvm-devel; fi;;
            esac
        fi
    fi
    if ! command -v cargo >/dev/null 2>&1; then
        case "$system" in
            FreeBSD|NetBSD|OpenBSD) packages rust;;
        esac
    fi
    echo 'PASS: source-build prerequisites'
fi
if $editor; then
    script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    minimum_code=$(cat "$script_dir/vscode-minimum")
    version_at_least() {
        awk -v current="$1" -v minimum="$2" 'BEGIN {
            sub(/-.*/, "", current);
            if (current !~ /^[0-9]+(\.[0-9]+)?(\.[0-9]+)?$/) exit 1;
            split(current,a,"."); split(minimum,b,".");
            for(i=1;i<=3;i++){if(a[i]+0>b[i]+0) exit 0; if(a[i]+0<b[i]+0) exit 1;} exit 0;
        }'
    }
    usable_code() {
        [ -x "$1" ] || return 1
        code_output=$("$1" --version 2>/dev/null) || return 1
        code_version=$(printf '%s\n' "$code_output" | head -n 1)
        version_at_least "$code_version" "$minimum_code"
    }
    for candidate in "$(command -v code || :)" "$HOME/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" '/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code' /usr/share/code/bin/code; do
        if usable_code "$candidate"; then
            echo "PASS: existing VS Code $code_version reused ($candidate)"; exit 0
        fi
    done
    echo "Installing/upgrading VS Code; Thor editor support requires version $minimum_code or newer."
    case "$(uname -m)" in arm64|aarch64) arch=arm64;; *) arch=x64;; esac
    case "$system" in
        Darwin)
            # An old running Code process would still receive new CLI requests
            # over its IPC socket after replacement. Never close user windows.
            code_running() { ps -axo command= | grep -E '/Visual Studio Code[.]app/Contents/MacOS/(Electron|Code)([[:space:]]|$)' >/dev/null; }
            if code_running; then
                echo 'Save your work and quit VS Code, then rerun from the macOS Terminal app. The running editor is too old for Thor; no editor files were replaced.' >&2; exit 1
            fi
            release=latest
            current_macos=$(sw_vers -productVersion)
            if ! version_at_least "$current_macos" '15.0.0'; then
                # Use a reviewed release rather than allowing latest to advance
                # past Ventura/Sonoma support. Inspect the downloaded app too.
                release=$(cat "$script_dir/vscode-macos-version")
            fi
            download "https://update.code.visualstudio.com/$release/darwin-$arch/stable" "$work/code.zip"
            mkdir "$work/editor"; ditto -x -k "$work/code.zip" "$work/editor"
            app="$work/editor/Visual Studio Code.app"
            codesign --verify --deep --strict "$app"
            app_minimum=$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$app/Contents/Info.plist")
            # sw_vers/Info.plist commonly omit trailing zero components.
            if ! awk -v current="$current_macos" -v minimum="$app_minimum" 'BEGIN {
                split(current,a,".");split(minimum,b,".");
                for(i=1;i<=3;i++){if(a[i]+0>b[i]+0)exit 0;if(a[i]+0<b[i]+0)exit 1;}exit 0;
            }'; then
                echo "The downloaded VS Code requires macOS $app_minimum (this Mac: $current_macos). The existing editor was preserved; select a compatible --code PATH." >&2; exit 1
            fi
            usable_code "$app/Contents/Resources/app/bin/code" || { echo 'Downloaded VS Code failed its version check; existing editor preserved.' >&2; exit 1; }
            if code_running; then
                echo 'VS Code was opened during the download. Save your work, quit it, and rerun; the existing application was preserved.' >&2; exit 1
            fi
            target="$HOME/Applications/Visual Studio Code.app"
            if [ ! -d "$target" ] && [ -d '/Applications/Visual Studio Code.app' ]; then target='/Applications/Visual Studio Code.app'; fi
            [ ! -L "$target" ] || { echo 'VS Code app is a symlink; update its target manually or select --code PATH.' >&2; exit 1; }
            parent=$(dirname "$target"); mkdir -p "$parent"
            app_move() { if [ -w "$parent" ]; then mv "$@"; else elevate mv "$@"; fi; }
            app_remove() { if [ -w "$1" ]; then rm -rf "$1"; else elevate rm -rf "$1"; fi; }
            # Move the existing application aside until the new one verifies.
            # Settings and extensions are outside the .app and are untouched.
            backup="$parent/.thor-code-previous-$$.app"
            if [ -e "$backup" ]; then echo "VS Code backup already exists: $backup" >&2; exit 1; fi
            stage="$parent/.thor-code-new-$$.app"
            if [ -e "$stage" ]; then echo "VS Code staging path already exists: $stage" >&2; exit 1; fi
            code_stage=$stage
            app_move "$app" "$stage"
            code_backup=$backup; code_target=$target
            had_previous=false
            if [ -d "$target" ]; then app_move "$target" "$backup"; had_previous=true; fi
            if ! app_move "$stage" "$target" || ! usable_code "$target/Contents/Resources/app/bin/code"; then
                if $had_previous; then
                    if [ -d "$target" ]; then app_move "$target" "$work/failed-code.app"; fi
                    app_move "$backup" "$target"
                fi
                echo 'VS Code upgrade failed; the previous application was restored when present.' >&2; exit 1
            fi
            if $had_previous; then app_remove "$backup"; fi
            code_backup=; code_stage=;;
        Linux)
            if ps -axo comm= | grep -E '(^|/)code$' >/dev/null; then
                echo 'Save your work and quit VS Code, then rerun from another terminal to upgrade the old editor.' >&2; exit 1
            fi
            if command -v apt-get >/dev/null 2>&1; then
                download "https://update.code.visualstudio.com/latest/linux-deb-$arch/stable" "$work/code.deb"
                # Decline a new global repository; Code's package remains installed.
                printf 'code code/add-microsoft-repo boolean false\n' | elevate debconf-set-selections
                chmod 755 "$work"; chmod 644 "$work/code.deb"
                elevate env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y "$work/code.deb"
            elif command -v dnf >/dev/null 2>&1; then
                download "https://update.code.visualstudio.com/latest/linux-rpm-$arch/stable" "$work/code.rpm"
                elevate dnf install -y "$work/code.rpm"
            else
                echo 'Install VS Code from your distribution (or pass --code PATH), then rerun --vscode.' >&2; exit 1
            fi;;
        FreeBSD) packages vscode;;
        NetBSD|OpenBSD) echo 'The compiler supports this host, but automatic VS Code installation is unavailable. Install a compatible editor and pass --code PATH.' >&2; exit 1;;
    esac
    for candidate in "$(command -v code || :)" "$HOME/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" '/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code' /usr/share/code/bin/code; do
        if usable_code "$candidate"; then echo "PASS: VS Code $code_version installed ($candidate)"; exit 0; fi
    done
    echo "VS Code installation did not provide version $minimum_code or newer; update it and rerun --vscode." >&2; exit 1
fi

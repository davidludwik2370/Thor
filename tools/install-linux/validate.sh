#!/bin/sh
set -eu
for tool in cargo rustc node npm; do
    if command -v "$tool" >/dev/null 2>&1; then echo "Unexpected developer dependency on PATH: $tool" >&2; exit 1; fi
done
[ ! -x /usr/share/code/bin/code ] || { echo 'Editor was already installed before the fresh-install test.' >&2; exit 1; }
mkdir -p "$HOME/logs" "$HOME/.config/Code/User"
cat /etc/os-release > "$HOME/logs/os-release"
getconf GNU_LIBC_VERSION > "$HOME/logs/libc-version.txt"
printf '%s\n' '{"workbench.colorTheme":"Default Light Modern","files.autoSave":"off","editor.semanticTokenColorCustomizations":{"rules":{"variable:python":"#112233"}}}' > "$HOME/.config/Code/User/settings.json"
printf '%s\n' '[{"key":"ctrl+r","command":"workbench.action.files.openFile","when":"editorLangId == python"}]' > "$HOME/.config/Code/User/keybindings.json"
cp "$HOME/.config/Code/User/settings.json" "$HOME/logs/default-settings.before.json"
cp "$HOME/.config/Code/User/keybindings.json" "$HOME/logs/default-keybindings.before.json"
git clone --depth 1 https://github.com/davidludwik2370/Thor.git "$HOME/Thor" > "$HOME/logs/clone.log" 2>&1
git -C "$HOME/Thor" rev-parse HEAD > "$HOME/logs/source-commit.txt"
cd "$HOME/Thor"
set -- --vscode --profile Thor --path
if [ -n "${THOR_INSTALL_BUNDLE:-}" ]; then set -- "$@" --bundle "$THOR_INSTALL_BUNDLE"; fi
./thor-install "$@" > "$HOME/logs/install.log" 2>&1
./thor-install "$@" > "$HOME/logs/reinstall.log" 2>&1
thor="$HOME/.local/share/thor/bin/thor"
"$HOME/.local/share/thor/bin/thor-install" --doctor > "$HOME/logs/doctor.log" 2>&1
for mode in -lic -ic; do
    env -i HOME="$HOME" SHELL=/bin/bash PATH=/usr/local/bin:/usr/bin:/bin /bin/bash "$mode" 'command -v thor; thor version; thor-install --doctor' > "$HOME/logs/shell${mode}.log" 2>&1
done
# VS Code extension tests need a fresh application process. Close only the
# blank profile-creation window in this disposable container, never host VS Code.
for pid in $(ps -u "$(id -u)" -o pid=,args= | awk '$2 == "/usr/share/code/code" && $3 == "--no-sandbox" {print $1}'); do
    kill -TERM "$pid"
done
mkdir -p "$HOME/work"
cd "$HOME/work"
"$thor" new editor --gitignore
cp /source/tools/install-ubuntu/fixture.thor "$HOME/work/editor/editor.thor"
"$thor" run "$HOME/work/editor" -o:speed -disable-assert -no-bounds-check -no-type-assert >"$HOME/logs/unchecked.log" 2>&1
if [ "$(cat "$HOME/work/ran.txt")" != ok ]; then echo 'Unchecked build produced the wrong result' >&2; exit 1; fi
"$thor" vet "$HOME/work/editor" >"$HOME/logs/vet.log" 2>&1
mkdir -p "$HOME/work/native-tests"
cat >"$HOME/work/native-tests/main_test.thor" <<'THOR'
package tests
import "core:testing"
@(test) smoke :: proc(t: ^testing.T) {
    _ = t
    values~ :> [x*x for x in 0..<4]
    if values[3] != 9 { panic("installed test mode failed") }
}
THOR
"$thor" test "$HOME/work/native-tests" -thread-count:2 >"$HOME/logs/test.log" 2>&1
# Add only a test-local launch setting; normal installation does not edit projects.
mkdir -p "$HOME/work/editor/.vscode"
cat >"$HOME/work/editor/.vscode/launch.json" <<'JSON'
{"version":"0.2.0","configurations":[{"name":"Installed Thor","type":"thor-lldb","request":"launch","cwd":"${workspaceFolder}","program":"${workspaceFolder}/.vscode/bin/debug","manifest":"${workspaceFolder}/.vscode/bin/generated/thor-manifest.json","preLaunchTask":"build-thor-debug","initCommands":["settings set target.disable-aslr false"]}]}
JSON
cp -R /source/tools/install-ubuntu/verification-extension "$HOME/verification-extension"
THOR_INSTALL_GUI_RESULT="$HOME/logs/editor-result.json" timeout 180s code --wait --new-window --profile Thor \
    --user-data-dir "$HOME/.config/Code" --extensions-dir "$HOME/.vscode/extensions" \
    --disable-workspace-trust --skip-welcome --skip-release-notes \
    --extensionDevelopmentPath="$HOME/verification-extension" \
    --extensionTestsPath="$HOME/verification-extension/test.js" "$HOME/work/editor" >"$HOME/logs/editor.log" 2>&1
cat "$HOME/logs/editor-result.json"
grep -q '"success": true' "$HOME/logs/editor-result.json"
if [ "$(sha256sum < "$HOME/.config/Code/User/settings.json")" != "$(sha256sum < "$HOME/logs/default-settings.before.json")" ] || \
   [ "$(sha256sum < "$HOME/.config/Code/User/keybindings.json")" != "$(sha256sum < "$HOME/logs/default-keybindings.before.json")" ]; then
    echo 'Default profile configuration was modified' >&2; exit 1
fi
echo 'Default profile settings/keybindings preserved byte-for-byte.'

cd "$HOME/Thor"
./thor-install --uninstall > "$HOME/logs/uninstall.log" 2>&1
./thor-install --uninstall > "$HOME/logs/uninstall-again.log" 2>&1
[ ! -e "$HOME/.local/share/thor/bin/thor" ] || { echo 'Uninstall retained the installed command.' >&2; exit 1; }
[ -f "$HOME/work/editor/editor.thor" ] || { echo 'Uninstall removed a project source.' >&2; exit 1; }
if grep -q '>>> thor-install PATH >>>' "$HOME/.profile" "$HOME/.bashrc"; then echo 'Uninstall retained PATH edits.' >&2; exit 1; fi
if [ -n "${THOR_INSTALL_BUNDLE:-}" ]; then
    ./thor-install --bundle "$THOR_INSTALL_BUNDLE" --prefix "$HOME/Thor Ünicode tools" --path > "$HOME/logs/unicode-prefix-install.log" 2>&1
    "$HOME/Thor Ünicode tools/bin/thor-install" --doctor > "$HOME/logs/unicode-prefix-doctor.log" 2>&1
    ./thor-install --prefix "$HOME/Thor Ünicode tools" --uninstall > "$HOME/logs/unicode-prefix-uninstall.log" 2>&1
fi
printf '%s\n' '{"success":true,"fresh_os_container":true,"automatic_prerequisites":true,"automatic_vscode":true,"ordinary_terminal_path":true,"reinstall":true,"uninstall":true,"projects_preserved":true}' > "$HOME/logs/compatibility-result.json"

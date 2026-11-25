#!/bin/sh
set -eu
for tool in cargo rustc node python3; do
    if command -v "$tool" >/dev/null 2>&1; then echo "Unexpected developer dependency on PATH: $tool" >&2; exit 1; fi
done
mkdir -p "$HOME/bootstrap/release" "$HOME/logs"
mkdir -p "$HOME/editor-user/User"
printf '%s\n' '{"workbench.colorTheme":"Default Light Modern","files.autoSave":"off","editor.semanticTokenColorCustomizations":{"rules":{"variable:python":"#112233"}}}' > "$HOME/editor-user/User/settings.json"
printf '%s\n' '[{"key":"ctrl+r","command":"workbench.action.files.openFile","when":"editorLangId == python"}]' > "$HOME/editor-user/User/keybindings.json"
cp "$HOME/editor-user/User/settings.json" "$HOME/logs/default-settings.before.json"
cp "$HOME/editor-user/User/keybindings.json" "$HOME/logs/default-keybindings.before.json"
cp /source/thor-install "$HOME/bootstrap/thor-install"
cp /source/release/version "$HOME/bootstrap/release/version"
chmod +x "$HOME/bootstrap/thor-install"
"$HOME/bootstrap/thor-install" --bundle "$THOR_INSTALL_BUNDLE" --vscode --profile Thor --path \
    --code /usr/local/bin/code --code-user-data "$HOME/editor-user" --extensions-dir "$HOME/editor-extensions" >"$HOME/logs/install.log" 2>&1
"$HOME/bootstrap/thor-install" --bundle "$THOR_INSTALL_BUNDLE" --vscode --profile Thor --path \
    --code /usr/local/bin/code --code-user-data "$HOME/editor-user" --extensions-dir "$HOME/editor-extensions" >"$HOME/logs/reinstall.log" 2>&1
thor="$HOME/.local/share/thor/bin/thor"
"$HOME/.local/share/thor/bin/thor-install" --doctor >"$HOME/logs/doctor.log" 2>&1
# VS Code extension tests need a fresh application process. Close only the
# blank profile-creation window in this disposable container, never host VS Code.
for pid in $(ps -u "$(id -u)" -o pid=,args= | awk '$2 == "/opt/vscode/code" && $3 == "--no-sandbox" {print $1}'); do
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
    --user-data-dir "$HOME/editor-user" --extensions-dir "$HOME/editor-extensions" \
    --disable-workspace-trust --skip-welcome --skip-release-notes \
    --extensionDevelopmentPath="$HOME/verification-extension" \
    --extensionTestsPath="$HOME/verification-extension/test.js" "$HOME/work/editor" >"$HOME/logs/editor.log" 2>&1
cat "$HOME/logs/editor-result.json"
grep -q '"success": true' "$HOME/logs/editor-result.json"
if [ "$(sha256sum < "$HOME/editor-user/User/settings.json")" != "$(sha256sum < "$HOME/logs/default-settings.before.json")" ] || \
   [ "$(sha256sum < "$HOME/editor-user/User/keybindings.json")" != "$(sha256sum < "$HOME/logs/default-keybindings.before.json")" ]; then
    echo 'Default profile configuration was modified' >&2; exit 1
fi
echo 'Default profile settings/keybindings preserved byte-for-byte.'

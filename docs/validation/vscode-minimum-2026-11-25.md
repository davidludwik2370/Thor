# VS Code version preflight and upgrade

The MacBook Air report identified VS Code 1.66.0, which predates profile support
and does not satisfy Thor's extension engine requirement (`^1.96.0`). Its CLI
warned that `--profile` was unknown. Thor's compiler installation was healthy,
but Doctor reported that no editor setup had been recorded.

The previous prerequisite check accepted an existing executable without checking
its version. Setup now shares `install/vscode-minimum` between the native
installer and platform prerequisite scripts. Automatic source setup checks the
editor before building the toolchain; binary bootstraps use the checkout's
current prerequisite fixes even with a previously published bundle. Explicit
editor paths remain user-managed and are validated before profile edits.

Compatible editors are reused. Obsolete standard installations are upgraded;
an old running editor must be quit normally, preserving unsaved work. On macOS,
the installer verifies the new app's signature and minimum OS version before
replacement, stages it on the destination filesystem, and rolls back a failed
relocation check. Existing settings and extension directories are preserved.
Doctor rejects an editor downgrade. Uninstall retains editor extensions when
the available CLI cannot safely address their recorded profile.

Older Macs use reviewed VS Code 1.147.0, rather than automatically following
future releases beyond their OS support. Its app metadata specifies macOS 12.0
as the minimum. Microsoft documents the Ventura/Sonoma transition in its
[platform support FAQ](https://code.visualstudio.com/docs/supporting/faq#can-i-run-vs-code-on-old-macos-versions).
The extension minimum is checked against the committed extension manifest.

Validation includes old/current/numerically larger versions, malformed output,
both Mac executable names, process-check self-matching, active-editor refusal,
signature and OS-floor failures, rollback, settings preservation, source-build
preflight ordering, editor downgrade, and safe uninstall. The final counts are
recorded with the test evidence: the broad selection passed 110 tests, the final
prerequisite/assertion selection passed 23, and the standalone-bootstrap and
source-ordering selection passed nine. The native installer passed nine Rust
tests. All checks passed; the smaller selections include overlapping tests.

A real signed VS Code 1.147.0 archive was downloaded and used to replace a
disposable 1.66.0 CLI fixture. VS Code then created a real isolated Thor profile,
installed the release's Thor 0.23.3 VSIX, recognized `main.thor` as language
`thor`, found its grammar, and successfully activated the extension. This
complete upgrade/editor probe took 23.52 seconds on the development Mac.
It simulated macOS 13.5.1 for package selection, while the actual GUI ran on
macOS 15.6; it is not a native macOS 13 result. Separate real CLI probes verified
profile creation both with a project folder and with an empty window. Test
applications, profiles and temporary build data were removed afterward.

For an existing compiler installation, quit VS Code and run these from the
macOS Terminal app in the updated checkout:

```sh
git pull --ff-only
sh install/prerequisites.sh --vscode
thor-install --vscode --profile Thor
thor-install --doctor
```

This upgrades/configures the editor and reuses the installed compiler. A source
compiler rebuild is not required to repair this installation.

# Hover when opening an unbuilt compiler checkout

On the macOS 13.5.1 MacBook Air, Thor's grammar and extension were installed, but
hover did not work. The actual extension log reported that TLS was missing at
`~/Thor/tls/bin/tls`. The repository contained TLS source; its compiled server
was correctly installed under Application Support with the rest of the toolchain.

Discovery previously accepted a candidate containing a compiler launcher and a
`tls` directory. A source checkout therefore shadowed the registered installation
before startup checked for the executable. TLS startup now requests discovery
with a built-server requirement. Incomplete workspace, environment and home
checkouts fall through to a working installed toolchain. Built development
checkouts remain selectable. Compiler-only Run and Debug retain their existing
discovery behavior and do not require TLS to be built.

Thor Language 0.23.4 contains the fix. Regressions cover an unbuilt workspace,
stale environment selection, registered installations missing their server,
working checkout fallback, and native Windows executable names. All **188 editor
unit tests** and **seven assertion-hygiene tests** passed. The existing debugger
discovery test also caught and prevented requiring TLS for compiler-only actions.

The updated VSIX was installed directly on the MacBook Air. After reloading the
window, the user confirmed that hover worked. A new inventoried installation
bundle was then assembled from that machine's verified installed payload and
the updated VSIX, without rebuilding or changing any compiler, TLS, SDK, runtime
or license files. This updates the retained editor payload too, so rerunning
editor setup retains 0.23.4. Doctor passed file checksums, checked and unchecked
compile/run, TLS hover and field completion, the editor inventory, JavaScript
runtime dependencies, and native OLS hover and completion. SSH's non-login PATH
refresh check was skipped as expected; interactive hover was confirmed separately.

Temporary archives and diagnostics were removed from the MacBook. Test logs and
the resulting component-inventory comparison are retained locally under
`dist/checkout-hover-2026-11-25/`. No project source files or editor preferences
were changed. The source fix applies to newly built bundles; existing published
archives are unchanged.

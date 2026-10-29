# Publishing applications

`thor publish` compiles an executable and packages it with its resources, icon
and discoverable native dependencies. It creates a local artifact; it does not
upload anything or submit to an app store. Ordinary build/run/check commands
keep their existing behavior and do not read publish settings or convert icons.

```sh
thor publish .
thor publish . --app-name "My Game" --app-icon assets/icon.ico
thor publish . --app-type gui -o:speed
thor publish . --app-type console
```

The default application type is **GUI**. On macOS the bundle directly launches
the program's Mach-O executable, without opening Terminal. Windows selects the
Windows subsystem, which does not create a console window. Linux's optional
desktop entry uses `Terminal=false`. Packaging does not add a graphical UI to a
program; the program still creates its own windows through its chosen UI library.

Console mode is explicit. On macOS a small native launcher opens the packaged
command in Terminal. On Windows it selects the console subsystem. Linux desktop
entries use `Terminal=true`. macOS console Finder launches do not forward
`open --args`; launch the inner executable directly when command-line arguments
are needed. GUI macOS launches and ordinary native executable launches preserve
their normal argument handling.

## Files and icons

```text
my-project/
  main.thor
  thor.toml                # Optional.
  assets/
    icon.png               # Or icon.ico.
    images/
    sounds/
  data/
    defaults.json
```

All files under `assets/` are included recursively, preserving their relative
paths and empty directories. No source scanning attempts to infer arbitrary
runtime file opens. Files embedded with `#load` are already compiled in and need
not also be declared as resources.

An optional `thor.toml`, beside the entry package or source file, declares extra
resources and persistent metadata:

```toml
[publish]
name = "My Game"
id = "com.example.mygame"
version = "1.2.0"
type = "gui"
icon = "assets/icon.png"
resources = ["data/defaults.json", "fonts/"]
libraries = ["lib/plugin.dylib"]
```

Only declare libraries for the platform being published. `resources` adds to
the automatic `assets/` directory. `libraries` adds native libraries/plugins,
including those loaded dynamically and consequently absent from the executable's
link-time dependency list. Their own dependencies are inspected too. It does not
rewrite application-specific plugin loading code.

Resource and icon paths are project-relative and stay inside the project.
Project-local symlinks are followed into ordinary copied files; external links,
cycles, missing paths and special files are errors. Declared libraries may use
absolute paths to installed third-party libraries.

`assets/icon.png` or `assets/icon.ico` is discovered automatically. If both exist,
select one explicitly. Both formats work on all three platforms: PNG or the
largest image in an ICO becomes ICNS on macOS, an embedded ICO resource on
Windows, and PNG on Linux. PNG/ICO conversion requires
`python3 -m pip install Pillow`; publishing without an icon needs no image
package. Rectangular icons are cropped to a square without stretching. The crop
starts centered and shifts over transparent padding when that can retain all
visible artwork. A warning is emitted only if the crop removes pixels with
nonzero alpha; fully transparent padding, including hidden RGB colors, is quiet.
The warning follows `-ignore-warnings` and `-warnings-as-errors` and is included
in structured JSON diagnostics.

Large icons are reduced to at most 1024×1024 before platform conversion, without
a warning for scaling alone. The original source file is never edited. Normal
square ICO files retain their supplied Windows size variants; an ICO that needs
normalization is regenerated from its largest image. A 1024-pixel PNG is a useful
source; resizing cannot restore detail missing from a small source icon. Normal
image-decoder limits still apply to malformed or exceptionally large inputs.

Reading TOML uses Python 3.11's `tomllib`. On Python 3.10, install `tomli` to read
`thor.toml`. Publishing without that file, and the rest of the compiler, continue
to support Python 3.10.

## Options and outputs

CLI metadata overrides `[publish]`; repeated resource/library options add entries:

| Option | Meaning |
| --- | --- |
| `--app-name NAME` | Display name and package name; defaults to executable name. |
| `--app-id ID` | Reverse-domain identifier; default `local.thor.<sanitized-name>`. |
| `--app-version VERSION` | One to three numeric components; default `0.1.0`. |
| `--app-type gui\|console` | Launch mode; default `gui`. |
| `--app-icon PATH` | Project-relative PNG or ICO override. |
| `--app-resource PATH` | Extra project-relative file/directory; repeatable. |
| `--app-library PATH` | Extra native library/plugin; repeatable. |
| `--publish-dir PATH` | Override final package directory. |

Publishing defaults to `-o:speed` for the final executable. An explicit Odin
optimization flag such as `-o:none` or `-o:size` overrides that default, including
when `-debug` is present. For a minimally optimized debug package, use
`thor publish . -debug -o:none`. The default is applied only to the final publish
build; ordinary commands and compile-time helpers keep their existing settings.
Other Odin flags such as `-define:...` and `-debug` still work, and publishing
does not enable debug instrumentation unless requested. Application packaging flags
are accepted only by `publish` and never reach Odin or compile-time helpers.

The default executable name is the working directory's name, matching `build`.
`-out:path/name` selects the inner executable name and uses `path/` as the package
directory unless `--publish-dir` overrides it. `--app-name` changes the package's
display/file name independently of that executable name. Native compilation uses
a scratch executable; it does not leave an extra standalone binary in the project.

| Native host/target | Artifact |
| --- | --- |
| macOS | `Name.app` |
| Windows | `Name-windows-<arch>.zip` |
| Linux | `Name-linux-<arch>.tar.gz` |

Output defaults to the directory where the command runs. A successful command
prints the final path. The initial implementation requires the target OS to match
the host OS and an executable build mode. Cross-OS packaging, installers, MSIX,
AppImage, universal macOS builds, distribution signing and notarization are not
implemented. The Windows resource option `-resource:` is managed by publishing
and cannot also be provided manually; use `--app-type` instead of conflicting
native `-subsystem:` flags.

Publishing stages the result next to its destination and installs it only after
dependency processing and validation succeed. Existing packages are replaced
only when their embedded `thor-publish.json` marks them as Thor-managed. An
unrelated file, app or symlink is preserved with an error. Resource folders cannot
contain the publish destination. Temporary publish output is removed after success
or failure; normal generated source and compile-time/parser caches remain.

## Finding resources at runtime

```odin
import "thor:app"
import "core:os"

main :: proc() {
    path := app.resource_path("assets/images/sprite.png")
    defer delete(path)

    bytes, err := os.read_entire_file(path, context.allocator)
    assert(err == nil)
    defer delete(bytes)
}
```

`app.resource_path` returns an owned absolute string using the supplied allocator
(default `context.allocator`). Free it once with that allocator. During ordinary
Thor builds it resolves against the entry package's original directory; published
applications resolve relative to their executable and package layout. It does not
change the process working directory or redirect ordinary file I/O. Absolute paths,
parent traversal and Windows drive paths are rejected. It allocates and performs
runtime executable-path lookup when published, so it is not a pure compile-time
operation. Mutable user data should be stored outside the application resources.

## Dependencies and platform behavior

macOS uses `otool` to inspect linked dependencies and rpaths, recursively copies
third-party dylibs/frameworks into `Contents/Frameworks`, and rewrites load paths
in the copies with `install_name_tool`. Apple system libraries stay system
dependencies. Nested Mach-O files, frameworks and the final app are ad-hoc signed
and verified with `codesign`. Console mode additionally uses `clang` for its tiny
native launcher. These tools are provided by Apple's developer tools. This local
ad-hoc signature is not Developer ID signing/notarization for public distribution.
Frameworks with links outside their bundle, unresolved dependencies and conflicting
dependency names produce an error.

Publishing also asks Odin for its linked-library list in a temporary scratch
file. Those original link paths help find runtime libraries beside import
libraries and shared-library symlinks. The scratch list is removed afterward and
does not add files or work to ordinary builds. Explicit plugins can use macOS
`@rpath/...` loading; the published executable's rpath includes its Frameworks
directory. Linux's published rpath likewise includes its bundled `lib/` directory.

Windows reads PE import/delay-import tables without an external inspection tool.
Required non-system DLLs are copied beside the executable; the project root,
`bin`, `lib`, `libs`, declared-library directories and `PATH` are searched. Explicitly
declare a DLL outside those locations. The native Windows resource compiler used
by Odin embeds the app icon/version, and the selected subsystem controls the
console window. Extract the ZIP and run the executable.

Linux uses `readelf` (or `llvm-readelf`) and `ldd` for dependency inspection. It
copies non-system shared libraries under `lib/`; `patchelf` is required when
dependencies need relocation. The OS loader and core libc-related libraries stay
system dependencies. Run the archive's `AppRun` launcher. To add a menu entry,
explicitly run `install-desktop.sh` after putting the extracted app where it will
live; rerun that script after moving it. Archives target a CPU/OS ABI and are not
guaranteed to run on older or incompatible Linux distributions.

With `-debug`, generated dSYM bundles are included under `resources/Debug` (macOS:
`Contents/Resources/Debug`). They can be added to LLDB explicitly. Runtime source
diagnostics preserve their existing mapping behavior, but debug ASan symbolizer
tools and original source files are not bundled for another machine. Normal
release publication does not require Thor or Odin at runtime.

The compiler suite exercises native macOS bundles, moved packages, dependency
closure, icons, source-mapped errors, allocation tracking, TLS and Windows/Linux
packaging contracts. Windows/Linux native execution still needs verification on
those hosts.

See the [runnable example](../examples/publish/README.md).

"""Local, relocatable application packages. Imported only by `thor publish`."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import zipfile

from .compiler import ThorError


class PublishError(ThorError):
    pass


def host_target():
    system = {"darwin": "darwin", "win32": "windows", "linux": "linux"}.get(sys.platform)
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "x64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    if not system:
        raise PublishError("Publishing currently supports macOS, Windows and Linux hosts.")
    return system, arch


def run_tool(command):
    try:
        result = subprocess.run([str(x) for x in command], capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise PublishError(f"Publishing requires {command[0]} on PATH.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise PublishError(f"Publish tool {command[0]} failed: {detail}")
    return result.stdout


def relative_path(value, description):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PublishError(f"{description} must be a nonempty relative path.")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts or path == PurePosixPath("."):
        raise PublishError(f"{description} must stay inside the project: {value!r}")
    return Path(*path.parts)


def validate_name(value, description="Application name"):
    if not isinstance(value, str) or not value.strip() or value in {".", ".."} \
            or any(c in value for c in '/\\<>:"|?*') or any(ord(c) < 32 for c in value) \
            or value.endswith((" ", ".")):
        raise PublishError(f"{description} must be a valid file name: {value!r}")
    if value.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        raise PublishError(f"{description} is reserved on Windows: {value!r}")
    return value


def icon_image(path):
    """Decode PNG or the largest ICO image; keep conversion out of ordinary builds."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise PublishError("Publishing PNG/ICO icons requires Pillow: python3 -m pip install Pillow") from exc
    if path.suffix.lower() not in {".png", ".ico"}:
        raise PublishError("Application icons must be PNG or ICO files.")
    try:
        with Image.open(path) as opened:
            expected = "PNG" if path.suffix.lower() == ".png" else "ICO"
            if opened.format != expected:
                raise ValueError(f"expected {expected} image data")
            image = opened.ico.getimage(max(opened.ico.sizes(), key=lambda s: s[0] * s[1])) if expected == "ICO" else opened
            if image.width != image.height or not 1 <= image.width <= 4096:
                raise ValueError("use a square icon between 1 and 4096 pixels wide")
            return image.convert("RGBA")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise PublishError(f"Cannot read icon {path.name}: {exc}") from exc


def write_icon(image, source, destination, system):
    if system == "windows" and source.suffix.lower() == ".ico":
        shutil.copy2(source, destination)
        return
    if system == "darwin":
        # Pillow writes the multi-resolution ICNS container without Xcode.
        from PIL import Image
        image.resize((1024, 1024), Image.Resampling.LANCZOS).save(destination, format="ICNS")
    elif system == "windows":
        from PIL import Image
        image.resize((256, 256), Image.Resampling.LANCZOS).save(
            destination, format="ICO", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    else:
        image.save(destination, format="PNG")


def read_config(root):
    path = root / "thor.toml"
    if not path.exists():
        return {}
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
    except ImportError as exc:
        raise PublishError("Reading thor.toml needs Python 3.11+ or python3 -m pip install tomli.") from exc
    try:
        settings = tomllib.loads(path.read_text(encoding="utf-8")).get("publish", {})
    except (ValueError, OSError) as exc:
        raise PublishError(f"Cannot read thor.toml: {exc}") from exc
    if not isinstance(settings, dict):
        raise PublishError("thor.toml [publish] must be a table.")
    allowed = {"name", "id", "version", "icon", "resources", "libraries", "type"}
    unknown = settings.keys() - allowed
    if unknown:
        raise PublishError("Unknown [publish] setting: " + ", ".join(sorted(unknown)))
    for key in ("name", "id", "version", "icon", "type"):
        if key in settings and not isinstance(settings[key], str):
            raise PublishError(f"[publish].{key} must be a string.")
    for key in ("resources", "libraries"):
        if key in settings and (not isinstance(settings[key], list) or
                                any(not isinstance(x, str) or not x for x in settings[key])):
            raise PublishError(f"[publish].{key} must be an array of nonempty paths.")
    return settings


def collect_resources(root, entries):
    """Snapshot relative files, following only project-local, noncyclic links."""
    files = {}
    directories = set()

    def visit(path, relative, active):
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise PublishError(f"Resource symlink leaves the project: {relative}")
        if not path.exists():
            raise PublishError(f"Resource does not exist: {relative}")
        if path.is_dir():
            if resolved in active:
                raise PublishError(f"Resource directory contains a symlink cycle: {relative}")
            directories.add(relative)
            for child in sorted(path.iterdir()):
                visit(child, relative / child.name, active | {resolved})
        elif path.is_file():
            files[relative] = path
        else:
            raise PublishError(f"Resource is not an ordinary file or directory: {relative}")

    for value in entries:
        relative = relative_path(value, "Resource")
        visit(root / relative, relative, set())
    return files, directories


@dataclass
class Publisher:
    root: Path
    system: str
    arch: str
    name: str
    executable_name: str
    identifier: str
    version: str
    app_type: str
    output: Path
    icon: Path | None
    image: object
    resources: dict
    directories: set
    libraries: list
    linked_file: Path | None = None

    @classmethod
    def prepare(cls, entry, flags, options):
        root = entry.parent if entry.is_file() else entry
        if not root.is_dir():
            raise PublishError(f"Project does not exist: {root}")
        system, arch = host_target()
        target = next((f.split(":", 1)[1] for f in flags if f.startswith("-target:")), "")
        if target:
            parts = target.split("_")
            if len(parts) != 2 or parts[0] != system:
                raise PublishError("Publish on the target operating system; cross-OS publishing is not yet supported.")
            arch = parts[1]
        if any(f.startswith("-build-mode:") and f != "-build-mode:exe" for f in flags) or "-no-entry-point" in flags:
            raise PublishError("thor publish requires an executable build with an entry point.")
        if system == "windows" and any(f.startswith("-resource:") for f in flags):
            raise PublishError("thor publish manages Windows icon/version resources; custom -resource is not supported here.")
        config = read_config(root)
        explicit = next((f.split(":", 1)[1] for f in flags if f.startswith("-out:")), None)
        executable = Path(explicit).absolute() if explicit is not None else Path.cwd() / (Path.cwd().name or "program")
        executable_name = executable.name
        if system == "windows" and not executable_name.lower().endswith(".exe"):
            executable_name += ".exe"
        validate_name(executable_name, "Executable name")
        default_name = executable_name[:-4] if executable_name.lower().endswith(".exe") else executable_name
        name = validate_name(options.app_name if options.app_name is not None else config.get("name", default_name))
        identifier = options.app_id if options.app_id is not None else config.get("id", "local.thor." + (re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "app"))
        if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", identifier):
            raise PublishError("Application ID must be a reverse-domain identifier such as com.example.myapp.")
        version = options.app_version if options.app_version is not None else config.get("version", "0.1.0")
        if not re.fullmatch(r"\d{1,5}(?:\.\d{1,5}){0,2}", version) or any(int(x) > 65535 for x in version.split(".")):
            raise PublishError("Application version must have one to three numeric components, each at most 65535.")
        app_type = options.app_type if options.app_type is not None else config.get("type", "gui")
        if app_type not in {"gui", "console"}:
            raise PublishError("Application type must be gui or console.")
        output_dir = (options.publish_dir or executable.parent).resolve()
        suffix = ".app" if system == "darwin" else f"-{system}-{arch}" + (".zip" if system == "windows" else ".tar.gz")
        output = output_dir / (name + suffix)
        selected = options.app_icon if options.app_icon is not None else config.get("icon")
        icon = None
        if selected is not None:
            icon = root / relative_path(str(selected), "Icon")
        else:
            candidates = [root / "assets" / ("icon" + ext) for ext in (".png", ".ico")]
            existing = [p for p in candidates if p.exists()]
            if len(existing) > 1:
                raise PublishError("Both assets/icon.png and assets/icon.ico exist; choose one with --app-icon or [publish].icon.")
            icon = existing[0] if existing else None
        if icon is not None and (not icon.is_file() or not icon.resolve().is_relative_to(root)):
            raise PublishError(f"Icon must be an existing file inside the project: {icon}")
        image = icon_image(icon) if icon is not None else None
        entries = [*config.get("resources", []), *options.app_resource]
        if (root / "assets").exists() or (root / "assets").is_symlink():
            if not (root / "assets").is_dir():
                raise PublishError("The automatic assets path must be a directory.")
            entries.insert(0, "assets")
        resources, directories = collect_resources(root, entries)
        if any(output.is_relative_to((root / path).resolve()) for path in directories):
            raise PublishError("Publish output must be outside the included resource directories.")
        if output in [p.resolve() for p in resources.values()] or output == root:
            raise PublishError("Publish output would replace a project input.")
        libraries = []
        for value in [*config.get("libraries", []), *options.app_library]:
            path = Path(value)
            path = (root / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.exists() or not (path.is_file() or system == "darwin" and path.suffix == ".framework"):
                raise PublishError(f"Declared library does not exist or is unsupported: {value}")
            libraries.append(path)
        publisher = cls(root, system, arch, name, executable_name, identifier, version,
                        app_type, output, icon, image, resources, directories, libraries)
        publisher.check_output()
        return publisher

    def check_output(self):
        if self.output.is_symlink():
            raise PublishError("Publish output must not be a symlink.")
        if not self.output.exists():
            return
        try:
            if self.system == "darwin":
                data = json.loads((self.output / "Contents/Resources/thor-publish.json").read_text())
            elif self.system == "windows":
                with zipfile.ZipFile(self.output) as archive:
                    data = json.loads(archive.read(self.name + "/resources/thor-publish.json"))
            else:
                with tarfile.open(self.output) as archive:
                    data = json.load(archive.extractfile(self.name + "/resources/thor-publish.json"))
            if data.get("generator") != "thor" or data.get("schema") != 1:
                raise ValueError("not a Thor package")
        except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, zipfile.BadZipFile) as exc:
            raise PublishError(f"Refusing to replace output not managed by thor publish: {self.output}") from exc

    def native_flags(self, flags, scratch):
        result = [f for f in flags if not f.startswith("-out:")]
        exported = next((f.split(':', 1)[1] for f in result if f.startswith('-export-linked-libs-file:')), None)
        self.linked_file = Path(exported).absolute() if exported else scratch / 'linked-libraries.txt'
        if exported is None:
            result.append('-export-linked-libs-file:' + str(self.linked_file))
        if self.system == "darwin":
            extra = " ".join(f.split(":", 1)[1] for f in result if f.startswith("-extra-linker-flags:"))
            result = [f for f in result if not f.startswith("-extra-linker-flags:")]
            result.append("-extra-linker-flags:" + extra + " -Wl,-headerpad_max_install_names")
        elif self.system == "windows":
            expected = "windows" if self.app_type == "gui" else "console"
            specified = [f.split(":", 1)[1] for f in result if f.startswith("-subsystem:")]
            if specified and specified != [expected]:
                raise PublishError("-subsystem conflicts with --app-type; select gui or console with --app-type.")
            if not specified:
                result.append("-subsystem:" + expected)
            icon = scratch / "app.ico"
            if self.image is not None:
                write_icon(self.image, self.icon, icon, "windows")
            def rc(value):
                return value.replace("\\", "\\\\").replace('"', '\\"')
            numbers = (self.version.split(".") + ["0", "0", "0", "0"])[:4]
            content = '#pragma code_page(65001)\n'
            if self.image is not None:
                content += f'1 ICON "{rc(str(icon))}"\n'
            content += '1 VERSIONINFO\nFILEVERSION ' + ",".join(numbers) + '\nPRODUCTVERSION ' + ",".join(numbers) + '\nBEGIN\nBLOCK "StringFileInfo"\nBEGIN\nBLOCK "040904b0"\nBEGIN\n'
            for key, value in [("FileDescription", self.name), ("ProductName", self.name), ("FileVersion", self.version), ("ProductVersion", self.version)]:
                content += f'VALUE "{key}", "{rc(value)}\\0"\n'
            content += 'END\nEND\nBLOCK "VarFileInfo"\nBEGIN\nVALUE "Translation", 0x409, 1200\nEND\nEND\n'
            resource = scratch / "app.rc"
            resource.write_text(content, encoding="utf-8")
            result.append("-resource:" + str(resource))
        return result

    def package(self, executable, scratch):
        from .publish_deps import bundle_dependencies, sign_macos
        root = scratch / (self.name + (".app" if self.system == "darwin" else ""))
        if self.system == "darwin":
            binary_dir, resource_dir, library_dir = root / "Contents/MacOS", root / "Contents/Resources", root / "Contents/Frameworks"
        elif self.system == "windows":
            binary_dir, resource_dir, library_dir = root, root / "resources", root
        else:
            binary_dir, resource_dir, library_dir = root / "bin", root / "resources", root / "lib"
        for path in (binary_dir, resource_dir):
            path.mkdir(parents=True, exist_ok=True)
        binary = binary_dir / self.executable_name
        shutil.copy2(executable, binary)
        debug_symbols = Path(str(executable) + '.dSYM')
        if debug_symbols.is_dir():
            debug_dir = resource_dir / 'Debug'
            debug_dir.mkdir()
            shutil.copytree(debug_symbols, debug_dir / debug_symbols.name)
        for relative in self.directories:
            (resource_dir / relative).mkdir(parents=True, exist_ok=True)
        for relative, original in self.resources.items():
            destination = resource_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
        if (resource_dir / "thor-publish.json").exists():
            raise PublishError("Resource thor-publish.json conflicts with Thor's package metadata.")
        icon_name = {"darwin": "AppIcon.icns", "windows": "AppIcon.ico", "linux": "AppIcon.png"}[self.system]
        if self.image is not None:
            if (resource_dir / icon_name).exists():
                raise PublishError(f"Resource {icon_name} conflicts with the packaged application icon.")
            write_icon(self.image, self.icon, resource_dir / icon_name, self.system)
        hints = []
        if self.linked_file is not None:
            for line in self.linked_file.read_text().splitlines():
                fields = line.rsplit('\t', 2)
                if len(fields) == 3 and fields[2] == 'user':
                    path = Path(fields[0])
                    if not path.is_absolute():
                        path = self.root / path
                    if path.exists():
                        hints.append(path)
        libraries = bundle_dependencies(self.system, executable, binary, library_dir, self.root, self.libraries, hints)
        metadata = {"generator": "thor", "schema": 1, "name": self.name, "id": self.identifier,
                    "version": self.version, "platform": self.system, "architecture": self.arch,
                    "type": self.app_type, "resources": sorted(p.as_posix() for p in self.resources),
                    "libraries": libraries}
        (resource_dir / "thor-publish.json").write_text(json.dumps(metadata, indent=2) + "\n")
        if self.system == "darwin":
            bundle_executable = self.executable_name
            if self.app_type == "console":
                # Only console apps explicitly request Terminal. GUI apps run
                # their Mach-O executable directly through Launch Services.
                command = resource_dir / "ThorConsole.command"
                if command.exists() or self.executable_name == "ThorConsole":
                    raise PublishError("Executable name conflicts with Thor's console launcher.")
                command.write_text('#!/bin/sh\nexec "$(dirname "$0")/../MacOS/"' + shlex.quote(self.executable_name) + ' "$@"\n')
                command.chmod(0o755)
                launcher = binary_dir / "ThorConsole"
                source = scratch / 'console-launcher.c'
                source.write_text('''#include <mach-o/dyld.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
int main(void) {
    char executable[PATH_MAX], command[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) != 0) return 1;
    char *last = strrchr(executable, '/');
    if (!last) return 1;
    *last = 0;
    int n = snprintf(command, sizeof(command), "%s/../Resources/ThorConsole.command", executable);
    if (n < 0 || n >= sizeof(command)) return 1;
    execl("/usr/bin/open", "open", "-a", "Terminal", command, (char *)0);
    perror("Cannot open Terminal");
    return 1;
}
''')
                run_tool(['clang', '-arch', 'x86_64' if self.arch == 'amd64' else self.arch,
                          source, '-o', launcher])
                bundle_executable = launcher.name
            info = {"CFBundlePackageType": "APPL", "CFBundleInfoDictionaryVersion": "6.0",
                    "CFBundleExecutable": bundle_executable, "CFBundleName": self.name,
                    "CFBundleDisplayName": self.name, "CFBundleIdentifier": self.identifier,
                    "CFBundleShortVersionString": self.version, "CFBundleVersion": self.version,
                    "NSHighResolutionCapable": True}
            if self.image is not None:
                info["CFBundleIconFile"] = icon_name
            (root / "Contents/Info.plist").write_bytes(plistlib.dumps(info, sort_keys=True))
            sign_macos(root)
        elif self.system == "linux":
            launcher = root / "AppRun"
            launcher.write_text('#!/bin/sh\nexec "$(dirname "$0")/bin/"' + shlex.quote(self.executable_name) + ' "$@"\n')
            launcher.chmod(0o755)
            # Installation is explicit; a relocatable archive has no fixed
            # absolute desktop Exec/Icon paths until the user chooses a home.
            install = root / "install-desktop.sh"
            install.write_text(self.desktop_installer(icon_name if self.image is not None else ""))
            install.chmod(0o755)
        self.check_output()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        if self.system == "darwin":
            backup = scratch / "previous.app"
            if self.output.exists():
                os.replace(self.output, backup)
            try:
                os.replace(root, self.output)
            except OSError:
                if backup.exists():
                    os.replace(backup, self.output)
                raise
        else:
            archive = scratch / ("package.zip" if self.system == "windows" else "package.tar.gz")
            if self.system == "windows":
                with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as stream:
                    for path in sorted(root.rglob("*")):
                        if path.is_file():
                            stream.write(path, str(Path(self.name) / path.relative_to(root)))
                        elif path.is_dir():
                            stream.write(path, str(Path(self.name) / path.relative_to(root)) + "/")
            else:
                with tarfile.open(archive, "w:gz") as stream:
                    stream.add(root, arcname=self.name)
            os.replace(archive, self.output)
        return self.output

    def desktop_installer(self, icon_name):
        # The application itself has no Python/Thor dependency. Escape desktop
        # entry values at installation time, when the absolute path is known.
        exec_escape = r'''sed 's/\\/\\\\\\\\/g; s/"/\\\\"/g; s/`/\\\\`/g; s/\$/\\\\$/g; s/%/%%/g' '''.strip()
        return '''#!/bin/sh
set -eu
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
desktop_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$desktop_dir"
escaped_exec=$(printf '%s' "$app_dir/AppRun" | ''' + exec_escape + ''')
escaped_icon=$(printf '%s' "$app_dir/resources/''' + icon_name + '''" | sed 's/\\\\/\\\\\\\\/g')
{
  printf '%s\\n' '[Desktop Entry]' 'Type=Application' ''' + shlex.quote("Name=" + self.name.replace("\\", "\\\\")) + '''
  printf 'Exec="%s"\\n' "$escaped_exec"
''' + ('''  printf 'Icon=%s\\n' "$escaped_icon"
''' if icon_name else "") + '''  printf '%s\\n' ''' + shlex.quote("Terminal=" + str(self.app_type == "console").lower()) + '''
} > "$desktop_dir/''' + self.identifier + '''.desktop"
printf '%s\\n' 'Desktop entry installed. Rerun this script after moving the application.'
'''

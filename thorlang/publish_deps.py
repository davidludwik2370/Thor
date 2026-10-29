"""Inspect native dependencies and relocate only copies in the publish staging tree."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
import struct

from .publishing import PublishError, run_tool


def macho(path):
    with path.open("rb") as stream:
        return stream.read(4) in {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}


def sign_macos(root):
    # Sign nested Mach-O files first, then framework bundles and the outer app.
    # In particular, never use --deep as a substitute for signing dependencies.
    seen = set()
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.resolve() not in seen and macho(path):
            seen.add(path.resolve())
            run_tool(["codesign", "--force", "--sign", "-", path])
    frameworks = sorted((p for p in root.rglob("*.framework") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for framework in frameworks:
        run_tool(["codesign", "--force", "--sign", "-", framework])
    run_tool(["codesign", "--force", "--sign", "-", root])
    run_tool(["codesign", "--verify", "--deep", "--strict", root])


def macos_dependencies(executable, binary, destination, project, declared, hints=()):
    queue = [(executable.resolve(), binary, [])]
    mapped = {executable.resolve(): binary}
    owners = {}

    def register(path, inherited=()):
        path = path.resolve()
        if path in mapped:
            return mapped[path]
        framework = next((p for p in path.parents if p.suffix == ".framework"), None)
        if framework:
            relative = Path(framework.name) / path.relative_to(framework)
            owner, source = framework.name, framework
        else:
            relative = Path(path.name)
            owner, source = path.name, path
        if owner in owners and owners[owner] != source:
            raise PublishError(f"Two different dependencies would have the same packaged name: {owner}")
        if owner not in owners:
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / owner
            if target.exists():
                raise PublishError(f"Dependency conflicts with a packaged file: {owner}")
            if framework:
                shutil.copytree(framework, target, symlinks=True)
                # Prevent an installed framework's external symlink from
                # silently keeping this package dependent on the build machine.
                for link in target.rglob("*"):
                    if link.is_symlink() and not link.resolve().is_relative_to(target.resolve()):
                        raise PublishError(f"Framework has an external symlink: {owner}/{link.relative_to(target)}")
                    if not link.is_symlink() and link.is_dir():
                        link.chmod(link.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
                    elif not link.is_symlink() and link.is_file() and macho(link):
                        link.chmod(link.stat().st_mode | stat.S_IWUSR)
                target.chmod(target.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
            else:
                shutil.copy2(path, target)
                target.chmod(target.stat().st_mode | stat.S_IWUSR)
            owners[owner] = source
        target = destination / relative
        mapped[path] = target
        queue.append((path, target, list(inherited)))
        return target

    for path in declared:
        if path.is_dir():
            executable_name = path.stem
            candidates = [path / executable_name, path / "Versions/Current" / executable_name]
            path = next((p for p in candidates if p.is_file()), None)
            if path is None:
                raise PublishError("Cannot find the executable in the declared framework.")
        if not macho(path):
            raise PublishError(f"Declared library is not Mach-O: {path.name}")
        register(path)

    def expand(value, source):
        if value.startswith("@loader_path"):
            return source.parent / value.removeprefix("@loader_path").lstrip("/")
        if value.startswith("@executable_path"):
            return executable.parent / value.removeprefix("@executable_path").lstrip("/")
        return Path(value)

    for source, target, inherited in queue:
        listing = run_tool(["otool", "-L", source])
        dependencies = list(dict.fromkeys(line.strip().split(" (compatibility version", 1)[0]
                            for line in listing.splitlines() if line.startswith(("\t", " "))))
        load_commands = run_tool(["otool", "-l", source])
        rpaths = [expand(value, source) for value in re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset", load_commands)]
        rpaths += inherited
        identifier = ""
        if source != executable.resolve():
            identities = run_tool(["otool", "-D", source]).splitlines()
            identifier = next((line.strip() for line in identities[1:] if line and not line.endswith(":")), "")
        for dependency in dependencies:
            if dependency == identifier or dependency.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if dependency.startswith("@rpath/"):
                suffix = dependency[len("@rpath/"):]
                candidates = [p / suffix for p in rpaths]
                candidates += [p.parent / suffix for p in declared]
                candidates += [p for p in hints if p.name == Path(suffix).name]
                candidates += [p.parent / suffix for p in hints]
                candidates += [project / suffix, source.parent / suffix]
            else:
                candidates = [expand(dependency, source)]
                if dependency.startswith(('@executable_path/', '@loader_path/')):
                    suffix = dependency.split('/', 1)[1]
                    candidates += [project / suffix]
                candidates += [p for p in hints if p.name == Path(dependency).name]
            resolved = next((p.resolve() for p in candidates if p.is_file()), None)
            if resolved is None:
                raise PublishError(f"Cannot locate dependency {dependency!r} required by {source.name}; declare it with --app-library or [publish].libraries.")
            if str(resolved).startswith(("/usr/lib/", "/System/Library/")):
                continue
            relocated = register(resolved, rpaths)
            replacement = "@loader_path/" + os.path.relpath(relocated, target.parent).replace(os.sep, "/")
            run_tool(["install_name_tool", "-change", dependency, replacement, target])
        if identifier:
            run_tool(["install_name_tool", "-id", "@rpath/" + target.relative_to(destination).as_posix(), target])
    if owners:
        for target in mapped.values():
            rpath = '@loader_path/' + os.path.relpath(destination, target.parent).replace(os.sep, '/')
            commands = run_tool(['otool', '-l', target])
            existing = re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset', commands)
            if rpath not in existing:
                run_tool(['install_name_tool', '-add_rpath', rpath, target])
    return sorted(p.relative_to(destination).as_posix() for p in mapped.values() if p != binary)


def pe_imports(path):
    """Read PE import and delay-import tables without requiring Visual Studio tools."""
    data = path.read_bytes()
    try:
        if data[:2] != b"MZ":
            raise ValueError("missing DOS header")
        pe = struct.unpack_from("<I", data, 0x3c)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ValueError("missing PE header")
        sections, optional_size = struct.unpack_from("<H12xH", data, pe + 6)
        optional = pe + 24
        magic = struct.unpack_from("<H", data, optional)[0]
        if magic not in (0x10b, 0x20b):
            raise ValueError("unsupported PE optional header")
        directories = optional + (96 if magic == 0x10b else 112)
        count = struct.unpack_from("<I", data, directories - 4)[0]
        image_base = struct.unpack_from("<I" if magic == 0x10b else "<Q", data, optional + (28 if magic == 0x10b else 24))[0]
        table = optional + optional_size
        section_data = [struct.unpack_from("<8sIIII", data, table + i * 40) for i in range(sections)]

        def offset(rva):
            for _, virtual_size, address, size, raw in section_data:
                if address <= rva < address + max(size, virtual_size):
                    position = raw + rva - address
                    if position >= len(data) or rva - address >= size:
                        break
                    return position
            raise ValueError("import address is outside file sections")

        def string(rva):
            start = offset(rva)
            end = data.index(b"\0", start)
            name = data[start:end].decode("ascii")
            if not name or "/" in name or "\\" in name or ":" in name:
                raise ValueError("invalid DLL import name")
            return name

        imports = []
        for index, width in [(1, 20), (13, 32)]:
            if count <= index:
                continue
            rva, size = struct.unpack_from("<II", data, directories + index * 8)
            if not rva:
                continue
            start = offset(rva)
            for position in range(start, min(len(data), start + size), width):
                record = data[position:position + width]
                if len(record) != width:
                    raise ValueError("truncated import table")
                if not any(record):
                    break
                if index == 1:
                    name = struct.unpack_from("<I", record, 12)[0]
                else:
                    attrs, name = struct.unpack_from("<II", record)
                    if not attrs & 1:
                        name -= image_base
                imports.append(string(name))
        return list(dict.fromkeys(imports))
    except (struct.error, ValueError, UnicodeError, IndexError) as exc:
        raise PublishError(f"Cannot inspect Windows executable {path.name}: {exc}") from exc


WINDOWS_SYSTEM = set("kernel32 user32 gdi32 gdi32full advapi32 shell32 ole32 oleaut32 uuid comdlg32 comctl32 ws2_32 winmm imm32 version shlwapi setupapi cfgmgr32 bcrypt crypt32 ncrypt secur32 iphlpapi dnsapi psapi dbghelp ntdll msvcrt ucrtbase opengl32 d3d11 d3d12 dxgi dwmapi shcore powrprof winhttp wininet hid winspool uxtheme normaliz msimg32 rpcrt4 avrt dinput8 dsound xinput1_4 xinput9_1_0 mfplat mfreadwrite mfuuid strmiids propsys wtsapi32 netapi32 userenv usp10 wintrust mswsock msasn1 kernelbase dcomp windowscodecs winusb win32u dhcpcsvc cryptbase profapi ktmw32 cabinet winbio winfax urlmon combase wldap32 activeds authz cryptui winhttp wininet winmm".split())


def windows_dependencies(executable, binary, destination, project, declared, hints=()):
    dirs = [executable.parent, project, project / "bin", project / "lib", project / "libs"]
    dirs += [p.parent for p in declared]
    dirs += [p.parent for p in hints]
    dirs += [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    queue = [(executable.resolve(), binary)]
    owners = {}
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))

    def find(name):
        for directory in dirs:
            if directory.is_dir():
                found = next((p for p in directory.iterdir() if p.name.lower() == name.lower() and p.is_file()), None)
                if found:
                    return found.resolve()
        return None

    def register(source):
        if not source.is_file():
            raise PublishError(f"Declared Windows library must be a DLL file: {source}")
        key = source.name.lower()
        if key in owners:
            if owners[key] != source:
                raise PublishError(f"Two different DLLs have the same packaged name: {source.name}")
            return
        target = destination / source.name
        if target.exists():
            raise PublishError(f"DLL conflicts with a packaged file: {source.name}")
        shutil.copy2(source, target)
        owners[key] = source
        dirs.append(source.parent)
        queue.append((source, target))

    for source in declared:
        register(source.resolve())
    for source, target in queue:
        for name in pe_imports(source):
            lower = name.lower()
            if lower.startswith(("api-ms-win-", "ext-ms-win-")) or lower.removesuffix(".dll") in WINDOWS_SYSTEM:
                continue
            dependency = find(name)
            if dependency is None:
                system = system_root / "System32" / name
                if system.exists():
                    continue
                raise PublishError(f"Cannot locate DLL {name!r} required by {source.name}; declare it with --app-library or [publish].libraries.")
            if dependency.is_relative_to(system_root.resolve()):
                continue
            register(dependency)
    return sorted(source.name for source in owners.values())


LINUX_SYSTEM = re.compile(r"^(?:ld-linux[^/]*|ld-musl[^/]*|lib(?:c|m|dl|pthread|rt|resolv|util|anl)\.so(?:\..*)?|linux-vdso\.so(?:\..*)?)$")


def linux_dependencies(executable, binary, destination, project, declared, hints=()):
    inspector = shutil.which("readelf") or shutil.which("llvm-readelf")
    if not inspector:
        raise PublishError("Linux publishing requires readelf (binutils) or llvm-readelf.")
    queue = [(executable.resolve(), binary)]
    owners = {}
    patched = set()

    def register(source):
        source = source.resolve()
        if not source.is_file():
            raise PublishError(f"Declared Linux library must be a file: {source}")
        name = source.name
        if name in owners:
            if owners[name] != source:
                raise PublishError(f"Two different libraries have the same packaged name: {name}")
            return destination / name
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / name
        shutil.copy2(source, target)
        owners[name] = source
        queue.append((source, target))
        return target

    for source in declared:
        register(source)
    for source, target in queue:
        dynamic = run_tool([inspector, "-d", source])
        needed = re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic)
        third_party = [name for name in needed if not LINUX_SYSTEM.match(Path(name).name)]
        if not third_party:
            continue
        resolved = None
        for dependency in third_party:
            candidates = [Path(dependency)] if os.path.isabs(dependency) else []
            candidates += [p for p in declared if p.name == dependency]
            candidates += [source.parent / dependency, project / dependency]
            candidates += [project / 'lib' / dependency, project / 'libs' / dependency]
            candidates += [p.parent / dependency for p in [*hints, *declared]]
            original = next((p for p in candidates if p.is_file()), None)
            if original is None:
                if resolved is None:
                    listing = run_tool(["ldd", source])
                    resolved = {name: Path(path) for name, path in re.findall(r"^\s*(\S+)\s+=>\s+(/.*?)\s+\(", listing, re.M)}
                original = resolved.get(dependency)
                if original is not None and not original.is_file():
                    original = None
            if original is None:
                raise PublishError(f"Cannot locate dependency {dependency!r} required by {source.name}; declare it with --app-library or [publish].libraries.")
            relocated = register(original)
            if dependency != relocated.name:
                run_tool(["patchelf", "--replace-needed", dependency, relocated.name, target])
        rpath = "$ORIGIN" if target.parent == destination else "$ORIGIN/" + os.path.relpath(destination, target.parent)
        run_tool(["patchelf", "--set-rpath", rpath, target])
        patched.add(target)
    if owners and binary not in patched:
        # A program can load explicitly declared plugins without a NEEDED entry.
        # Give its runtime loader the bundled library directory in that case too.
        rpath = '$ORIGIN/' + os.path.relpath(destination, binary.parent)
        run_tool(['patchelf', '--set-rpath', rpath, binary])
    return sorted(owners)


def bundle_dependencies(system, executable, binary, destination, project, declared, hints=()):
    functions = {"darwin": macos_dependencies, "windows": windows_dependencies, "linux": linux_dependencies}
    return functions[system](executable, binary, destination, project, declared, hints)

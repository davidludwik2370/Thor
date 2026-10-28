"""Small, source-text-free location tables shared by runtime memory reports.

Columns use Odin/LLVM's Unicode scalar convention. Exact copied spans retain
column precision; lowered spans identify their original owning expression.
"""
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import struct
import zlib

MAGIC = b"THOR-MEM-MAP\x00\x01"
MAX_PAYLOAD = 32 * 1024 * 1024


def _lines(raw):
    return [0, *[i + 1 for i, byte in enumerate(raw) if byte == 10]]


def _position(raw, lines, offset):
    row = bisect_right(lines, offset) - 1
    return row + 1, len(raw[lines[row]:offset].decode("utf-8")) + 1


def compact(project, symbolizer=""):
    originals = {str(p): s.raw for p, s in project.sources.items()}
    originals.update({str(p): s.raw for p, s in getattr(project, "sdk_sources", {}).items()})
    original_lines = {p: _lines(raw) for p, raw in originals.items()}
    files, file_ids, units = [], {}, []
    procedures = {}
    for path, source in project.sources.items():
        procedures[str(path)] = [(n.start, n.end, n.parent.group("names")[0].text)
            for n in source.nodes if n.kind == "Procedure" and n.parent
            and n.parent.kind == "Declaration" and n.parent.group("names")]
    for path, mapping in sorted(project.maps.items()):
        raw = project.generated[path]
        lines, rows = _lines(raw), []
        for a, b, original, start, end, kind, exact in mapping.segments:
            if original not in originals or kind == "memory-debug":
                continue
            if original not in file_ids:
                file_ids[original] = len(files)
                files.append(original)
            owner = min((p for p in procedures.get(original, ()) if p[0] <= start < p[1]),
                        key=lambda p: p[1] - p[0], default=(0, 0, ""))[2]
            cursor = a
            while cursor < b:
                line, column = _position(raw, lines, cursor)
                stop = min(b, lines[line] if line < len(lines) else len(raw))
                content_end = stop - 1 if raw[stop - 1:stop] == b"\n" else stop
                last_column = column + len(raw[cursor:content_end].decode("utf-8"))
                origin = start + cursor - a if exact else start
                source_line, source_column = _position(originals[original], original_lines[original], origin)
                # Include the end-of-line position for location-only stack traces.
                row = [line, column, max(column + 1, last_column + (stop > content_end)),
                       file_ids[original], source_line, source_column, bool(exact), owner]
                previous = rows[-1] if rows else None
                if previous and exact and previous[6] and previous[0] == line and previous[2] == column \
                        and previous[3:5] == row[3:5] and previous[7] == owner \
                        and previous[5] + column - previous[1] == source_column:
                    previous[2] = row[2]
                else:
                    rows.append(row)
                cursor = stop
        units.append({"path": path, "rows": rows})
    aliases = project.diagnostic_adapter().path_aliases
    return {"version": 1, "files": files, "units": units, "aliases": aliases,
            "symbolizer": symbolizer}


class Locations:
    def __init__(self, data):
        if data.get("version") != 1:
            raise ValueError("unsupported Thor memory map")
        self.files = data["files"]
        self.units = {}
        self.aliases = data.get("aliases", {})
        for unit in data["units"]:
            rows = unit["rows"]
            self.units[unit["path"]] = (rows, [(r[0], r[1]) for r in rows])

    def lookup(self, path, line, column=1):
        unit = self.units.get(path)
        if unit:
            rows, starts = unit
            index = bisect_right(starts, (line, max(column, 1))) - 1
            if index < 0 or rows[index][0] != line:
                # Some platform trace resolvers provide a line but no column.
                index = bisect_right(starts, (line, 0))
            if 0 <= index < len(rows):
                row, first, last, file, source_line, source_column, exact, procedure = rows[index]
                if row == line and (column <= 1 or first <= column < last):
                    return self.files[file], source_line, source_column + (max(0, column - first) if exact else 0), procedure
        for old, new in self.aliases.items():
            if path.startswith(old):
                return new + path[len(old):], line, column, ""
        return None


def encode(data):
    payload = zlib.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())
    return MAGIC + struct.pack("<Q", len(payload)) + hashlib.sha256(payload).digest() + payload


def decode(blob):
    header = len(MAGIC) + 8 + 32
    if not blob.startswith(MAGIC) or len(blob) < header:
        raise ValueError("invalid Thor memory map header")
    size = struct.unpack_from("<Q", blob, len(MAGIC))[0]
    if size > MAX_PAYLOAD or header + size > len(blob):
        raise ValueError("invalid Thor memory map size")
    payload = blob[header:header + size]
    if hashlib.sha256(payload).digest() != blob[len(MAGIC) + 8:header]:
        raise ValueError("Thor memory map checksum mismatch")
    inflater = zlib.decompressobj()
    raw = inflater.decompress(payload, MAX_PAYLOAD + 1)
    if len(raw) > MAX_PAYLOAD or not inflater.eof:
        raise ValueError("Thor memory map is too large or incomplete")
    data = json.loads(raw)
    Locations(data)  # Validate version and the lookup structure before using it.
    return data


def read_binary(path):
    """Read only the dedicated Mach-O section, never scan arbitrary binary data."""
    with Path(path).open("rb") as stream:
        header = stream.read(32)
        if len(header) != 32 or struct.unpack_from("<I", header)[0] != 0xFEEDFACF:
            raise ValueError("not a native 64-bit Mach-O executable")
        count, command_size = struct.unpack_from("<II", header, 16)
        if command_size > MAX_PAYLOAD or count > 65536:
            raise ValueError("invalid Mach-O load commands")
        commands = stream.read(command_size)
        offset = 0
        for _ in range(count):
            if offset + 8 > len(commands):
                break
            command, size = struct.unpack_from("<II", commands, offset)
            if size < 8 or offset + size > len(commands):
                break
            if command == 0x19 and size >= 72:  # LC_SEGMENT_64
                sections = struct.unpack_from("<I", commands, offset + 64)[0]
                for index in range(min(sections, (size - 72) // 80)):
                    start = offset + 72 + index * 80
                    if commands[start:start + 16].rstrip(b"\0") == b"__thor_map":
                        length = struct.unpack_from("<Q", commands, start + 40)[0]
                        position = struct.unpack_from("<I", commands, start + 48)[0]
                        if length > MAX_PAYLOAD:
                            raise ValueError("Thor memory map section is too large")
                        stream.seek(position)
                        return decode(stream.read(length))
            offset += size
    raise ValueError("executable does not contain a Thor memory map")

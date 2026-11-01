"""Composable emission annotations and versioned, UTF-8 byte source maps.

Annotations live only during lowering. Nested annotations survive interpolation,
duplication and reordering; decoding them partitions the output into disjoint
spans. Copied bytes map exactly, synthetic bytes map to their owning construct.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import re
import uuid


@dataclass(frozen=True)
class Origin:
    file: str
    start: int
    end: int
    kind: str = "generated"
    exact: bool = False


class MappingBuilder:
    def __init__(self, *, debug=True):
        self.prefix = "\0thor-map-" + uuid.uuid4().hex + ":"
        self.origins = []
        self.debug=debug

    def wrap(self, text, path, start, end, kind="generated", *, exact=False):
        if not text:
            return text
        index = len(self.origins)
        self.origins.append(Origin(str(path), start, end, kind, exact))
        return f"{self.prefix}{index}+\0{text}{self.prefix}{index}-\0"

    def finish(self, annotated):
        pattern = re.compile(re.escape(self.prefix) + r"(\d+)([+-])\x00")
        output, segments, stack, expansions, regions = [], [], [], [], []
        cursor = offset = 0
        for match in pattern.finditer(annotated):
            chunk = annotated[cursor:match.start()]
            if chunk:
                if not stack:
                    raise ValueError("unmapped generated source")
                origin, opened = stack[-1]
                size = len(chunk.encode("utf-8"))
                start = origin.start + offset - opened if origin.exact else origin.start
                end = start + size if origin.exact else origin.end
                kind='pure-procedure' if origin.kind.startswith('pure-procedure|') else origin.kind
                row=[offset, offset + size, origin.file, start, end, kind, origin.exact]
                previous=segments[-1] if segments else None
                if previous and origin.exact and previous[6] and previous[1]==offset and previous[2]==origin.file and previous[4]==start and previous[5]==kind:
                    previous[1]=offset+size;previous[4]=end
                else:segments.append(row)
                output.append(chunk)
                offset += size
            index = int(match[1])
            if match[2] == "+":
                stack.append((self.origins[index], offset))
            else:
                assert stack and stack[-1][0] == self.origins[index], "unbalanced emission annotations"
                origin, opened = stack.pop()
                last=segments[-1] if segments else None
                identity=bool(last and last[6] and last[2]==origin.file and last[0]<=opened and last[1]>=offset
                              and last[3]+opened-last[0]==origin.start and origin.end-origin.start==offset-opened)
                if not origin.exact and not identity and origin.kind in {
                    'array','Binary','Ternary','Ident','pure-call-target','Index','Slice',
                    'SwitchExpr','CompileSwitchExpr','Interpolation','Call','Deref','Unary',
                }:
                    expansions.append([opened, offset, origin.file, origin.start, origin.end])
                if self.debug and origin.kind in {"expression-helper", "debug-capture", "source-call-target"}:
                    regions.append([opened, offset, origin.kind, origin.file, origin.start, origin.end])
                if self.debug and origin.kind.startswith('pure-procedure|'):
                    _,name,variant=origin.kind.split('|')
                    regions.append([opened,offset,'pure-procedure',origin.file,origin.start,origin.end,name,variant])
            cursor = match.end()
        assert not stack and cursor == len(annotated), "unbalanced emission annotations"
        return "".join(output), SourceMap(segments, expansions, regions)


class SourceMap:
    def __init__(self, segments, expansions=(), regions=()):
        self.segments = segments
        self.expansions = expansions
        self.regions = regions
        self.starts = [s[0] for s in segments]

    def lookup(self, offset, length=1):
        if not self.segments:
            return None
        s = self.segments[max(0, min(bisect_right(self.starts, offset) - 1, len(self.segments) - 1))]
        a, b, file, start, end, kind, exact = s
        if exact:
            start += min(max(offset - a, 0), b - a)
            end = min(end, start + max(length, 1))
        return Origin(file, start, end, kind, exact)

    def json(self, generated, sources):
        referenced = {s[2] for s in self.segments}
        return {"version": 1, "encoding": "utf-8", "generated": str(generated),
                "sources": {str(p): s.raw.decode("utf-8") for p, s in sources.items() if str(p) in referenced},
                "segments": self.segments, "expansions": self.expansions,
                "regions": self.regions}


def position(raw, offset):
    """One-based line and UTF-16 column (also used by the LSP adapter)."""
    offset = min(max(offset, 0), len(raw))
    line = raw.count(b"\n", 0, offset) + 1
    start = raw.rfind(b"\n", 0, offset) + 1
    column = len(raw[start:offset].decode("utf-8", errors="replace").encode("utf-16-le")) // 2 + 1
    return line, column

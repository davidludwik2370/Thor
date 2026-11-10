"""Qualify generated native names only when user bindings can shadow them.

Exact copied source is never rewritten. Odin's builtin namespace supplies the
same native operations/types, without adding Thor-reserved identifiers.
"""
from bisect import bisect_left
import re


BUILTINS = {
    'type_of', 'typeid_of', 'type_info_of', 'size_of', 'align_of', 'offset_of',
    'len', 'cap', 'make', 'new', 'new_clone', 'delete', 'clear', 'reserve',
    'resize', 'append', 'copy', 'clone', 'assert', 'assert_contextless',
    'panic', 'panic_contextless', 'unreachable', 'min', 'max', 'abs', 'clamp',
    'raw_data', 'expand_values', 'soa_zip', 'soa_unzip',
}
TOKEN = re.compile(rb'//[^\r\n]*|/\*|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'|`[^`]*`|'
                   rb'[A-Za-z_\x80-\xff][A-Za-z_0-9\x80-\xff]*|\s+|[\s\S]')


def tokens(raw):
    """Native token boundaries, excluding whitespace and nested comments."""
    cursor = 0
    while cursor < len(raw):
        match = TOKEN.match(raw, cursor)
        token = match[0]
        start, cursor = match.span()
        if token == b'/*':
            depth = 1
            while depth and cursor < len(raw):
                if raw[cursor:cursor+2] == b'/*': depth += 1; cursor += 2
                elif raw[cursor:cursor+2] == b'*/': depth -= 1; cursor += 2
                else: cursor += 1
            continue
        if not token.isspace() and not token.startswith(b'//'):
            yield start, cursor, token


def qualify(source, code, mapping):
    names = set(source.imports) | set(source.selected)
    scopes = [(source.path.parent, 'package')]
    scopes.extend(node for node in source.nodes if source.is_scope(node) or node.kind == 'ProcedureType')
    for scope in scopes:
        names.update(source.resolver.scopes.get(scope, {}).keys() & BUILTINS)
    names &= BUILTINS
    if not names: return code, mapping
    raw = code.encode()
    inserts = []
    previous = b''
    scanned=list(tokens(raw))
    for i,(start, _, token) in enumerate(scanned):
        follows=scanned[i+1][2] if i+1<len(scanned) else None
        if follows==b'(' and token.decode(errors='replace') in names and previous not in {b'.', b'#'}:
            origin = mapping.lookup(start, len(token))
            if origin and not origin.exact and origin.kind not in {'source-call-target','pure-call-target','run-target'}:
                inserts.append(start)
        previous = token
    if not inserts: return code, mapping
    alias = source.prefix + 'builtin'
    prefix = (alias + '.').encode()
    # Insertions lie exclusively in synthetic segments, so their source origin
    # is unchanged. Shift every generated coordinate, including debug regions
    # and expression expansion boundaries; original byte offsets stay intact.
    def shift(offset): return offset + bisect_left(inserts, offset) * len(prefix)
    for rows in (mapping.segments, mapping.expansions, mapping.regions):
        for row in rows: row[0], row[1] = shift(row[0]), shift(row[1])
    mapping.starts = [row[0] for row in mapping.segments]
    chunks, cursor = [], 0
    for start in inserts:
        chunks.extend((raw[cursor:start], prefix))
        cursor = start
    chunks.append(raw[cursor:])
    raw = b''.join(chunks)
    # The purity validator can already have emitted this namespace in the
    # same file. Reuse it for hygiene-qualified operations.
    namespace = (b'import', alias.encode(), b'"base:builtin"')
    spellings = [token for _, _, token in scanned]
    if any(tuple(spellings[i:i+3]) == namespace for i in range(len(spellings)-2)):
        return raw.decode(), mapping
    imported = f'\nimport {alias} "base:builtin"\n'.encode()
    mapping.segments.append([len(raw), len(raw)+len(imported), str(source.path), 0, 0, 'support', False])
    mapping.starts.append(len(raw))
    return (raw+imported).decode(), mapping

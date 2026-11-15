"""Preserve native defer exit selection and map its emitted location to source."""
from bisect import bisect_right

from . import sourceidentity as identity
from .hygiene import tokens
from .sourcemap import Origin


def deferred_owner(node):
    owner = identity.active_procedure(node)
    if owner is None:
        return None
    for parent in node.ancestors():
        if parent is owner:
            break
        if parent.kind == 'Defer':
            return owner
    return None


def branch_value(source, owner):
    for procedure, parameter in reversed(getattr(source, 'branch_captures', [])):
        if procedure is owner:
            return parameter
    return '#branch_location'


def render(source, node):
    owner = deferred_owner(node)
    if owner is None:
        return None  # Odin diagnoses invalid uses and retains native when laziness.
    value = branch_value(source, owner)
    if identity.mode(source) == 'none':
        return value
    source.branch_procedures.add(owner)
    source.expressions.module('source_runtime', 'base:runtime')
    procedure = identity.string(source, identity.location_procedure(owner.role('body')), 'P')
    return source.prefix + 'map_branch_location(' + value + ', ' + identity.quote(procedure) + ')'


def capture_helper(expressions, node, params, args):
    from .semantics import descendants
    owner = deferred_owner(node)
    if owner is None:
        return None
    needed = any(child.kind == 'Directive' and child.data.get('name') == 'branch_location'
                 and identity.active_procedure(child) is owner for child in descendants(node))
    if not needed:
        return None
    source = expressions.source
    runtime = expressions.module('source_runtime', 'base:runtime')
    parameter = source.prefix + 'branch_capture'
    params.append(parameter + ': ' + runtime + '.Source_Code_Location')
    args.append(source.mapping.wrap(branch_value(source, owner), source.path,
                                    node.start, node.end, 'debug-capture'))
    return owner, parameter


def finish(source, code, mapping):
    if not source.branch_procedures or identity.mode(source) == 'none':
        return code, mapping
    raw = code.encode()
    starts = [0, *[index+1 for index, byte in enumerate(raw) if byte == 10]]
    endpoints = expression_endpoints(source, raw, mapping)

    def position(offset):
        row = bisect_right(starts, offset)-1
        return row+1, len(raw[starts[row]:offset].decode())+1

    def make_row(offset, line, column, end_column, point=False):
        origin = mapping.lookup(offset)
        if origin is None or origin.file != str(source.path):
            return None
        original = endpoints.get(offset, origin.start)
        owner = min((proc for proc in source.branch_procedures
                     if proc.role('body').start <= original < proc.role('body').end),
                    key=lambda proc: proc.end-proc.start, default=None)
        if owner is None:
            return None
        before = source.raw[:original].decode()
        source_line = before.count('\n')+1
        source_column = len(before.rsplit('\n', 1)[-1])+1
        return (identity.number(source, line), identity.number(source, column),
                0 if point else end_column, identity.number(source, source_line),
                identity.number(source, source_column), origin.exact and not point and offset not in endpoints)

    rows = []
    if identity.mode(source) == 'obfuscated':
        # Hashing destroys intervals. Reuse native token boundaries for exact
        # positions, restricted to procedures containing this directive.
        for offset, _, _ in tokens(raw):
            row = make_row(offset, *position(offset), 0, point=True)
            if row is not None:
                rows.append(row)
    else:
        boundaries = set()
        for offset in endpoints:
            byte = raw[offset]
            width = 1 + (byte >= 0xc0) + (byte >= 0xe0) + (byte >= 0xf0)
            boundaries.update((offset, offset+width))
        boundaries = sorted(boundaries)
        for start, end, *_ in mapping.segments:
            cursor = start
            while cursor < end:
                line, column = position(cursor)
                stop = min(end, starts[line] if line < len(starts) else len(raw))
                index = bisect_right(boundaries, cursor)
                if index < len(boundaries):
                    stop = min(stop, boundaries[index])
                end_column = column + len(raw[cursor:stop].decode())
                row = make_row(cursor, line, column, end_column)
                if row is not None:
                    rows.append(row)
                cursor = stop
    rows.sort(key=lambda row: row[:2])
    runtime = source.prefix + 'source_runtime'
    row_type = source.prefix + 'Branch_Location_Row'
    table = source.prefix + 'branch_locations'
    mapper = source.prefix + 'map_branch_location'
    # This footer is appended after normal hygiene, so qualify its native names
    # explicitly without shifting the positions being mapped.
    builtin = source.prefix + 'branch_builtin'
    file = identity.quote(identity.string(source, str(source.path), 'F'))
    footer = f'''
import {builtin} "base:builtin"
@(private="file")
{row_type} :: struct {{
    generated_line, generated_column, end_column: {builtin}.int,
    source_line, source_column: {builtin}.i32,
    exact: {builtin}.bool,
}}
@(private="file", rodata)
{table} := []{row_type}{{
'''
    footer += ''.join('{%d, %d, %d, %d, %d, %s},\n' %
                      (*row[:5], str(row[5]).lower()) for row in rows)
    footer += f'''}}
@(private="file")
{mapper} :: #force_inline proc "contextless"(loc: {runtime}.Source_Code_Location, procedure: {builtin}.string) -> {runtime}.Source_Code_Location {{
    low, high := 0, {builtin}.len({table})
    for low < high {{
        middle := low + (high-low)/2
        row := {table}[middle]
        if row.generated_line < {builtin}.int(loc.line) || row.generated_line == {builtin}.int(loc.line) && row.generated_column <= {builtin}.int(loc.column) {{
            low = middle+1
        }} else {{ high = middle }}
    }}
    index := low-1
    if index >= 0 {{
        row := {table}[index]
        if row.generated_line == {builtin}.int(loc.line) && row.generated_column <= {builtin}.int(loc.column) &&
            (row.end_column == 0 && row.generated_column == {builtin}.int(loc.column) || {builtin}.int(loc.column) < row.end_column) {{
            result := loc
            result.file_path = {file}
            result.line = row.source_line
            result.column = row.source_column
            result.procedure = procedure
            if row.exact {{ result.column += {builtin}.i32({builtin}.int(loc.column)-row.generated_column) }}
            return result
        }}
    }}
    return loc
}}
'''
    encoded, updated = mapping.rewrite(raw, [(len(raw), len(raw), footer,
        Origin(str(source.path), 0, 0, 'source-location-support'))])
    return encoded.decode(), updated


def expression_endpoints(source, raw, mapping):
    endpoints = {}
    for start, end, original, first, last in sorted(mapping.expansions, key=lambda row: row[4]-row[3]):
        if original != str(source.path) or not any(proc.role('body').start <= first < proc.role('body').end
                                                  for proc in source.branch_procedures):
            continue
        last_token = next(reversed(list(tokens(raw[start:end]))), None)
        node = source.at([first, last], source.root)
        if last_token is not None and node is not None:
            endpoints[start+last_token[0]] = original_end_token(node)
    return endpoints


def original_end_token(node):
    roles = {'Binary':'right', 'Unary':'expr', 'Cast':'expr', 'AutoCast':'expr',
             'ExpressionStatement':'expr', 'Selector':'field', 'ImplicitSelector':'field',
             'TypeAssertion':'type', 'FieldValue':'value', 'Ternary':'y', 'WhenExpr':'y', 'OrElse':'y'}
    child = node.role(roles[node.kind]) if node.kind in roles else None
    if node.kind == 'Declaration':
        values = node.group('values')
        child = values[-1] if values else node.role('type')
    if child is not None:
        return original_end_token(child)
    if node.kind in {'Ident','Implicit','Literal','Directive','Unsafe'}:
        return node.start
    if node.kind in {'Call','Index','Slice','Paren','Compound','ProcedureGroup','Interpolation',
                     'Comprehension','SwitchExpr','CompileSwitchExpr'}:
        return node.end-1
    last = next(reversed(list(tokens(node.source.raw[node.start:node.end]))), None)
    return node.start+last[0] if last is not None else node.start

"""Unused source bindings, independent of generated helpers and cleanup."""
from .sourcemap import Origin
from bisect import bisect_left


def condition(project, node, seen=None):
    if node is None:
        return None
    seen = set() if seen is None else seen
    if node in seen:
        return None
    seen.add(node)
    known = node.source.big.constants.payloads.get(node)
    if known and known['kind'] == 'bool':
        return known['value']
    if node.kind == 'Paren':
        return condition(project, node.role('expr'), seen)
    if node.kind == 'Ident':
        if node.text == 'ODIN_DEBUG' and project.resolver.symbol(node) is None:
            return '-debug' in project.flags
        symbol = project.resolver.symbol(node)
        if symbol and symbol.value and not symbol.declaration.data.get('mutable', True):
            return condition(project, symbol.value, seen)
    if node.kind == 'Unary' and node.data['op'] == '!':
        value = condition(project, node.role('expr'), seen)
        return not value if value is not None else None
    if node.kind == 'Binary' and node.data['op'] in {'&&', '||'}:
        left = condition(project, node.role('left'), seen.copy())
        right = condition(project, node.role('right'), seen.copy())
        if node.data['op'] == '&&':
            if left is False or right is False:
                return False
            return True if left is True and right is True else None
        if left is True or right is True:
            return True
        return False if left is False and right is False else None
    from .sliceaccess import Summary
    return project.slice_access.condition(node, Summary(None, {}))


def field_label(resolver, node):
    parent = node.parent
    if parent is None:
        return False
    if node.start < parent.start:  # Statement labels precede the statement span.
        return True
    if parent.kind == 'Other' and parent.text.split()[:1] in (['break'], ['continue']):
        return True
    if parent.kind in {'ImplicitSelector', 'EnumType', 'PolyType'}:
        return True
    if parent.kind == 'Selector':
        return parent.role('field') is node
    if parent.kind == 'FieldValue' and parent.role('field') is node:
        owner = parent.parent
        return (owner is None or owner.kind != 'Compound'
                or resolver.type_of(owner).kind in {'struct', 'record', 'anonymous'})
    return False


def direct_name(node):
    while node and node.kind == 'Paren':
        node = node.role('expr')
    return node if node and node.kind == 'Ident' else None


def explicit_discard(node):
    while node.parent:
        parent = node.parent
        if parent.kind == 'Paren':
            node = parent
            continue
        if parent.kind != 'Assignment' or parent.data['op'] != '=':
            return False
        targets, values = parent.group('lhs'), parent.group('rhs')
        if len(targets) == len(values) and node in values:
            target = direct_name(targets[values.index(node)])
            return target is not None and target.text == '_'
        return False
    return False


def simple_reads(root):
    """Keep calls, escapes and unknown operations conservative; no data flow."""
    reads, pending = [], [root]
    while pending:
        node = pending.pop()
        if node.kind == 'Ident':
            reads.append(node)
        elif node.kind not in {'Literal', 'Paren', 'Binary'} and not (
                node.kind == 'Unary' and node.data['op'] in {'+', '-', '!', '~'}):
            return None
        pending.extend(node.children)
    return reads


def write_only(resolver, source):
    writes = set()
    for node in source.nodes:
        if node.kind == 'Assignment' and node.data['op'] != 'in':
            targets, values = node.group('lhs'), node.group('rhs')
        elif node.kind == 'IncDec':
            targets, values = [node.role('expr')], []
        else:
            continue
        for index, target in enumerate(targets):
            name = direct_name(target)
            symbol = resolver.symbol(name) if name else None
            # A using projection writes through its owner; indexing, fields
            # and pointer dereferences also use the reference being written.
            if (not symbol or symbol.projection
                    or symbol.declaration.kind in {'Range', 'TypeSwitch'}
                    and symbol.node.text.startswith('&')):
                continue
            writes.add(name)
            if len(targets) == len(values):
                reads = simple_reads(values[index])
                if reads is not None:
                    writes.update(read for read in reads if resolver.symbol(read) is symbol)
    return writes


def unused_imports(source, resolver, names):
    """Keep every potential source use, including inactive target branches.

    Imports are file-local. Compiler support imports and synthetic selective
    aliases are deliberately absent from this original-source scan.
    """
    from .compiler import unquote
    from .vet import Fix
    imports = []
    for node in source.nodes:
        if node.kind != 'Import':
            continue
        path = unquote(node.data['op'])
        namespace = node.data['name'] or path.rsplit('/', 1)[-1].rsplit(':', 1)[-1]
        selections = [selection for selection in source.info.get('selections') or []
                      if selection['path'] == node.data['op'] and (
                          node.start <= selection['start'] < node.end
                          or selection['start'] >= node.end and not any(
                              other.kind == 'Import' and node.end <= other.start < selection['start']
                              for other in source.nodes))]
        # A blank namespace without selected names is an intentional import.
        if namespace == '_' and not selections:
            continue
        imports.append((node, path, namespace, selections))
    if not imports:
        return []
    used = set()
    for node in source.nodes:
        if node.kind != 'Ident' or node in names or field_label(resolver, node):
            continue
        symbol = resolver.symbol(node)
        for index, (_, path, namespace, selections) in enumerate(imports):
            if index in used:
                continue
            if namespace != '_' and node.text == namespace and symbol is None:
                used.add(index)
                continue
            if not any(selection['wildcard'] or node.text in selection['names'] for selection in selections):
                continue
            target = resolver.package_symbol(resolver.import_directory(source, path), node.text)
            selected = source.selected.get(node.text, '').split('.', 1)[0]
            if target is not None and symbol is target or symbol is None and (any(
                    node.text in selection['names'] for selection in selections)
                    or source.imports.get(selected) == path):
                used.add(index)
    result = []
    for index, (node, path, _, selections) in enumerate(imports):
        if index in used:
            continue
        end = max([node.end, *(selection['end'] for selection in selections)])
        highlight_end = end
        # Preserve comments, line endings and neighboring same-line statements.
        comments = [source.raw[a:b] for a, b in source.info.get('comments') or []
                    if node.start <= a and b <= end]
        newline = b'\r\n' if b'\r\n' in source.raw else b'\n'
        replacement = newline.join(comments)
        if comments and comments[-1].startswith(b'//'):
            replacement += newline
        after = end
        while after < len(source.raw) and source.raw[after:after+1] in (b' ', b'\t'):
            after += 1
        if source.raw[after:after+1] == b';':
            end = after+1
        start = node.start
        line_start = source.raw.rfind(b'\n', 0, start)+1
        line_end = source.raw.find(b'\n', end)
        line_end = len(source.raw) if line_end < 0 else line_end+1
        if (not source.raw[line_start:start].strip(b' \t\r')
                and not source.raw[end:line_end].strip(b' \t\r\n')):
            start, end = line_start, line_end
            replacement = newline.join(comments)
            if comments and source.raw[:end].endswith(b'\n'):
                replacement += newline
        result.append(Fix(source, start, end, replacement, 'import', path, node.start, highlight_end))
    return result


def datatype(node, resolver, seen=None):
    """Recognize aliases, including instantiated generic record types."""
    if node is None:
        return False
    seen = set() if seen is None else seen
    if node in seen:
        return False
    seen.add(node)
    if node.source.denotes_type(node):
        return True
    if node.kind == 'Paren':
        return datatype(node.role('expr'), resolver, seen)
    if node.kind == 'WhenExpr':
        return all(datatype(node.role(role), resolver, seen.copy()) for role in ('x','y'))
    if node.kind == 'Call':
        callee = node.role('callee')
        while callee is not None and callee not in seen:
            seen.add(callee)
            if callee.kind == 'Paren':
                callee = callee.role('expr')
                continue
            if callee.kind in {'StructType','UnionType'}:
                params = callee.role('params')
                return bool(params and params.children)
            symbol = resolver.symbol(callee)
            if symbol is None or symbol.declaration.data.get('mutable', True):
                return False
            callee = symbol.value
        return False
    symbol = resolver.symbol(node)
    return bool(symbol and not symbol.declaration.data.get('mutable', True)
                and datatype(symbol.value, resolver, seen))


def procedure_value(node, resolver):
    """Constant aliases of procedures/overload groups are procedure bindings."""
    seen = set()
    while node is not None and node not in seen:
        seen.add(node)
        if node.kind == 'Paren':
            node = node.role('expr')
        elif node.kind == 'Procedure':
            return node.role('body') is not None
        elif node.kind == 'ProcedureGroup':
            return True
        elif node.kind in {'Ident','Selector'}:
            symbol = resolver.symbol(node)
            if symbol is None or symbol.declaration.data.get('mutable', True) or symbol.immutable:
                return False
            node = symbol.value
        else:
            return False
    return False


def automatic_procedure(symbol):
    declaration = symbol.declaration
    if symbol.name == 'main' and symbol.scope.kind == 'File':
        return True
    attributes = {(item.role('field') if item.kind == 'FieldValue' else item).text
                  for attribute in declaration.group('attributes') for item in attribute.group('elems')}
    return bool(attributes.intersection({'init','fini','test','export','require'})
                or any(owner.kind == 'ForeignBlock' for owner in declaration.ancestors()))


def loop_fix(symbol):
    from .vet import Fix
    source=symbol.node.source
    loop=symbol.declaration
    names=loop.group('names')
    if len(names)>2 or symbol.node not in names:return None
    index=names.index(symbol.node)
    start,end=symbol.node.start,symbol.node.end
    highlight=end-len(symbol.name.encode())
    prefix=source.raw[start:highlight]
    replacement=prefix[1:]+b'_' if prefix.startswith(b'&') else b'_'
    if index==1 and loop.role('index_type') is None:
        bindings=loop.group('bindings') or names
        gap_start,gap_end=bindings[0].end,bindings[1].start
        comments=source.info.get('comments') or []
        lower=bisect_left(comments,gap_start,key=lambda span:span[0])
        upper=bisect_left(comments,gap_end,key=lambda span:span[0])
        comments=comments[lower:upper]
        comma=next((i for i in range(gap_start,gap_end) if source.raw[i]==ord(',')
                    and not any(a<=i<b for a,b in comments)),None)
        if comma is None:return None
        retained=source.raw[comma+1:gap_end]
        if not comments or not any(c in retained for c in (b'\n',b'\r')):
            start,end=comma,bindings[1].end
            replacement=retained if comments else b''
    return Fix(source,start,end,replacement,'iterator',symbol.name,highlight,symbol.node.end)


def analyze(project, *, include_unused=True):
    root = project.entry.parent if project.file_entry else project.entry
    resolver = project.resolver
    sources = [source for source in project.sources.values()
               if source.thor and not source.excluded and source.path.is_relative_to(root)]
    source_set = set(sources)
    usage_sources = [source for source in list(project.sources.values())
                     if not source.excluded and source.path.is_relative_to(root)]
    candidates = {}
    types = set()
    procedures, automatic = set(), set()
    for symbol in list(resolver.by_decl.values()):
        declaration = symbol.declaration
        if symbol.node.source not in source_set:
            continue
        owner = next((parent for parent in declaration.ancestors()
                      if parent.kind in {'Procedure', 'StructType', 'File'}), None)
        local = (declaration.kind in {'Declaration', 'Range', 'TypeSwitch'}
                 and owner is not None and owner.kind == 'Procedure')
        if declaration.kind == 'Field' and symbol.scope.kind == 'Procedure':
            signature = symbol.scope.role('type')
            local = (symbol.scope.role('body') is not None
                     and declaration.parent is signature.role('params'))
        is_datatype = False
        is_procedure = False
        if (include_unused and declaration.kind == 'Declaration'
                and not declaration.data.get('mutable', True) and symbol.value is not None):
            enabled = resolver.native_enabled
            resolver.native_enabled = True
            try:
                is_datatype = datatype(symbol.value, resolver)
                is_procedure = not is_datatype and not symbol.immutable and procedure_value(symbol.value, resolver)
            finally:
                resolver.native_enabled = enabled
        if local or is_datatype or is_procedure:
            candidates[declaration, symbol.name] = symbol
            if is_datatype:
                types.add((declaration, symbol.name))
            if is_procedure:
                procedures.add((declaration, symbol.name))
                if automatic_procedure(symbol):
                    automatic.add((declaration, symbol.name))
    used, observed, certain_names = set(automatic), set(), set()
    import_fixes = []
    comprehension_loops=set()
    with resolver.cached_queries():
        for source in usage_sources:
            writes = write_only(resolver, source)
            names = set()
            pending = [name for node in source.nodes for name in node.group('names')]
            while pending:
                node = pending.pop()
                if node not in names:
                    names.add(node)
                    pending.extend(node.children)
            if include_unused and source.thor:
                import_fixes.extend(unused_imports(source, resolver, names))
            pending = [(source.root, True)]
            while pending:
                node, certain = pending.pop()
                if node.kind=='Comprehension':comprehension_loops.add(node.role('loop'))
                if certain and node in names:
                    certain_names.add(node)
                if node.kind in {'When', 'WhenExpr'} and node.role('cond'):
                    cond = node.role('cond')
                    pending.append((cond, certain))
                    selected = condition(project, cond)
                    roles = ('body', 'else') if node.kind == 'When' else ('x', 'y')
                    for index, role in enumerate(roles):
                        arm = node.role(role)
                        if arm and (selected is None or selected == (index == 0)):
                            pending.append((arm, certain and selected is not None))
                    continue
                certain = certain and node.kind not in {'CompileSwitch', 'CompileSwitchExpr'}
                if (node.kind == 'Selector' or node.kind == 'Ident' and node not in names and node not in writes
                        and not field_label(resolver, node)):
                    symbol = resolver.symbol(node)
                    projection = symbol is not None and symbol.projection is not None
                    while symbol and symbol.projection:
                        symbol = resolver.symbol(symbol.projection[0])
                    if symbol:
                        key = symbol.declaration, symbol.name
                        if (key in types or key in procedures) and node.source is symbol.node.source and (
                                symbol.declaration.start <= node.start < symbol.declaration.end):
                            continue # Self references alone are not a use.
                        used.add(key)
                        if projection or not explicit_discard(node):
                            observed.add((symbol.declaration, symbol.name))
                pending.extend((child, certain) for child in node.children)
    adapter = project.diagnostic_adapter()
    findings, immutable_exemptions = [], []
    fixes=list(import_fixes)
    for key, symbol in candidates.items():
        if symbol.node not in certain_names:
            continue
        # Unused names and bindings used only in direct discards receive
        # no immutable advice. Any ordinary use makes them eligible again.
        if key not in observed and key not in types:
            immutable_exemptions.append(symbol.node)
        if key in used:
            continue
        node = symbol.node
        start = node.end - len(symbol.name.encode())
        if include_unused and symbol.declaration.kind=='Range' and symbol.declaration not in comprehension_loops:
            fix=loop_fix(symbol)
            if fix:fixes.append(fix)
        is_datatype = key in types
        is_procedure = key in procedures
        code = 'thor/unused-type' if is_datatype else 'thor/unused-procedure' if is_procedure else 'thor/unused'
        message = (f"Type '{symbol.name}' declared but not used" if is_datatype else
                   f"Procedure '{symbol.name}' declared but not used" if is_procedure else f"'{symbol.name}' declared but not used")
        findings.append({'type': 'warning', 'stage': 'vet', 'code': code,
                         'pos': adapter.locate(Origin(str(node.source.path), start, node.end)),
                         'msgs': [message]})
    findings.extend({key: value for key, value in fix.diagnostic(adapter).items() if key != 'fix'} for fix in import_fixes)
    return sorted(findings, key=lambda finding: (finding['pos']['file'], finding['pos']['offset'])), immutable_exemptions, fixes

"""Unused source bindings, independent of generated helpers and cleanup."""
from .sourcemap import Origin


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


def analyze(project):
    root = project.entry.parent if project.file_entry else project.entry
    resolver = project.resolver
    sources = [source for source in project.sources.values()
               if source.thor and not source.excluded and source.path.is_relative_to(root)]
    source_set = set(sources)
    candidates = {}
    for symbol in resolver.by_decl.values():
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
        if local:
            candidates[declaration, symbol.name] = symbol
    used, observed, certain_names = set(), set(), set()
    with resolver.cached_queries():
        for source in sources:
            writes = write_only(resolver, source)
            names = set()
            pending = [name for node in source.nodes for name in node.group('names')]
            while pending:
                node = pending.pop()
                if node not in names:
                    names.add(node)
                    pending.extend(node.children)
            pending = [(source.root, True)]
            while pending:
                node, certain = pending.pop()
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
                if (node.kind == 'Ident' and node not in names and node not in writes
                        and not field_label(resolver, node)):
                    symbol = resolver.symbol(node)
                    projection = symbol is not None and symbol.projection is not None
                    while symbol and symbol.projection:
                        symbol = resolver.symbol(symbol.projection[0])
                    if symbol:
                        used.add((symbol.declaration, symbol.name))
                        if projection or not explicit_discard(node):
                            observed.add((symbol.declaration, symbol.name))
                pending.extend((child, certain) for child in node.children)
    adapter = project.diagnostic_adapter()
    findings, immutable_exemptions = [], []
    for key, symbol in candidates.items():
        if symbol.node not in certain_names:
            continue
        # Unused names and bindings used only in direct discards receive
        # no immutable advice. Any ordinary use makes them eligible again.
        if key not in observed:
            immutable_exemptions.append(symbol.node)
        if key in used:
            continue
        node = symbol.node
        start = node.end - len(symbol.name.encode())
        findings.append({'type': 'warning', 'stage': 'vet', 'code': 'thor/unused',
                         'pos': adapter.locate(Origin(str(node.source.path), start, node.end)),
                         'msgs': [f"'{symbol.name}' declared but not used"]})
    return sorted(findings, key=lambda finding: (finding['pos']['file'], finding['pos']['offset'])), immutable_exemptions

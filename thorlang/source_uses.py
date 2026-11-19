"""Classify source binding names and labels before resolving references."""

def field_label(resolver, node):
    parent = node.parent
    if parent is None:
        return False
    if node.start < parent.start:  # Statement labels precede the statement span.
        return True
    if parent.kind == 'Other' and parent.text.split()[:1] in (['break'], ['continue']):
        return True
    if parent.kind in {'ImplicitSelector', 'EnumType', 'PolyType', 'Attribute'}:
        return True
    if parent.kind == 'Call' and parent.role('callee').text == '#config' and parent.group('args')[:1] == [node]:
        return True
    if parent.kind == 'Selector':
        return parent.role('field') is node
    if parent.kind == 'FieldValue' and parent.role('field') is node:
        owner = parent.parent
        return (owner is None or owner.kind != 'Compound'
                or resolver.type_of(owner).kind in {'struct', 'record', 'anonymous'})
    return False



def names(source):
    result=set()
    pending=[name for node in source.nodes for name in node.group("names")]
    while pending:
        node=pending.pop()
        if node not in result:
            result.add(node)
            pending.extend(node.children)
    return result

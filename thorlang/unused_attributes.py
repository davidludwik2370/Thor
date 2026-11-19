"""Explicitly unavailable declarations, checked before compile-time evaluation."""
from .source_uses import field_label, names as binding_names


def elements(attribute):
    return [item for item in attribute.group('elems')
            if (item.role('field') if item.kind == 'FieldValue' else item).text == 'unused']


def marked(declaration):
    return declaration.source.thor and any(elements(attribute) for attribute in declaration.group('attributes'))


def only_unused_attributes(declaration):
    return declaration.source.thor and all(len(attribute.group('elems')) == len(elements(attribute))
                                          for attribute in declaration.group('attributes'))


def validate(project, *, final=False):
    marked_nodes = {node for source in project.sources.values()
                    if source.thor and not source.excluded and b'unused' in source.raw
                    for node in source.nodes if node.kind == 'Declaration' and marked(node)}
    if not any(source.thor and not source.excluded and b'unused' in source.raw
               and any(node.kind == 'Attribute' and elements(node) for node in source.nodes)
               for source in project.sources.values()):
        return
    spellings = {name.text for node in marked_nodes for name in node.group('names')}
    resolver = project.resolver
    from .vet_unused import condition
    with resolver.cached_queries():
        for source in project.sources.values():
            if source.excluded:
                continue
            names = binding_names(source)
            pending = [(source.root,True)]
            while pending:
                node,certain = pending.pop()
                if (source.thor and node.kind == 'Attribute' and elements(node)
                        and (node.parent is None or node.parent.kind != 'Declaration')):
                    if not certain and not final:continue
                    fail(source,elements(node)[0],
                         '@(unused) applies to variable, constant, type, and procedure declarations.',
                         'thor/unused-attribute')
                if node in marked_nodes and (certain or final):
                    attrs = [item for attribute in node.group('attributes') for item in elements(attribute)]
                    message = None
                    if len(attrs) > 1:
                        message = 'Duplicate @(unused) attribute.'
                    elif attrs[0].kind != 'Ident':
                        message = '@(unused) takes no value; use the bare attribute.'
                    elif all(name.text == '_' for name in node.group('names')):
                        message = '@(unused) requires a named declaration.'
                    else:
                        entry = (source.root.data.get('name') == 'main'
                                 and any(name.text == 'main' for name in node.group('names'))
                                 and not any(owner.kind == 'Procedure' for owner in node.ancestors()))
                        automatic = any((item.role('field') if item.kind == 'FieldValue' else item).text
                                        in {'init','fini','test','export','require'}
                                        and (item.kind != 'FieldValue'
                                             or (known := condition(project,item.role('value'))) is True
                                             or known is None and final)
                                        for attribute in node.group('attributes') for item in attribute.group('elems'))
                        if entry or automatic:
                            message = '@(unused) cannot mark an entry point or an automatically used declaration.'
                    if message:
                        fail(source, attrs[0], message, 'thor/unused-attribute')
                if node.kind in {'When','WhenExpr'} and node.role('cond'):
                    cond = node.role('cond')
                    selected = condition(project, cond)
                    roles = ('body','else') if node.kind == 'When' else ('x','y')
                    pending.extend((arm,certain and selected is not None) for index, role in reversed(list(enumerate(roles)))
                                   if (selected is None or selected == (index == 0)) and (arm := node.role(role)))
                    pending.append((cond,certain))
                    continue
                field = node.role('field') if node.kind == 'Selector' else node
                if ((certain or final) and node.kind in {'Ident','Selector'} and field.text in spellings
                        and (node.kind == 'Selector' or node not in names and not field_label(resolver,node))):
                    symbol = resolver.symbol(node)
                    if symbol and symbol.declaration in marked_nodes:
                        message = f"Cannot use '{symbol.name}': it is marked @(unused). Remove the attribute before using it."
                        fail(source,field,message,'thor/unused-reference',symbol.node)
                pending.extend((child,certain) for child in reversed(node.children))


def fail(source, node, message, code, declaration=None):
    from .compiler import ThorError
    def position(node):
        def coordinates(offset):
            prefix=node.source.raw[:offset].decode()
            return prefix.count('\n')+1,len(prefix.rsplit('\n',1)[-1].encode('utf-16-le'))//2+1
        line,column=coordinates(node.start)
        end_line,end_column=coordinates(node.end)
        return {'file':str(node.source.path),'offset':node.start,'end_offset':node.end,
                'line':line,'column':column,'end_line':end_line,'end_column':end_column}
    error = {'type':'error','stage':'thor','code':code,'pos':position(node),'msgs':[message]}
    if declaration:
        error['related'] = [{'pos':position(declaration),'message':'Declaration marked @(unused) here.'}]
    raise ThorError(message,diagnostics=[error])


def render_attribute(source, node):
    removed = elements(node)
    if not removed:
        return None
    kept = [source.render(item,constant=True) for item in node.group('elems') if item not in removed]
    return '@('+', '.join(kept)+')' if kept else ''

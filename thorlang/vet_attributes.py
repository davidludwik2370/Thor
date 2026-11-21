"""Declaration-local vet suppression. Compiler errors are never suppressed."""
import json


def elements(attribute):
    return [item for item in attribute.group('elems')
            if (item.role('field') if item.kind=='FieldValue' else item).text=='vet_ignore']


def constant_text(node, seen=None):
    if node is None:return None
    seen=set() if seen is None else seen
    if node in seen:return None
    seen=seen|{node}
    if node.kind=='Literal':
        if node.text.startswith('`'):return node.text[1:-1]
        try:
            value=json.loads(node.text)
            return value if isinstance(value,str) else None
        except ValueError:return None
    if node.kind=='Paren':return constant_text(node.role('expr'),seen)
    if node.kind=='Call' and node.role('callee').text=='#config' and len(node.group('args'))==2:
        name,default=node.group('args')
        from .compiler import unquote
        key=unquote(name.text) if name.text.startswith(('"','`')) else name.text
        flags=node.source.purity.project.flags if node.source.purity else []
        value=next((flag.split('=',1)[1] for flag in flags if flag.startswith('-define:'+key+'=')),None)
        if value is None:return constant_text(default,seen)
        if value.lower() in {'true','false','t','f'} or value[:1] in '0123456789+-':return None
        return value[1:-1] if value.startswith("'") and value.endswith("'") else value
    if node.kind=='Binary' and node.data.get('op')=='+':
        a,b=constant_text(node.role('left'),seen),constant_text(node.role('right'),seen)
        return a+b if a is not None and b is not None else None
    symbol=node.source.resolver.symbol(node)
    if symbol and symbol.declaration.kind=='Declaration' and not symbol.declaration.data.get('mutable'):
        return constant_text(symbol.value,seen)
    return None


def rules(declaration):
    items=[item for attribute in declaration.group('attributes') for item in elements(attribute)]
    def fail(item,text):declaration.source.error(text,item.start,item.end)
    if len(items)>1:fail(items[1],'Duplicate @(vet_ignore) attribute.')
    result=set()
    for item in items:
        text=constant_text(item.role('value'))
        if not text or not text.strip():fail(item,'@(vet_ignore) requires a nonempty constant string of vet check names.')
        for name in map(str.strip,text.split(',')):
            name='pure' if name=='fn' else name
            if name not in {'immutable','pure','unused','semicolon'}:
                fail(item,f"Unknown vet check '{name}' in @(vet_ignore). Expected immutable, pure (or fn), unused, or semicolon.")
            result.add(name)
    return result


def validate(project):
    for source in project.sources.values():
        if source.excluded or not source.thor or b'vet_ignore' not in source.raw:continue
        for node in source.nodes:
            if node.kind=='Attribute' and elements(node):
                if not node.parent or node.parent.kind not in {'Declaration','Import'}:
                    source.error('@(vet_ignore) applies to a declaration or import.',node.start,node.end)
                rules(node.parent)


def ignored(source,start,end,rule):
    if not source.thor or b'vet_ignore' not in source.raw:return False
    rule='unused' if rule in {'import','iterator'} else rule
    candidates=[node for node in source.nodes if node.kind in {'Declaration','Import'} and (
        node.start<=start and end<=node.end or rule=='semicolon' and node.end<=start
        and not source.raw[node.end:start].strip() and b'\n' not in source.raw[node.end:start])]
    if not candidates:return False
    owner=min(candidates,key=lambda n:n.end-n.start)
    if any(v.kind=='Procedure' and v.role('body') and v.role('body').start<=start< v.role('body').end
           for v in owner.group('values')):return False
    return rule in rules(owner)

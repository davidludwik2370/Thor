"""Reviewed pmap scheduler identity and its independently checked callback."""
from pathlib import Path
from . import resulttypes


def has_mode_parameter(signature):
    return signature.source.path==Path(__file__).resolve().parents[1]/'runtime/parallel/pmap.thor'


def is_call(resolver,call):
    if call is None or call.kind!='Call':return False
    expected=Path(__file__).resolve().parents[1]/'runtime/parallel'
    if call.source.import_dir('thor:parallel')!=expected:return False
    def visit(node,seen):
        if node is None or node in seen:return False
        seen.add(node)
        if node.kind=='Paren':return visit(node.role('expr'),seen)
        symbol=resolver.symbol(node)
        if not symbol or symbol.immutable or symbol.declaration.data.get('mutable'):return False
        if symbol.name=='pmap' and symbol.node.source.path.parent==expected:return True
        return bool(symbol.value and symbol.value.kind in {'Ident','Selector','Paren'} and visit(symbol.value,seen))
    return visit(call.role('callee'),set())


def callback(resolver,call):
    if not is_call(resolver,call):return None
    for _,signature in resolver.procedure_candidates(call.role('callee')):
        for default in resulttypes.defaults(resolver,signature):
            bound=resulttypes.default_bindings(resolver,signature,call,default)
            if not bound:continue
            bindings,argument,_=bound
            symbol=resolver.symbol(argument)
            if symbol and (symbol.immutable or symbol.declaration.data.get('mutable') or symbol.declaration.kind=='Field'):return None
            result=resulttypes.resolve_default(resolver,call,default.call,bindings,argument)[0]
            if result and result[0].parent and result[0].parent.kind=='Procedure':return argument,result[0].parent
    return None


def explicit_allocator(call,target):
    returns_values=bool(target.role('type').role('results') and target.role('type').role('results').children)
    return (sum(arg.kind!='FieldValue' for arg in call.group('args'))>=(5 if returns_values else 4) or any(
        arg.kind=='FieldValue' and arg.role('field').text in {'allocator','worker_allocator'} for arg in call.group('args')))


def job_field_count(resolver,call):
    selected=callback(resolver,call)
    if selected and not resulttypes.signature_spreads(resolver,selected[1].role('type')):return 1
    jobs=None
    for index,arg in enumerate(call.group('args')):
        if arg.kind=='FieldValue':
            if arg.role('field').text=='jobs':jobs=arg.role('value')
        elif index==0:jobs=arg
    typ=resolver.type_of(jobs).elem if jobs else None
    if typ is None:return None
    if typ.members:return len(typ.members)
    if typ.node:return len(resolver.fields(typ.node.role('fields')))
    return None


def primitive_callback(purity,argument,target):
    if purity.primitive(argument):return True
    declaration=target.parent
    if declaration and declaration.kind=='Declaration':
        return any(purity.primitive(name) for name in declaration.group('names'))
    return False

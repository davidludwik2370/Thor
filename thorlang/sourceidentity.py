"""Source-sensitive native expressions must observe the input, not generated helpers."""
import json
import os
from pathlib import Path


def quote(value):
    return json.dumps(value, ensure_ascii=False)


def procedure(node):
    proc = active_procedure(node)
    if proc is None:
        return ''
    value = proc
    while value.parent and value.parent.kind == 'Paren':
        value = value.parent
    decl = value.parent
    if decl and decl.kind == 'Declaration' and not decl.data.get('mutable',True) and value in decl.group('values'):
        index = decl.group('values').index(value)
        if index < len(decl.group('names')):
            return decl.group('names')[index].text
    return '(anonymous-procedure)'


def active_procedure(node):
    return next((n for n in node.ancestors() if n.kind=='Procedure' and n.role('body') and
                 n.role('body').start<=node.start and node.end<=n.role('body').end),None)


def hash_bytes(value):
    h = 0xcbf29ce484222325
    for byte in value:
        h = ((h ^ byte) * 0x100000001b3) & ((1 << 64)-1)
    return h


def mode(source):
    return next((f.split(':',1)[1] for f in reversed(getattr(source,'compiler_flags',[]))
                 if f.startswith('-source-code-locations:')), 'normal')


def string(source, value, prefix):
    setting = mode(source)
    if setting == 'none': return ''
    if setting == 'filename' and prefix != 'P': return value.rsplit(os.sep,1)[-1]
    if setting == 'obfuscated' and value: return prefix + 'x' + format(hash_bytes(value.encode()), 'x')
    return value


def number(source, value):
    setting = mode(source)
    if setting == 'none': return 0
    if setting == 'obfuscated':
        import sys
        n = hash_bytes(value.to_bytes(4, sys.byteorder, signed=True)) & 0xffffffff
        if n >= 1 << 31: n -= 1 << 32
        if n < 0: n = (1-n) & 0xffffffff
        return n if n < 1 << 31 else n-(1 << 32)
    return value


def location(source, target):
    origin = target.source
    before = origin.raw[:target.start].decode()
    line, column = before.count('\n')+1, len(before.rsplit('\n',1)[-1])+1
    alias = source.expressions.module('source_runtime', 'base:runtime')
    return f'{alias}.Source_Code_Location{{{quote(string(source,str(origin.path),"F"))}, {number(source,line)}, {number(source,column)}, {quote(string(source,location_procedure(target),"P"))}}}'


def location_procedure(node):
    def identity(proc):
        name=procedure(proc.role('body'))
        parent=next((n for n in proc.ancestors() if n.kind=='Procedure'),None)
        prefix=identity(parent) if parent else '_proclit'
        package=proc.source.root.data['name']
        if name != '(anonymous-procedure)':
            if parent is None: return package+'::'+name
            value=proc
            while value.parent and value.parent.kind=='Paren':value=value.parent
            return f'{prefix}::{name}-{value.parent.group("names")[0].start}'
        return f'{prefix}$anon-{package}:{proc.source.path.name}:{proc.start}'
    proc=active_procedure(node)
    return identity(proc) if proc and procedure(node)=='(anonymous-procedure)' else procedure(node)


def directive(source, node):
    if node.kind == 'Directive':
        name = node.data.get('name')
        if name == 'branch_location':
            from . import branch_locations
            return branch_locations.render(source, node)
        if name == 'file': return quote(string(source,str(source.path),'F'))
        if name == 'directory': return quote(string(source,str(source.path.parent)+os.sep,'D'))
        if name == 'line': return str(number(source,source.raw[:node.start].count(b'\n')+1))
        if name == 'procedure' and procedure(node): return quote(string(source,procedure(node),'P'))
    if node.kind == 'Call' and node.role('callee').text == '#location':
        args = node.group('args')
        target = node.role('callee')
        if args:
            symbol = source.resolver.symbol(args[0])
            if symbol is None: return None
            target = symbol.node
        return location(source,target)
    return None


def resource_argument(node):
    p = node.parent
    return bool(p and p.kind == 'Call' and p.group('args')[:1] == [node] and
                p.role('callee').text in {'#load','#load_hash','#load_directory','#exists'})


def resource(source, node, code):
    p = '('+code+')'
    absolute = f"({p}[0]=='/' when len({p})>0 else false)"
    if os.name == 'nt':
        absolute = f"(({p}[0]=='/' || {p}[0]=='\\\\') when len({p})>0 else false) || ({p}[1]==':' when len({p})>1 else false)"
    relative = f'({quote(str(source.path.parent)+os.sep)} + {p})'
    roots = {name:Path(source.info['odin_root'])/name for name in ('base','core','vendor')} if source.info.get('odin_root') else {}
    roots.update(source.collections)
    for name, root in sorted(roots.items()):
        prefix = name+':'
        length = len(prefix)
        relative = f'(({quote(str(root)+os.sep)} + {p}[{length}:]) when ({p}[:{length}]=={quote(prefix)} when len({p})>={length} else false) else {relative})'
    return f'({p} when ({absolute}) else {relative})'


def caller_arguments(source, call, signature=None):
    signature = signature or source.signature(call)
    if signature is None: return []
    fields = [(f,n.text.lstrip('$')) for f in signature.role('params').children for n in f.group('names')]
    if not any(f.role('default') and f.role('default').text.startswith('#caller_') for f,_ in fields): return []
    callee = call.role('callee')
    receiver = callee.kind == 'Selector' and callee.data.get('op') == '->'
    supplied = {fields[0][1]:callee.role('expr')} if receiver and fields else {}
    index = int(receiver)
    for arg in call.group('args'):
        if arg.kind == 'FieldValue': supplied[arg.role('field').text] = arg.role('value')
        else:
            if index < len(fields): supplied[fields[index][1]] = arg
            index += 1
    added = []
    for field,name in fields:
        default = field.role('default')
        if name in supplied or default is None: continue
        if default.text == '#caller_location': value = location(source,callee)
        elif default.text.startswith('#caller_expression'):
            args = default.group('args')
            if args:
                target = args[0].text
                argument = supplied.get(target)
                value = quote(expression(argument))
            else: value = quote(expression(call))
        else: continue
        added.append(name+'='+value)
    args = call.group('args')
    if not added:return []
    text=(', ' if args else '')+', '.join(added)
    return [((args[-1].end if args else call.end-1),
             source.mapping.wrap(text,source.path,call.start,call.end,'source-location-support'))]


def expression(node):
    """Match Odin's AST display without ever observing generated helper text."""
    if node is None: return ''
    r = lambda name: expression(node.role(name))
    items = lambda name: ', '.join(expression(n) for n in node.group(name))
    kind, op = node.kind, node.data.get('op','')
    if kind == 'Binary': return f'{r("left")} {op} {r("right")}'
    if kind == 'Paren': return '('+r('expr')+')'
    if kind == 'Unary': return op+r('expr')
    if kind == 'Deref': return r('expr')+'^'
    if kind == 'Selector': return r('expr')+op+r('field')
    if kind == 'Index': return r('expr')+'['+r('index')+']'
    if kind == 'Slice': return r('expr')+'['+r('low')+':'+r('high')+']'
    if kind == 'FieldValue': return r('field')+' = '+r('value')
    if kind == 'Call':
        args=[expression(a) for a in node.group('args')]
        return r('callee')+'('+', '.join(args)+')'
    if kind == 'Ellipsis': return '..'+r('expr')
    if kind == 'Compound': return r('type')+'{'+items('elems')+'}'
    if kind == 'Cast': return op+'('+r('type')+')'+r('expr')
    if kind == 'AutoCast': return 'auto_cast '+r('expr')
    if kind == 'OrElse': return r('x')+' or_else '+r('y')
    if kind == 'OrReturn': return r('expr')+' or_return'
    if kind == 'Ternary': return (r('cond')+' ? '+r('x')+' : '+r('y')) if op=='?' else (r('x')+' if '+r('cond')+' else '+r('y'))
    if kind == 'WhenExpr': return r('x')+' when '+r('cond')+' else '+r('y')
    if kind == 'ProcedureGroup': return 'proc{'+items('args')+'}'
    if kind == 'ArrayType': return ('#'+op if op else '')+'['+r('len')+']'+r('elem')
    if kind == 'DynamicArrayType': return ('#'+op if op else '')+'[dynamic'+('; '+r('capacity') if node.role('capacity') else '')+']'+r('elem')
    if kind == 'PointerType': return '^'+r('elem')
    if kind == 'MultiPointerType': return '[^]'+r('elem')
    if kind == 'MapType': return 'map['+r('key')+']'+r('value')
    if kind == 'Fields':
        named=any(n.text!='_' for f in node.children for n in f.group('names'))
        return ', '.join(expression(f) if named else expression(f.role('type')) for f in node.children)
    if kind == 'Field':
        names=node.group('names')
        prefix=node.source.raw[node.start:names[0].start].decode().strip() if names else ''
        typ,default=r('type'),r('default')
        return (prefix+' ' if prefix else '')+items('names')+('' if not names else ': ' if typ else ' :')+typ+((' ' if typ else '')+'= '+default if default else '')
    if kind == 'ProcedureType':
        results=node.role('results')
        named=bool(results and any(f.group('names') for f in results.children))
        return 'proc('+r('params')+')'+((' -> '+('(' if named else '')+r('results')+(')' if named else '')) if results else '')
    if kind == 'Procedure':
        return r('type')+(' {...}' if node.role('body') else ' ---')+f' /* {node.source.root.data["name"]}:{node.source.path.name}!{node.start} */'
    return node.text

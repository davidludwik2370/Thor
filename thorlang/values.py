"""Versioned native value protocol and typed Odin reconstruction (no addresses)."""
import json
import re
import sys


class ValueErrorProtocol(ValueError):pass


def validate(value):
    """Validate portable contents without emitting executable reconstruction."""
    expression(value,'T','meta',big=lambda *_:'0')


def decode(data):
    if len(data)>16*1024*1024:raise ValueErrorProtocol('Compile-time result exceeds 16 MiB.')
    try:packet=json.loads(data)
    except (ValueError,UnicodeError) as exc:raise ValueErrorProtocol('Invalid compile-time result protocol.') from exc
    if not isinstance(packet,dict) or packet.get('thor_value')!=1 or 'value' not in packet:
        raise ValueErrorProtocol('Unsupported compile-time result protocol version.')
    return packet['value']


def expression(value,typ,meta,depth=0,big=None):
    if depth>32 or not isinstance(value,dict):raise ValueErrorProtocol('Invalid nested compile-time value.')
    kind=value.get('kind')
    def child(v,t):return expression(v,t,meta,depth+1,big)
    if kind=='specialized':
        names=value.get('types')
        if not isinstance(names,list) or not all(isinstance(v,dict) and v.get('kind')=='string' for v in names):raise ValueErrorProtocol('Invalid specialization metadata.')
        for name in names:child(name,'string')
        return child(value.get('value'),typ)
    if kind in {'big_integer','big_rational'}:
        from .bigvalues import number
        try:number(value)
        except (ValueError,KeyError,TypeError) as exc:raise ValueErrorProtocol(str(exc)) from exc
        if big is None:raise ValueErrorProtocol('Big-number reconstruction requires static numeric backing.')
        return big(value,typ)
    if kind=='bool':
        if type(value.get('value')) is not bool:raise ValueErrorProtocol('Invalid boolean result.')
        return f'{typ}({str(value["value"]).lower()})'
    if kind=='integer':
        v=value.get('value','')
        if not isinstance(v,str) or not re.fullmatch(r'-?\d{1,40}',v):raise ValueErrorProtocol('Invalid integer result.')
        number=int(v)
        if not -(1<<127)<=number<1<<128:raise ValueErrorProtocol('Integer result is out of range.')
        return f'{typ}({number})'
    if kind in {'float','bits','string'}:
        try:raw=bytes.fromhex(value['bytes'])
        except (ValueError,KeyError,TypeError) as exc:raise ValueErrorProtocol('Invalid result bytes.') from exc
        if kind=='string':return typ+'("'+''.join(f'\\x{b:02x}' for b in raw)+'")'
        if len(raw) not in {1,2,4,8,16}:raise ValueErrorProtocol('Unsupported numeric result width.')
        order=value.get('byte_order',sys.byteorder)
        if order not in {'big','little'}:raise ValueErrorProtocol('Invalid byte order.')
        bits=int.from_bytes(raw,order)
        if kind=='float':
            if len(raw) not in {2,4,8}:raise ValueErrorProtocol('Unsupported float result width.')
            return f'{typ}(0h{bits:0{2*len(raw)}x})'
        return f'(transmute({typ})u{8*len(raw)}(0x{bits:x}))'
    if kind in {'complex','quaternion'}:
        count=2 if kind=='complex' else 4
        parts=value.get('values')
        if not isinstance(parts,list) or len(parts)!=count:raise ValueErrorProtocol('Invalid complex/quaternion result.')
        def component(v):
            width=len(bytes.fromhex(v['bytes']))*8
            return child(v,f'f{width}')
        components=[component(v) for v in parts]
        if kind=='quaternion':components=[name+'='+v for name,v in zip(['real','imag','jmag','kmag'],components)]
        return f'{typ}({kind}('+','.join(components)+'))'
    if kind=='sequence':
        items=value.get('values')
        storage=value.get('storage','array')
        if storage not in {'array','slice','dynamic'}:raise ValueErrorProtocol('Invalid sequence storage.')
        if not isinstance(items,list) or type(value.get('nil')) is not bool:raise ValueErrorProtocol('Invalid sequence result.')
        if storage=='dynamic':
            capacity=value.get('capacity')
            if type(capacity) is not int or not len(items)<=capacity<=sys.maxsize:raise ValueErrorProtocol('Invalid dynamic-array capacity.')
            if value['nil'] and capacity and value.get('zero_sized') is not True:raise ValueErrorProtocol('A nil dynamic array cannot have nonzero-sized capacity.')
        if value['nil'] and not items:
            return f'{typ}(nil)'
        if value['nil'] and value.get('zero_sized') is not True:raise ValueErrorProtocol('A nil sequence cannot have nonzero-sized elements.')
        element=f'{meta}.type_elem_type({typ})'
        return f'{typ}{{'+','.join(child(v,element) for v in items)+'}'
    if kind=='bitset':
        items=value.get('values')
        if not isinstance(items,list):raise ValueErrorProtocol('Invalid bit-set result.')
        element=f'{meta}.type_bit_set_elem_type({typ})'
        return f'{typ}{{'+','.join(child(v,element) for v in items)+'}'
    if kind in {'record','enum_array'}:
        fields=value.get('fields')
        if not isinstance(fields,list):raise ValueErrorProtocol('Invalid record result.')
        seen=set();parts=[]
        for pair in fields:
            if not isinstance(pair,list) or len(pair)!=2 or not isinstance(pair[0],str) or not pair[0].isidentifier() or pair[0] in seen:
                raise ValueErrorProtocol('Invalid record field.')
            name,v=pair;seen.add(name)
            ft=f'type_of(({typ}{{}}).{name})' if kind=='record' else f'{meta}.type_elem_type({typ})'
            parts.append(('' if kind=='record' else '.')+name+'='+child(v,ft))
        return ('#partial ' if kind=='enum_array' else '')+f'{typ}{{'+','.join(parts)+'}'
    if kind=='union':
        index=value.get('variant')
        if type(index) is not int or index< -1:raise ValueErrorProtocol('Invalid union variant.')
        if index==-1:return f'{typ}{{}}'
        vt=f'{meta}.type_variant_type_of({typ},{index})'
        return f'{typ}('+child(value.get('value'),vt)+')'
    raise ValueErrorProtocol('Unsupported compile-time value kind.')


def has_owned(value, *, slices_only=False):
    kind=value['kind']
    if kind=='specialized':return has_owned(value['value'],slices_only=slices_only)
    if kind in {'string','big_integer','big_rational'}:return not slices_only
    if kind=='sequence':
        if value.get('storage') in {'slice','dynamic'} or value.get('nil'):return True
        return any(has_owned(v,slices_only=slices_only) for v in value['values'])
    if kind in {'record','enum_array'}:return any(has_owned(v,slices_only=slices_only) for _,v in value['fields'])
    if kind=='union' and value['variant']>=0:return has_owned(value['value'],slices_only=slices_only)
    return False


def has_dynamic(value):
    kind=value['kind']
    if kind=='specialized':return has_dynamic(value['value'])
    if kind=='sequence':return value.get('storage')=='dynamic' or any(has_dynamic(v) for v in value['values'])
    if kind in {'record','enum_array'}:return any(has_dynamic(v) for _,v in value['fields'])
    if kind=='union' and value['variant']>=0:return has_dynamic(value['value'])
    return False


def materialize(value,typ,meta,module,prefix,big=None):
    """Construct independent storage directly, without allocating templates."""
    statements=[]
    serial=0
    def build(v,t):
        nonlocal serial
        if not has_owned(v):return expression(v,t,meta,big=big)
        kind=v['kind']
        if kind=='specialized':return build(v['value'],t)
        if kind in {'big_integer','big_rational'}:return f'{module}.clone({expression(v,t,meta,big=big)})'
        if kind=='string':return f'{module}.clone({expression(v,t,meta,big=big)})'
        if kind=='sequence':
            elem=f'{meta}.type_elem_type({t})'
            if v.get('storage')=='dynamic':
                serial+=1;name=prefix+str(serial)
                statements.append(f'{name}:={module}.allocate_dynamic({t},{len(v["values"])},{v["capacity"]},{str(not v["nil"]).lower()})')
                for i,item in enumerate(v['values']):statements.append(f'{name}[{i}]={build(item,elem)}')
                return name
            if v.get('storage')=='slice' or v['nil']:
                serial+=1;name=prefix+str(serial)
                statements.append(f'{name}:={module}.allocate_slice({t},{len(v["values"])},{str(not v["nil"]).lower()})')
                for i,item in enumerate(v['values']):statements.append(f'{name}[{i}]={build(item,elem)}')
                return name
            return f'{t}{{'+','.join(build(item,elem) for item in v['values'])+'}'
        if kind in {'record','enum_array'}:
            parts=[]
            for name,item in v['fields']:
                field_type=f'type_of(({t}{{}}).{name})' if kind=='record' else f'{meta}.type_elem_type({t})'
                parts.append(('' if kind=='record' else '.')+name+'='+build(item,field_type))
            return ('#partial ' if kind=='enum_array' else '')+f'{t}{{'+','.join(parts)+'}'
        if kind=='union':
            variant=f'{meta}.type_variant_type_of({t},{v["variant"]})'
            return f'{t}({build(v["value"],variant)})'
        return expression(v,t,meta,big=big)
    result=build(value,typ)
    return '\n'.join([*statements,'return '+result])

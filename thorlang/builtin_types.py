"""Shapes of compiler-defined values; effect checks still inspect arguments."""
from dataclasses import replace


def result(resolver, call, index=0, seen=None):
    from .semantics import Type
    purity=getattr(call.source,'purity',None)
    name=purity.builtin(call) if purity else None
    if not name:
        name=next((n for n in ['soa_unzip','swizzle','expand_values','compress_values','raw_data','size_of','align_of','offset_of'] if resolver.native_builtin_alias(call.role('callee'),n)),None)
    if name in {'size_of','align_of','offset_of'}:return Type('scalar','uintptr' if name=='offset_of' else 'int',untyped=name!='offset_of') if index==0 else None
    if name not in {'expand_values','compress_values','soa_unzip','min','max','abs','clamp','conj','real','imag','jmag','kmag','complex','quaternion','swizzle','raw_data','copy'}:return None
    args=[arg.role('value') if arg.kind=='FieldValue' else arg for arg in call.group('args')]
    if not name or not args:return None
    typ=resolver.type_node(args[0]) if call.source.denotes_type(args[0]) else resolver.type_of(args[0],set(seen or ()))
    def numeric_type():
        result=typ
        for arg in args[1:]:
            candidate=resolver.type_of(arg,set(seen or ()))
            if result.untyped and (not candidate.untyped or candidate.name.startswith('f')):result=candidate
        return result
    if name=='expand_values':
        if typ.kind=='array':return typ.elem if index<(typ.length or 0) else Type()
        if typ.members:return list(typ.members.values())[index] if index<len(typ.members) else Type()
        fields=resolver.fields(typ.node.role('fields')) if typ.node else []
        return resolver.type_node(fields[index][1],typ.substitutions) if index<len(fields) else Type()
    if name=='soa_unzip':
        fields=typ.elem.members if typ.soa and typ.elem else {}
        return Type('slice',elem=list(fields.values())[index]) if index<len(fields) else Type()
    if index:return None
    if name in {'min','max'}:
        ellipsis=call.data.get('roles',{}).get('ellipsis')
        packed=ellipsis and ellipsis[0]>=0 and args[0].start>=ellipsis[1]
        return typ.elem or Type() if packed or args[0].kind=='Unary' and args[0].data.get('op')=='**' else numeric_type()
    if name in {'abs','clamp','conj','real','imag','jmag','kmag'}:
        if name in {'clamp','conj'}:return numeric_type() if name=='clamp' else typ
        scalar={'complex32':'f16','complex64':'f32','complex128':'f64','quaternion64':'f16','quaternion128':'f32','quaternion256':'f64'}.get(typ.name)
        return Type('scalar',scalar) if scalar else typ
    if name in {'complex','quaternion'}:
        if typ.kind in {'unknown','generic'}:return typ
        bits={'f16':16,'f32':32}.get(numeric_type().name,64)*(2 if name=='complex' else 4)
        return Type('scalar',f'{name}{bits}')
    if name=='swizzle':return Type('array',elem=typ.elem,length=len(args)-1) if typ.kind=='array' else Type()
    if name=='compress_values':
        groups=[]
        for arg in args:
            count=1
            if arg.kind=='Call':
                plan=resolver.resolve_call(arg)
                if plan:count=max(1,len(resolver.fields(plan.signature.role('results'))))
                elif resolver.native_builtin_alias(arg.role('callee'),'expand_values'):
                    expanded=resolver.type_of(arg.group('args')[0])
                    if expanded.kind=='array' and expanded.length is not None:
                        groups.append((expanded.elem,expanded.length));continue
                    count=len(expanded.members) if expanded.members else len(resolver.fields(expanded.node.role('fields'))) if expanded.node else 0
            groups.extend((replace(resolver.result_type(arg,i,set(seen or ())),untyped=False),1) for i in range(count))
        if not groups:return Type()
        total=sum(n for _,n in groups)
        if total==1:return groups[0][0]
        def shape(t):
            return (t.kind,t.name,t.nominal,t.length,t.node if t.kind in {'struct','union','enum'} else None,
                    shape(t.elem) if t.elem else None,tuple((n,shape(v) if isinstance(v,Type) else v) for n,v in sorted(t.substitutions.items())))
        same=all(shape(t)==shape(groups[0][0]) for t,_ in groups)
        if same:return Type('array',elem=groups[0][0],length=total)
        if total>max(4096,len(call.source.nodes)*4):return Type()
        types=(t for t,n in groups for _ in range(n))
        return Type('struct',members={f'v{i}':t for i,t in enumerate(types)})
    if name=='raw_data':return Type('pointer',elem=Type('scalar','u8') if typ.kind=='string' else typ.elem or Type())
    if name=='copy':return Type('scalar','int')
    return None

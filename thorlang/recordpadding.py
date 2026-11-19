"""Native compile-time layout proofs for ignored, unnamed struct fields."""


def catalog(sources):
    layouts=sorted({(record.data.get('struct_flags',''),tuple(
        (name.text,('using ' if field.data.get('using_field') else '')+('#subtype ' if field.data.get('subtype_field') else ''),field.data.get('field_tag',''))
        for field in (record.role('fields') or record).children if field.kind=='Field'
        for name in field.group('names')))
        for source in sources for record in source.nodes
        if record.kind=='StructType' and record.data.get('op')!='raw_union'
        and any(name.text=='_' for field in (record.role('fields') or record).children
                if field.kind=='Field' for name in field.group('names'))})
    return [(flags,fields) for flags,fields in layouts
            if len({name for name,_,_ in fields if name!='_'})==sum(name!='_' for name,_,_ in fields)]


def count(sources, meta, builtin, prefix, *, cache=None):
    stamp=(id(sources),len(sources))
    saved=getattr(cache,'record_padding_layouts',None)
    if saved is None or saved[0]!=stamp:
        layouts=catalog(sources)
        if cache is not None:cache.record_padding_layouts=(stamp,layouts)
    else:layouts=saved[1]
    if not layouts:return '', '0', []
    types=prefix+'padding_types';result=prefix+'padding_count'
    total=meta+'.type_struct_field_count(T)'
    code=f'\n{types}::struct{{value:({builtin}.type_of({builtin}.compress_values({builtin}.expand_values(T{{}}))) when {total}>0 else struct{{}})}}\n'
    tuple_type=f'{meta}.type_field_type({types},"value")'
    using_indices={index for _,fields in layouts for index,(_,flags,_) in enumerate(fields) if flags}
    for index in range(max(len(fields) for _,fields in layouts)):
        typ=f'{prefix}padding_type_{index}'
        code+=f'{typ}::struct{{value:(({tuple_type} when {total}==1 else ({meta}.type_elem_type({tuple_type}) when {meta}.type_is_array({tuple_type}) else {meta}.type_field_type({tuple_type},"v{index}"))) when {total}>{index} else struct{{}})}}\n'
        if index in using_indices:
            deref=typ+'_base'
            actual=f'{meta}.type_field_type({typ},"value")'
            code+=f'{deref}::struct{{value:({meta}.type_elem_type({actual}) when {actual}!=rawptr && {meta}.type_is_pointer({actual}) else {actual})}}\n'
            base=f'{meta}.type_field_type({deref},"value")'
            code+=f'{typ}_using::struct{{value:({actual} when {meta}.type_is_struct({base}) || {meta}.type_is_bit_field({base}) || ({builtin}.len({base})<=4 when {meta}.type_is_array({base}) else false) else struct{{}})}}\n'
    cases=[]
    for flags,fields in layouts:
        schema=[]
        for index,(name,field_flags,tag) in enumerate(fields):
            typ=f'{meta}.type_field_type({prefix}padding_type_{index}'+('_using' if field_flags else '')+',"value")'
            schema.append(field_flags+name+':'+typ+' '+tag)
        padding=sum(name=='_' for name,_,_ in fields)
        align='' if '#packed' in flags else f' #align({builtin}.align_of(T))'
        cases.append(f'({padding} when ({meta}.type_base_type(T)==struct{flags}{align}{{'+','.join(schema)+f'}} when {total}=={len(fields)} else false) else 0)')
    def maximum(items):
        if len(items)==1:return items[0]
        middle=len(items)//2
        return builtin+'.max('+maximum(items[:middle])+','+maximum(items[middle:])+')'
    code+=f'{result}::{maximum(cases)}\n'
    fields=[f'{meta}.type_field_type({prefix}padding_type_{index},"value")'
            for index in range(max(len(fields) for _,fields in layouts))]
    return code,result,fields

"""Unevaluated call-result records and dependent type defaults for libraries."""
from dataclasses import dataclass
from .semantics import Type, descendants


def error(node, message):
    node.source.error(message,node.start,node.end)


def literal(source, callback):
    if callback.source is not source:return None
    node=callback
    while node.kind=='Paren':node=node.role('expr')
    if node.kind!='Procedure':return None
    name=source.prefix+f'result_callback_{node.start}'
    code='\n'+name+'::'+source.render(node)+'\n'
    source.expressions.helpers[source.expressions.helper_scope(callback),name]=source.mapping.wrap(code,source.path,node.start,node.end,'result-callback')
    return name


def is_query(resolver, node):
    if node is None or node.kind != 'Call': return False
    def intrinsic(callee, seen):
        if callee is None or callee in seen: return False
        seen.add(callee)
        if callee.kind == 'Paren': return intrinsic(callee.role('expr'),seen)
        symbol=resolver.symbol(callee)
        if not symbol or symbol.immutable or symbol.declaration.data.get('mutable',False): return False
        directory=callee.source.import_dir('thor:intrinsics')
        if symbol.name=='result_type_of' and directory==symbol.node.source.path.parent: return True
        return bool(symbol.value and symbol.value.kind in {'Ident','Selector','Paren'} and intrinsic(symbol.value,seen))
    return intrinsic(node.role('callee'),set())


def expression(query):
    args=query.group('args')
    if len(args)!=1:return None
    call=args[0]
    while call.kind=='Paren':call=call.role('expr')
    return call if call.kind=='Call' else None


@dataclass
class Default:
    field: object
    name: str
    query: object
    call: object
    callback: int


def defaults(resolver, signature):
    parameters=signature.role('params')
    if parameters is None:return []
    names=[name for name,_ in resolver.fields(parameters)]
    found=[]
    for field in parameters.children:
        query=field.role('default')
        if not is_query(resolver,query):continue
        call=expression(query)
        symbol=resolver.symbol(call.role('callee')) if call else None
        if symbol and symbol.name in names:
            found.append(Default(field,field.group('names')[0].text.lstrip('$'),query,call,names.index(symbol.name)))
    return found


def arrange(resolver,signature,call):
    fields=resolver.fields(signature.role('params'))
    names=[name for name,_ in fields]
    values={};position=0
    for arg in call.group('args'):
        if arg.kind=='FieldValue':values[arg.role('field').text]=arg.role('value')
        else:
            if position>=len(names):return None
            values[names[position]]=arg;position+=1
    for field in signature.role('params').children:
        for name in field.group('names'):
            key=name.text.lstrip('$')
            if key not in values and field.role('default'):values[key]=field.role('default')
    return values


def argument_types(resolver,call,bindings):
    types=[]
    for arg in call.group('args'):
        if arg.kind=='FieldValue':arg=arg.role('value')
        if arg.kind=='Unary' and arg.data.get('op')=='**':
            operand=arg.role('expr')
            typ=resolver.type_node(operand.role('type'),bindings) if operand.kind=='Compound' and operand.role('type') else resolver.type_node(operand,bindings)
            if typ.kind=='array' and isinstance(typ.length,int):types.extend([typ.elem]*typ.length)
            elif typ.members:types.extend(typ.members.values())
            elif typ.node:
                for _,annotation in resolver.fields(typ.node.role('fields')):types.append(resolver.type_node(annotation,typ.substitutions))
        else:
            typ=resolver.type_node(arg,bindings)
            types.append(resolver.type_of(arg) if typ.kind=='unknown' else typ)
    return types


def resolve(resolver,call,bindings=None,callback=None):
    enabled=resolver.native_enabled
    resolver.native_enabled=True
    try:return _resolve(resolver,call,bindings,callback)
    finally:resolver.native_enabled=enabled


def _resolve(resolver,call,bindings=None,callback=None):
    bindings=bindings or {}
    callee=callback or call.role('callee')
    candidates=resolver.procedure_candidates(callee)
    if not candidates:
        typ=resolver.type_node(callee,bindings)
        if typ.kind=='unknown':typ=resolver.type_of(callee)
        if typ.kind=='procedure' and typ.node:candidates=[(None,typ.node)]
    actuals=argument_types(resolver,call,bindings)
    matches=[]
    for _,signature in candidates:
        parameters=resolver.fields(signature.role('params'))
        formals=[formal for _,formal in parameters]
        variadic=bool(formals and formals[-1] and formals[-1].kind=='Ellipsis')
        if len(actuals)>len(parameters) and not variadic:continue
        if len(actuals)<len(parameters):
            required=sum(len(field.group('names')) for field in signature.role('params').children
                         if not field.role('default') and not field.data.get('variadic'))
            if len(actuals)<required:continue
        substitutions=dict(bindings);score=0
        if variadic:
            fixed=len(parameters)-1
            formals=formals[:fixed]+[formals[-1].role('expr')]*max(0,len(actuals)-fixed)
        for formal,actual in zip(formals,actuals):
            amount=resolver.unify(formal,actual,substitutions)
            if amount<0:
                if len(candidates)>1:break
                continue
            score+=amount
        else:
            fields=[(name if name and name!='_' else f'v{i}',resolver.type_node(annotation,substitutions))
                    for i,(name,annotation) in enumerate(resolver.fields(signature.role('results')))]
            matches.append((score,signature,fields))
    if not matches:return None
    best=max(score for score,_,_ in matches)
    matches=[(signature,fields) for score,signature,fields in matches if score==best]
    return matches[0] if len(matches)==1 else None


def packed(result):
    fields=result[1]
    if len(fields)==1:return fields[0][1]
    return Type('struct',members=dict(fields)) if fields else Type()


def default_bindings(resolver,signature,site,default):
    arranged=arrange(resolver,signature,site)
    if arranged is None:return None
    bindings={}
    params=resolver.fields(signature.role('params'))
    for name,formal in params:
        if name in arranged:resolver.unify(formal,resolver.type_of(arranged[name]),bindings)
    callback=arranged.get(params[default.callback][0])
    return (bindings,callback,arranged) if callback else None


def type_code(source,query,call,result):
    fields=result[1]
    if not fields:error(query,'result_type_of: the call has no return values and therefore no result type.')
    if len({name for name,_ in fields})!=len(fields):error(query,'result_type_of: result field names collide. Name the results explicitly or supply an explicit result_type.')
    source.needs_intrinsics=True
    meta=source.prefix+'meta'
    signature=f'type_of({meta}.procedure_of({call}))'
    types=[f'{meta}.type_proc_return_type({signature},{i})' for i in range(len(fields))]
    if len(fields)==1:return types[0]
    return 'struct{'+','.join(name+':'+typ for (name,_),typ in zip(fields,types))+'}'


def render(source,node,constant=False):
    resolver=source.resolver
    if resolver is None:return None
    if node.kind=='Field' and is_query(resolver,node.role('default')):
        annotation=node.role('type')
        if annotation is None or annotation.kind!='TypeidType':error(node,"A result_type_of parameter default requires an explicit '$name: typeid' parameter.")
        return source.render(node.group('names')[0])+': typeid'
    if is_query(resolver,node):
        call=expression(node)
        if call is None:error(node,'result_type_of requires exactly one call expression, for example result_type_of(calculate(**Job{})).')
        result=resolve(resolver,call)
        if result is None:error(node,"result_type_of cannot resolve the callback's result signature from these argument types.")
        args=call.group('args')
        empty_spread=len(args)==1 and args[0].kind=='Unary' and args[0].data.get('op')=='**' and not argument_types(resolver,call,{})
        callee=call.role('callee');replacement=literal(source,callee)
        saved=source.run_values.get(callee)
        if replacement:source.run_values[callee]=replacement
        try:code=source.render(callee)+'()' if empty_spread else source.render(call)
        finally:
            if replacement:
                if saved is None:source.run_values.pop(callee,None)
                else:source.run_values[callee]=saved
        return source.mapping.wrap(type_code(source,node,code,result),source.path,node.start,node.end,'result-type')
    if node.kind!='Call':return None
    for _,signature in resolver.procedure_candidates(node.role('callee')):
        queries=defaults(resolver,signature)
        if not queries:continue
        default=queries[0]
        bound=default_bindings(resolver,signature,node,default)
        if not bound:continue
        bindings,callback,parameters=bound
        result=resolve(resolver,default.call,bindings,callback)
        if result is None:error(node,'Cannot resolve the mapping callback from the job field types. Job fields must match callback parameters in order.')
        names=[name for name,_ in resolver.fields(signature.role('params'))]
        callback_literal=literal(source,callback)
        replacements={}
        for name,annotation in resolver.fields(signature.role('params')):
            actual=parameters.get(name)
            if actual is None or actual.source is not source:continue
            for part in descendants(default.call):
                if part.kind=='Ident' and part.text==name:replacements[part]=callback_literal if name==names[default.callback] and callback_literal else source.render(actual)
            if annotation and annotation.kind=='ArrayType' and not annotation.role('len'):
                elem=annotation.role('elem')
                if elem and elem.kind=='PolyType':
                    for part in descendants(default.call):
                        if part.kind=='Ident' and part.text==elem.data['name']:replacements[part]=f'type_of(({source.render(actual)})[0])'
        cursor=default.call.start;pieces=[]
        for part,code in sorted(replacements.items(),key=lambda item:item[0].start):
            if part.start<cursor:continue
            pieces.extend([part.source.text(cursor,part.start),code]);cursor=part.end
        pieces.append(default.call.source.text(cursor,default.call.end));witness=''.join(pieces)
        query_args=default.call.group('args')
        if len(query_args)==1 and query_args[0].kind=='Unary' and query_args[0].data.get('op')=='**' and not argument_types(resolver,default.call,bindings):
            witness=(callback_literal or source.render(callback))+'()'
        fields=result[1]
        supplied=parameters.get(default.name) is not default.query
        if not fields and supplied:error(node,'The callback has no return values; result_type cannot be supplied.')
        helper=source.prefix+f'result_call_{node.start}'
        result_name=source.prefix+'Result'
        params=[];args=[];arranged={};explicit=None;bound_values={}
        callback_name=names[default.callback]
        enabled=resolver.native_enabled;resolver.native_enabled=True
        try:candidates=resolver.procedure_candidates(callback)
        finally:resolver.native_enabled=enabled
        specialized=len(candidates)>1 or any(part.kind=='PolyType' for part in descendants(result[0]))
        for index,arg in enumerate(node.group('args')):
            name=arg.role('field').text if arg.kind=='FieldValue' else names[index]
            value=arg.role('value') if arg.kind=='FieldValue' else arg
            if name==default.name:explicit=source.render(value,constant=True);continue
            argument=source.prefix+f'arg_{len(params)}'
            if name==callback_name and specialized:
                source.needs_intrinsics=True
                callback_type=source.prefix+f'Callback_{len(params)}'
                callback_signature=f'type_of({source.prefix}meta.procedure_of({witness}))'
                params.append('$'+callback_type+':typeid');args.append(callback_signature)
                params.append(argument+':'+callback_type)
                bound_values[name]='cast('+callback_signature+')nil'
            else:params.append(f'{argument}:${source.prefix}Arg_{len(params)}')
            arranged[name]=argument
            args.append(callback_literal if name==callback_name and callback_literal else source.render(value,constant=constant))
        native=[]
        type_position=names.index(default.name)
        for index,name in enumerate(names):
            if name==default.name:
                if fields:native.append(result_name)
            elif name in arranged:native.append(arranged[name] if index<type_position else name+'='+arranged[name])
        if fields:
            params.insert(0,f'${result_name}:typeid');args.insert(0,explicit or type_code(source,node,witness,result))
        target=source.render(node.role('callee'))
        from . import sourceidentity
        location=''.join(code for _,code in sourceidentity.caller_arguments(source,node,signature))
        native_call=target+'('+','.join(native)+location+')'
        return_count=len(resolver.fields(signature.role('results'))) if fields else 0
        output_types=[]
        if return_count:
            source.needs_intrinsics=True
            bound=[]
            for index,name in enumerate(names):
                if name==default.name:code=args[0]
                elif name in bound_values:code=bound_values[name]
                elif name in arranged:
                    at=next(i for i,param in enumerate(params) if param.startswith(arranged[name]+':'))
                    code=args[at]
                else:continue
                bound.append((name+'=' if index>type_position else '')+code)
            bound_call=target+'('+','.join(bound)+')'
            for i in range(return_count):
                output=source.prefix+f'Output_{i}'
                params.append('$'+output+':typeid');output_types.append(output)
                args.append(f'{source.prefix}meta.type_proc_return_type(type_of({source.prefix}meta.procedure_of({bound_call})),{i})')
        returns='->('+','.join(output_types)+')' if return_count else ''
        body=('return ' if return_count else '')+native_call
        declaration=f'\n{helper}::proc({",".join(params)}){returns}{{{body}}}\n'
        source.expressions.helpers[source.expressions.helper_scope(node),helper]=source.mapping.wrap(declaration,source.path,node.start,node.end,'result-default')
        return source.mapping.wrap(helper+'('+','.join(args)+')',source.path,node.start,node.end,'result-default')
    return None

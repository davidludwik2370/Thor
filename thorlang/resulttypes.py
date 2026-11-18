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
    checked=bool(source.purity and node in source.purity.calls.callback_entries)
    if checked:
        source.purity.calls.emitting.add(node)
        source.purity.calls.emitting_callbacks.add(node)
    try:code='\n'+name+'::'+source.render(node)+'\n'
    finally:
        if checked:
            source.purity.calls.emitting.discard(node)
            source.purity.calls.emitting_callbacks.discard(node)
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


@dataclass
class Results:
    signature: object
    fields: list
    arguments: list
    spread: object = None

    def __getitem__(self,index):return (self.signature,self.fields)[index]
    def __iter__(self):return iter((self.signature,self.fields))


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


def signature_spreads(resolver,signature):
    parameters=resolver.fields(signature.role('params'))
    return len(parameters)!=1 or bool(parameters[-1][1] and parameters[-1][1].kind=='Ellipsis')


def resolve_default(resolver,site,call,bindings,callback):
    from . import parallelcompile
    ambiguous=[]
    result=resolve(resolver,call,bindings,callback,parallelcompile.is_call(resolver,site),ambiguous)
    return result,bool(ambiguous)


def constraint(resolver,node,bindings):
    if node.kind=='Paren':return constraint(resolver,node.role('expr'),bindings)
    if node.text in ('true','false'):return node.text=='true'
    if node.kind=='Unary' and node.data.get('op')=='!':
        value=constraint(resolver,node.role('expr'),bindings)
        return not value if value is not None else None
    if node.kind=='Binary':
        left,right=node.role('left'),node.role('right');op=node.data.get('op')
        if op in ('&&','||'):
            a,b=constraint(resolver,left,bindings),constraint(resolver,right,bindings)
            if op=='&&':return False if a is False or b is False else True if a is True and b is True else None
            return True if a is True or b is True else False if a is False and b is False else None
        if op in ('==','!='):
            a=bindings.get(left.text) or resolver.type_node(left,bindings)
            b=bindings.get(right.text) or resolver.type_node(right,bindings)
            if isinstance(a,Type) and isinstance(b,Type) and a.kind not in ('unknown','generic') and b.kind not in ('unknown','generic'):
                return (a==b)==(op=='==')
    return None


def resolve(resolver,call,bindings=None,callback=None,mapping=False,ambiguous=None):
    enabled=resolver.native_enabled
    resolver.native_enabled=True
    try:
        from . import parallelcompile
        if callback is None and parallelcompile.is_call(resolver,call):
            candidates=resolver.procedure_candidates(call.role('callee'))
            for _,signature in candidates:
                for default in defaults(resolver,signature):
                    bound=default_bindings(resolver,signature,call,default)
                    if not bound:continue
                    result=resolve_default(resolver,call,default.call,bound[0],bound[1])[0]
                    if not result:return None
                    explicit=bound[2].get(default.name)
                    fields=[('v0',Type('slice',elem=resolver.type_of(explicit) if explicit is not None and explicit is not default.query else packed(result)))] if result.fields else []
                    selected=signature if fields else next((s for _,s in candidates if not resolver.fields(s.role('results'))),signature)
                    return Results(selected,fields,argument_types(resolver,call,bindings or {}))
            return None
        return _resolve(resolver,call,bindings,callback,mapping,ambiguous)
    finally:resolver.native_enabled=enabled


def _resolve(resolver,call,bindings=None,callback=None,mapping=False,ambiguous=None):
    bindings=bindings or {}
    callee=callback or call.role('callee')
    candidates=resolver.procedure_candidates(callee)
    if not candidates:
        typ=resolver.type_node(callee,bindings)
        if typ.kind=='unknown':typ=resolver.type_of(callee)
        if typ.kind=='procedure' and typ.node:candidates=[(None,typ.node)]
    actuals=argument_types(resolver,call,bindings)
    job=None
    if mapping:
        operand=call.group('args')[0]
        if operand.kind=='Unary':operand=operand.role('expr')
        job=resolver.type_node(operand.role('type'),bindings) if operand.kind=='Compound' else resolver.type_node(operand,bindings)
    matches=[]
    for _,signature in candidates:
        parameters=resolver.fields(signature.role('params'))
        spread=signature_spreads(resolver,signature) if mapping else None
        if mapping:
            if not spread:actuals=[job]
            elif job.kind!='struct':continue
            elif job.members:actuals=list(job.members.values())
            elif job.node:actuals=[resolver.type_node(t,job.substitutions) for _,t in resolver.fields(job.node.role('fields'))]
            else:actuals=[]
        formals=[formal for _,formal in parameters]
        variadic=bool(formals and formals[-1] and formals[-1].kind=='Ellipsis')
        if len(actuals)>len(parameters) and not variadic:continue
        if len(actuals)<len(parameters):
            from . import parallelcompile
            required=sum(len(field.group('names')) for field in signature.role('params').children
                         if not field.role('default') and not field.data.get('variadic') and not (
                             parallelcompile.has_mode_parameter(signature) and any(name.text.lstrip('$')=='__thor_pmap_spread' for name in field.group('names'))))
            if len(actuals)<required:continue
        substitutions=dict(bindings);score=0
        if variadic:
            fixed=len(parameters)-1
            formals=formals[:fixed]+[formals[-1].role('expr')]*max(0,len(actuals)-fixed)
        for formal,actual in zip(formals,actuals):
            expected=resolver.type_node(formal,substitutions) if formal else Type()
            amount=0 if expected.name=='any' else resolver.unify(formal,actual,substitutions)
            if amount<0:
                if len(candidates)>1:break
                continue
            score+=amount
        else:
            if mapping and len(candidates)>1 and signature.parent and any(constraint(resolver,c,substitutions) is False for c in signature.parent.group('where')):continue
            fields=[(name if name and name!='_' else f'v{i}',resolver.type_node(annotation,substitutions))
                    for i,(name,annotation) in enumerate(resolver.fields(signature.role('results')))]
            matches.append((score,Results(signature,fields,list(actuals),spread)))
    if not matches:return None
    if mapping and {result.spread for _,result in matches}=={False,True}:
        if ambiguous is not None:ambiguous.append(True)
        return None
    best=max(score for score,_ in matches)
    matches=[result for score,result in matches if score==best]
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
    return signature_code(source,query,f'type_of({source.prefix}meta.procedure_of({call}))',result)


def signature_code(source,query,signature,result):
    fields=result[1]
    if not fields:error(query,'result_type_of: the call has no return values and therefore no result type.')
    if len({name for name,_ in fields})!=len(fields):error(query,'result_type_of: result field names collide. Name the results explicitly or supply an explicit result_type.')
    source.needs_intrinsics=True
    meta=source.prefix+'meta'
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
        result,ambiguous=resolve_default(resolver,node,default.call,bindings,callback)
        if result is None:
            from . import parallelcompile
            error(node,'Ambiguous pmap callback: both whole-element and argument-bundle overloads match. Select a specific callback.' if ambiguous else
                'Cannot resolve the mapping callback: a single-parameter callback receives the whole slice element; multiple or variadic parameters require matching struct job fields in order.' if parallelcompile.is_call(resolver,node) else
                'Cannot resolve the mapping callback from the job field types. Job fields must match callback parameters in order.')
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
        if result.spread is not None:
            job_type=f'type_of(({source.render(parameters["jobs"])})[0]){{}}'
            if result.spread:
                # Keep arity visible: native overload selection can precede ** expansion.
                import json
                typ=resolver.type_of(parameters['jobs']).elem
                fields=list((typ.members or {}).items()) if typ else []
                if typ and typ.node and not fields:fields=resolver.fields(typ.node.role('fields'))
                source.needs_intrinsics=True
                jobs_code=source.render(parameters['jobs'])
                arguments=','.join(f'{source.prefix}meta.type_field_type(type_of(({jobs_code})[0]),{json.dumps(name)}){{}}' for name,_ in fields)
            else:arguments=job_type
            witness=(callback_literal or source.render(callback))+'('+arguments+')'
        fields=result[1]
        from . import parallelcompile
        action=not fields and parallelcompile.is_call(resolver,node)
        supplied=parameters.get(default.name) is not default.query
        if action:supplied=any(arg.kind=='FieldValue' and arg.role('field').text==default.name for arg in node.group('args'))
        if not fields and supplied:error(node,'The callback has no return values; result_type cannot be supplied.')
        helper=source.prefix+f'result_call_{node.start}'
        result_name=source.prefix+'Result'
        params=[];args=[];arranged={};explicit=None;bound_values={};known_value=None
        callback_name=names[default.callback]
        enabled=resolver.native_enabled;resolver.native_enabled=True
        try:candidates=resolver.procedure_candidates(callback)
        finally:resolver.native_enabled=enabled
        from . import parallelcompile
        checked_mapping=bool(source.purity and parallelcompile.is_call(resolver,node) and
            (source.purity.checked(source.purity.owner(node)) or source.purity.eval_mode and node in source.purity.calls.targets))
        static_callback=result.spread is not None and parallelcompile.known_callback(resolver,callback)
        specialized=static_callback or checked_mapping or len(candidates)>1 or any(part.kind=='PolyType' for part in descendants(result[0]))
        constant_parameters={name.text.lstrip('$') for field in signature.role('params').children
                             for name in field.group('names') if name.kind=='PolyType'}
        for index,arg in enumerate(node.group('args')):
            name=arg.role('field').text if arg.kind=='FieldValue' else names[index+int(action and index>=names.index(default.name))]
            value=arg.role('value') if arg.kind=='FieldValue' else arg
            if name=='__thor_pmap_spread' and result.spread is not None:error(arg,"pmap's argument mode is inferred and cannot be supplied explicitly.")
            if name==default.name:explicit=source.render(value,constant=True);continue
            argument=(source.prefix+f'known_callback_{node.start}' if static_callback and name==callback_name else source.prefix+f'arg_{len(params)}')
            if name==callback_name and specialized:
                source.needs_intrinsics=True
                callback_type=source.prefix+f'Callback_{len(params)}'
                callback_signature=f'type_of({source.prefix}meta.procedure_of({witness}))'
                if not static_callback:
                    params.append('$'+callback_type+':typeid');args.append(callback_signature)
                    params.append(argument+':'+callback_type)
                bound_values[name]='cast('+callback_signature+')nil'
            elif name in constant_parameters:
                params.append(f'${argument}:${source.prefix}Arg_{len(params)}')
                bound_values[name]=source.render(value,constant=True)
            else:params.append(f'{argument}:${source.prefix}Arg_{len(params)}')
            arranged[name]=argument
            from . import parallelcompile
            checked=source.purity.calls.targets.get(node,[]) if name==callback_name and source.purity and parallelcompile.is_call(resolver,node) else []
            target=checked[0] if checked else None
            if target and target in source.purity.calls.names and callback_literal is None:
                value_code=source.purity.calls.access(source,target)+'_pmap'
            else:value_code=callback_literal if name==callback_name and callback_literal else source.render(value,constant=constant)
            if static_callback and name==callback_name:known_value=value_code
            else:args.append(value_code)
        native=[]
        type_position=names.index(default.name)
        for index,name in enumerate(names):
            if name=='__thor_pmap_spread' and result.spread is not None:continue
            if name==default.name:
                if fields:native.append(result_name)
            elif name in arranged:native.append(arranged[name] if index<type_position else name+'='+arranged[name])
        if result.spread is not None:native.append('__thor_pmap_spread='+str(result.spread).lower())
        if fields:
            inferred=(signature_code(source,node,f'type_of({source.render(callback)})',result)
                      if result.spread is not None and not static_callback else type_code(source,node,witness,result))
            params.insert(0,f'${result_name}:typeid');args.insert(0,explicit or inferred)
        entry='_pmap_static_checked' if checked_mapping else '_pmap_static'
        target=(source.expressions.module('parallel','thor:parallel')+'.'+entry if static_callback else
                source.expressions.module('parallel','thor:parallel')+'.pmap_checked' if checked_mapping else source.render(node.role('callee')))
        from . import sourceidentity
        location=''.join(code for _,code in sourceidentity.caller_arguments(source,node,signature))
        native_call=target+'('+','.join(native)+location+')'
        return_count=len(resolver.fields(signature.role('results'))) if fields else 0
        output_types=[]
        if return_count:
            source.needs_intrinsics=True
            bound=[]
            for index,name in enumerate(names):
                if name=='__thor_pmap_spread' and result.spread is not None:continue
                if name==default.name:code=args[0]
                elif name in bound_values:code=bound_values[name]
                elif name in arranged:
                    at=next(i for i,param in enumerate(params) if param.startswith(arranged[name]+':'))
                    code=args[at]
                else:continue
                bound.append((name+'=' if index>type_position else '')+code)
            if result.spread is not None:bound.append('__thor_pmap_spread='+str(result.spread).lower())
            # This unevaluated witness uses a typed nil callback. Keep the
            # ordinary signature rather than requiring a constant procedure.
            bound_target=source.render(node.role('callee')) if static_callback else target
            bound_call=bound_target+'('+','.join(bound)+')'
            for i in range(return_count):
                output=source.prefix+f'Output_{i}'
                params.append('$'+output+':typeid');output_types.append(output)
                args.append(f'{source.prefix}meta.type_proc_return_type(type_of({source.prefix}meta.procedure_of({bound_call})),{i})')
        returns='->('+','.join(output_types)+')' if return_count else ''
        checks=[]
        if checked_mapping:
            purity=source.purity
            for target in purity.targets(node):
                bound=purity.borrowing.bindings(node,target,purity.borrowing.environment(purity.owner(node))) if purity.owner(node) else []
                for access in purity.borrowing.requirements.get(target,()):
                    condition=purity.borrowing.resolve(bound,access).render(purity,source)
                    token=source.expressions.error(node,'Pure pmap cannot mutate or delete job storage borrowed from an input.')
                    import json
                    checks.append('when '+condition+' {#panic('+json.dumps(token)+')}')
        if known_value is not None:
            job_type=f'type_of(({arranged["jobs"]})[0])'
            if result.spread:
                import json
                typ=resolver.type_of(parameters['jobs']).elem
                job_fields=list((typ.members or {}).items()) if typ else []
                if typ and typ.node and not job_fields:job_fields=resolver.fields(typ.node.role('fields'))
                arguments=','.join(f'{source.prefix}meta.type_field_type({job_type},{json.dumps(name)}){{}}' for name,_ in job_fields)
            else:arguments=job_type+'{}'
            checks.insert(0,arranged[callback_name]+'::'+source.prefix+'meta.procedure_of('+known_value+'('+arguments+'))')
        body='\n'.join(checks)+('\n' if checks else '')+('return ' if return_count else '')+native_call
        declaration=f'\n{helper}::proc({",".join(params)}){returns}{{{body}}}\n'
        source.expressions.helpers[source.expressions.helper_scope(node),helper]=source.mapping.wrap(declaration,source.path,node.start,node.end,'result-default')
        return source.mapping.wrap(helper+'('+','.join(args)+')',source.path,node.start,node.end,'result-default')
    return None

"""Automatic caller metadata is a procedure contract, including through aliases."""


def params(signature):
    return [(name,field) for field in signature.role('params').children if field.kind=='Field' for name in field.group('names')]


def descriptor(field,fields):
    if not field.data.get('automatic'):return None
    default=field.role('default')
    if default is None:return None
    if default.text=='#caller_location':return 'location'
    if default.text=='#caller_expression':return 'call'
    args=default.group('args')
    if default.role('callee') and default.role('callee').text=='#caller_expression' and len(args)==1 and args[0].kind=='Ident':
        return next(('argument:'+str(index) for index,(name,_) in enumerate(fields) if name.text.lstrip('$')==args[0].text),None)
    return None


def contract(signature):
    fields=params(signature)
    return [descriptor(field,fields) for _,field in fields]


def record_fields(resolver,typ):
    if typ.kind!='struct':return []
    if typ.members:return list(typ.members.items())
    if not typ.node:return []
    fields=typ.node.role('fields')
    return [(name.text,resolver.type_node(field.role('type'),typ.substitutions))
            for field in (fields.children if fields else typ.node.group('fields')) if field.kind=='Field'
            for name in field.group('names')]


def ro(name):return name.start in {row[0] for row in name.source.info.get('ro_parameters') or []}


def conversion(project,expected,actual,mutating=None):
    if expected.kind in {'scalar','string','enum','bitset','big_integer','big_rational','unknown','generic'}:return
    resolver=project.resolver
    if actual.kind in {'Paren','AutoCast','Runtime'}:return conversion(project,expected,actual.role('expr'),mutating)
    if actual.kind=='Compound':
        fields=record_fields(resolver,expected)
        for index,item in enumerate(actual.group('elems')):
            value=item.role('value') if item.kind=='FieldValue' else item
            label=item.role('field').text if item.kind=='FieldValue' else None
            target=expected.elem if expected.kind in {'array','slice','dynamic','map','set','fixed'} else next((t for n,t in fields if n==label),None) if label else fields[index][1] if index<len(fields) else None
            if target:conversion(project,target,value,mutating)
    compare_types(project,expected,resolver.type_of(actual),actual,mutating,True,set())


def compare_types(project,expected,typ,actual,mutating,direct,seen):
    key=(expected.kind,expected.node,typ.kind,typ.node)
    if key in seen:return
    seen.add(key)
    if expected.kind==typ.kind=='procedure' and expected.node and typ.node:
        expected=expected.node.role('type') if expected.node.kind=='Procedure' else expected.node
        signature=typ.node.role('type') if typ.node.kind=='Procedure' else typ.node
        a,b=contract(expected),contract(signature)
        if (any(a) or any(b)) and a!=b:
            actual.source.error('Incompatible automatic caller metadata parameters in procedure conversion. Preserve each :> metadata parameter and its capture target in the callback type.',actual.start,actual.end)
        if mutating is not None:
            for (expected_name,_),(actual_name,_) in zip(params(expected),params(signature)):
                if not ro(expected_name) or ro(actual_name):continue
                inspectable=signature.parent and signature.parent.kind=='Procedure' and signature.parent.role('body')
                if not direct or not inspectable or not fixed_procedure_value(project,actual) or (str(signature.source.path),actual_name.start) in mutating:
                    actual.source.error('A #ro callback parameter requires a procedure that preserves read-only slice access; this procedure may mutate or expose that input.',actual.start,actual.end)
        for role in ('params','results'):
            for (_,a),(_,b) in zip(project.resolver.fields(expected.role(role)),project.resolver.fields(signature.role(role))):
                if a and b:compare_types(project,project.resolver.type_node(a),project.resolver.type_node(b),actual,mutating,False,seen)
    elif expected.elem and typ.elem:compare_types(project,expected.elem,typ.elem,actual,mutating,False,seen)
    elif expected.kind==typ.kind=='struct':
        for (_,a),(_,b) in zip(record_fields(project.resolver,expected),record_fields(project.resolver,typ)):
            compare_types(project,a,b,actual,mutating,False,seen)


def binding_shape(typ,seen=None):
    if not hasattr(typ,'kind'):return typ
    seen=set() if seen is None else seen
    if id(typ) in seen:return (typ.kind,typ.node)
    seen=seen|{id(typ)}
    return (typ.kind,typ.name,typ.node,binding_shape(typ.elem,seen) if typ.elem else None,
            tuple(sorted((key,binding_shape(value,seen)) for key,value in typ.substitutions.items())))


def validate(project, *, permissions=False):
    if not any(s.thor and not s.excluded and (s.info.get('auto_parameters') or permissions and s.info.get('ro_parameters')) for s in project.sources.values()):return
    resolver=project.resolver
    mutating={(row['file'],row['offset']) for row in project.slice_access.metadata()} if permissions else None
    for source in project.sources.values():
        if source.excluded:continue
        for signature in source.nodes:
            if signature.kind!='ProcedureType' or not active(project,signature):continue
            fields=params(signature);seen=False
            for index,(name,field) in enumerate(fields):
                if not field.data.get('automatic'):
                    if seen:source.error('Automatic :> caller metadata parameters must follow ordinary input parameters.',name.start,name.end)
                    continue
                seen=True
                if name.text=='_' or descriptor(field,fields) is None:
                    source.error('A :> parameter requires #caller_expression, #caller_expression(earlier_parameter), or #caller_location and a parameter name.',field.start,field.end)
                args=field.role('default').group('args')
                if args and not any(n.text.lstrip('$')==args[0].text and not f.data.get('automatic') for n,f in fields[:index]):
                    source.error('#caller_expression in a :> parameter must name an earlier ordinary input parameter.',field.start,field.end)
            if not seen:continue
            if signature.data.get('calling_convention','') not in {'','odin','contextless'} or any(n.kind=='ForeignBlock' for n in signature.ancestors()):
                source.error('Automatic :> caller metadata parameters cannot be used with a foreign calling convention or foreign declaration.',signature.start,signature.end)
            proc=signature.parent
            decl=proc.parent if proc and proc.kind=='Procedure' else None
            if decl and decl.kind=='Declaration':
                main=source.root.data.get('name')=='main' and any(n.text=='main' for n in decl.group('names'))
                entry=any((item.role('field') if item.kind=='FieldValue' else item).text in {'export','init','fini','test'}
                          and (not item.role('value') or item.role('value').text!='false')
                          for attribute in decl.group('attributes') for item in attribute.group('elems'))
                if main or entry:source.error('Automatic :> caller metadata parameters cannot be used on exported or automatically invoked entry points.',signature.start,signature.end)
    pending=[(source,None,{}) for source in list(project.sources.values()) if not source.excluded]
    visited=set()
    previous=resolver.bound_types
    from .semantics import descendants
    try:
        while pending:
            source,body,bindings=pending.pop()
            key=(source,body,frozenset((k,binding_shape(v)) for k,v in bindings.items()))
            if key in visited:continue
            visited.add(key)
            resolver.bound_types={**previous,**bindings}
            generic_bodies={proc:proc.role('body') for proc in source.nodes if proc.kind=='Procedure' and proc.role('body') and resolver.generic_parameters(proc.role('type'))}
            for node in descendants(body) if body else source.nodes:
                scope=next((generic_bodies[p] for p in node.ancestors() if p in generic_bodies and generic_bodies[p].start<=node.start<generic_bodies[p].end),None)
                if scope is not None and scope is not body:continue
                if not active(project,node,bindings):continue
                if node.kind=='Call':
                    callee=node.role('callee');args=node.group('args')
                    if resolver.known_type_operand(callee):
                        if len(args)==1:conversion(project,resolver.type_node(callee),args[0],mutating)
                        continue
                    plan=resolver.resolve_call(node)
                    if plan and plan.substitutions:
                        proc=plan.signature.parent
                        if proc and proc.kind=='Procedure' and proc.role('body'):
                            from pathlib import Path
                            if not proc.source.thor and proc.source.path.is_relative_to(Path(__file__).resolve().parents[1]/'runtime'):continue
                            bound=dict(bindings)
                            for poly in descendants(plan.signature):
                                if poly.kind=='PolyType' and poly.data.get('name') in plan.substitutions:
                                    bound[poly,poly.data['name']]=plan.substitutions[poly.data['name']]
                                    if poly.parent and poly in poly.parent.group('names'):bound[poly.parent,poly.data['name']]=plan.substitutions[poly.data['name']]
                            pending.append((proc.source,proc.role('body'),bound))
                    signature=plan.signature if plan else source.signature(node)
                    if not signature:continue
                    fields=params(signature)
                    index=int(callee.kind=='Selector' and callee.data.get('op')=='->')
                    for arg in args:
                        if arg.kind=='FieldValue':
                            position=next((i for i,(n,_) in enumerate(fields) if n.text.lstrip('$')==arg.role('field').text),None)
                        else:
                            position=index
                            if index>=len(fields) or not fields[index][1].data.get('variadic'):index+=1
                        if position is None or position>=len(fields):continue
                        name,field=fields[position]
                        if field.data.get('automatic'):
                            source.error(f"Caller metadata parameter '{name.text}' is automatic (:>) and cannot be supplied explicitly.",arg.start,arg.end)
                        if field.role('type'):conversion(project,resolver.type_node(field.role('type'),plan.substitutions if plan else None),arg.role('value') if arg.kind=='FieldValue' else arg,mutating)
                elif node.kind in {'Declaration','Compound','Cast'} and node.role('type'):
                    values=node.group('values') if node.kind=='Declaration' else [node] if node.kind=='Compound' else [node.role('expr')]
                    for value in values:conversion(project,resolver.type_node(node.role('type')),value,mutating)
                elif node.kind=='Assignment':
                    for lhs,rhs in zip(node.group('lhs'),node.group('rhs')):conversion(project,resolver.type_of(lhs),rhs,mutating)
                elif node.kind=='Return':
                    proc=next((p for p in node.ancestors() if p.kind=='Procedure'),None)
                    if proc:
                        for value,(_,annotation) in zip(node.group('values'),resolver.fields(proc.role('type').role('results'))):
                            if annotation:conversion(project,resolver.type_node(annotation),value,mutating)
    finally:resolver.bound_types=previous


def in_default(node):
    for field in node.ancestors():
        if field.kind!='Field' or not field.parent or not field.parent.parent:continue
        signature=field.parent.parent
        if signature.kind!='ProcedureType' or signature.role('params') is not field.parent:continue
        default=field.role('default')
        if default and default.start<=node.start and node.end<=default.end and (
                default.text=='#caller_location' or default.text.startswith('#caller_expression')):return True
    return False


def fixed_procedure_value(project,node,seen=None):
    seen=set() if seen is None else seen
    if node in seen:return False
    seen=seen|{node}
    if node.kind in {'Paren','AutoCast'}:return fixed_procedure_value(project,node.role('expr'),seen)
    if node.kind=='Procedure':return True
    symbol=project.resolver.symbol(node)
    if not symbol or symbol.declaration.kind!='Declaration' or (symbol.declaration.data.get('mutable') and not symbol.immutable):return False
    return bool(symbol.value and fixed_procedure_value(project,symbol.value,seen))


def reader_targets(project):
    if not any(source.info.get('ro_parameters') for source in project.sources.values()):return set()
    resolver=project.resolver;targets=set()
    def collect(expected,actual):
        if actual.kind in {'Paren','AutoCast'}:return collect(expected,actual.role('expr'))
        if expected.kind=='procedure' and expected.node:
            signature=expected.node
            if not any(ro(name) for name,_ in params(signature)) or not fixed_procedure_value(project,actual):return
            typ=resolver.type_of(actual)
            signature=typ.node if typ.kind=='procedure' else None
            if signature and signature.kind=='Procedure':signature=signature.role('type')
            if signature and signature.parent and signature.parent.kind=='Procedure' and signature.parent.role('body'):targets.add(signature.parent)
            return
        if actual.kind!='Compound':return
        fields=record_fields(resolver,expected)
        for index,item in enumerate(actual.group('elems')):
            label=item.role('field').text if item.kind=='FieldValue' else None
            value=item.role('value') if item.kind=='FieldValue' else item
            typ=expected.elem or next((t for n,t in fields if n==label),None) if label else expected.elem or (fields[index][1] if index<len(fields) else None)
            if typ:collect(typ,value)
    for source in list(project.sources.values()):
        if source.excluded:continue
        for node in source.nodes:
            if not active(project,node):continue
            if node.kind in {'Declaration','Compound','Cast'} and node.role('type'):
                typ=resolver.type_node(node.role('type'))
                values=node.group('values') if node.kind=='Declaration' else [node] if node.kind=='Compound' else [node.role('expr')]
                for value in values:collect(typ,value)
            elif node.kind=='Assignment':
                for lhs,rhs in zip(node.group('lhs'),node.group('rhs')):collect(resolver.type_of(lhs),rhs)
            elif node.kind=='Call':
                callee=node.role('callee');args=node.group('args')
                if resolver.known_type_operand(callee):
                    if len(args)==1:collect(resolver.type_node(callee),args[0])
                    continue
                signature=source.signature(node)
                if not signature:continue
                fields=params(signature)
                for index,arg in enumerate(args):
                    label=arg.role('field').text if arg.kind=='FieldValue' else None
                    field=next((f for name,f in fields if name.text==label),None) if label else fields[index][1] if index<len(fields) else None
                    if field:collect(resolver.type_node(field.role('type')),arg.role('value') if label else arg)
            elif node.kind=='Return':
                proc=next((n for n in node.ancestors() if n.kind=='Procedure'),None)
                if proc:
                    for value,(_,annotation) in zip(node.group('values'),resolver.fields(proc.role('type').role('results'))):
                        if annotation:collect(resolver.type_node(annotation),value)
    return targets


def active(project,node,bindings=None):
    bindings={key[1] if isinstance(key,tuple) else key:value for key,value in (bindings or {}).items()}
    for ancestor in node.ancestors():
        if ancestor.kind!='When':continue
        cond=ancestor.role('cond')
        selected=project.resolver.constant_int(cond)
        if selected is None and bindings and cond.kind=='Binary' and cond.data.get('op') in {'==','!='}:
            left,right=cond.role('left'),cond.role('right')
            from .semantics import Type
            def type_operand(n):return isinstance(bindings.get(n.text),Type) or project.resolver.known_type_operand(n)
            if type_operand(left) and type_operand(right):
                a=project.resolver.type_node(left,bindings);b=project.resolver.type_node(right,bindings)
                if a.kind not in {'unknown','generic'} and b.kind not in {'unknown','generic'}:
                    selected=project.slice_access.same_type(a,b)==(cond.data.get('op')=='==')
            else:
                a=bindings.get(left.text,project.resolver.constant_int(left));b=bindings.get(right.text,project.resolver.constant_int(right))
                if isinstance(a,(int,bool)) and isinstance(b,(int,bool)):selected=(a==b)==(cond.data.get('op')=='==')
        if cond and cond.text in {'true','false'}:selected=int(cond.text=='true')
        if selected is None:continue
        arm=ancestor.role('body' if selected else 'else')
        if not arm or not arm.start<=node.start<arm.end:return False
    return True


def arrange(signature,args):
    fields=params(signature);groups=[[] for _ in fields];index=0;named=False
    for arg in args:
        if arg.kind=='FieldValue':
            named=True;i=next((i for i,(name,_) in enumerate(fields) if name.text==arg.role('field').text),None)
            if i is None or groups[i]:return None
            groups[i].append(arg.role('value'))
        else:
            if named or index>=len(fields):return None
            groups[index].append(arg)
            if not fields[index][1].data.get('variadic'):index+=1
    for i,(_,field) in enumerate(fields):
        if not groups[i]:
            if field.role('default'):groups[i]=[field.role('default')]
            elif not field.data.get('variadic'):return None
    return groups

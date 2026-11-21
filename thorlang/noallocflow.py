"""Bounded allocation-analysis facts, separate from type checking/code emission."""
from .semantics import descendants

UNKNOWN=()


class Flow:
    def __init__(self,check):self.c=check;self.r=check.r;self.values={};self.interests={};self.terminated=False;self.escaped=set()

    def snapshot(self):return dict(self.values),self.terminated,set(self.escaped)
    def restore(self,state):self.values=dict(state[0]);self.terminated=state[1];self.escaped=set(state[2])
    def opaque_write(self):self.values={k:UNKNOWN if k in self.escaped else self.clear_pointees(v) for k,v in self.values.items()}
    def address_taken(self,node):
        for _ in range(32):
            if node is None:return
            symbol=self.r.symbol(node)
            if symbol and symbol.projection:node=symbol.projection[0];continue
            key=self.key(node)
            if key is not None:self.escaped.add(key);return
            node=node.role('expr')
    def ambient(self,value):
        if not value:return False
        if value[0] in {'ambient_random','ambient_random_callback'}:return True
        if value[0]=='record':return any(self.ambient(v) for _,v in value[1])
        if value[0]=='pointer':return self.ambient(value[2])
        if value[0] in {'stream','buffered','buffered_reader'}:return self.ambient(value[1])
        if value[0]=='pack':return any(self.ambient(v) for v in value[1])
        return False
    def join(self,state):
        if state[1]:return
        if self.terminated:self.restore(state);return
        self.values={k:v if state[0].get(k)==v else UNKNOWN for k,v in self.values.items()}
        self.escaped.update(state[2])

    def key(self,node):
        symbol=self.r.symbol(node)
        if symbol and symbol.declaration.data.get('mutable'):
            if not any(a.kind=='Procedure' for a in symbol.declaration.ancestors()):return None
            if any((item.role('field') if item.kind=='FieldValue' else item).text=='static'
                   for attr in symbol.declaration.group('attributes') for item in attr.group('elems')):return None
        return symbol.node if symbol else None

    def deref(self,value):
        return self.values.get(value[1],value[2]) if value and value[0]=='pointer' else value

    def fact(self,node,depth=0):
        if node is None or depth>24:return UNKNOWN
        if (key:=self.key(node)) in self.values:return self.values[key]
        if node.kind=='Ident' and node.text=='nil':return ('nil',)
        if (value:=self.c.io.value(node,depth)) is not None:return value
        if (value:=self.c.random.value(node,depth)) is not None:return value
        kind=node.kind
        if node.text in {'true','false'}:return 'const',node.text=='true'
        if kind=='Literal' and node.text.startswith(('"','`')):
            from .compiler import unquote
            return 'const',unquote(node.text)
        if (value:=self.r.constant_int(node)) is not None:return 'const',value
        if kind in {'Paren','Runtime','FieldValue'}:return self.fact(node.role('value' if kind=='FieldValue' else 'expr'),depth+1)
        if kind=='Procedure':return 'function',node
        if kind=='ImplicitSelector':return 'atom',node.text.lstrip('.')
        if kind in {'Ident','Selector'}:
            if kind=='Selector':
                base=self.deref(self.fact(node.role('expr'),depth+1))
                if base and base[0]=='record':return dict(base[1]).get(node.role('field').text,UNKNOWN)
                if base==('ambient_random',) and node.role('field').text=='procedure':return ('ambient_random_callback',)
                if self.r.type_of(node).kind=='enum':return 'atom',node.role('field').text
            from .parallelcompile import known_callback
            if known_callback(self.r,node):
                candidates=self.r.procedure_candidates(node)
                if len(candidates)==1 and candidates[0][1].parent.kind=='Procedure':return 'function',candidates[0][1].parent
            symbol=self.r.symbol(node)
            if symbol and not symbol.declaration.data.get('mutable') and symbol.value:return self.fact(symbol.value,depth+1)
        if kind=='Unary' and node.data.get('op')=='&':return 'pointer',self.key(node.role('expr')),self.fact(node.role('expr'),depth+1)
        if kind=='Deref':return self.deref(self.fact(node.role('expr'),depth+1))
        if kind in {'Cast','AutoCast'}:return self.fact(node.role('expr'),depth+1)
        if kind=='Compound':
            elems=node.group('elems')
            if len(elems)>32:return UNKNOWN
            typ=self.r.type_of(node)
            fields=self.r.fields(typ.node.role('fields')) if typ.node else []
            return 'record',tuple((item.role('field').text if item.kind=='FieldValue' else fields[i][0] if i<len(fields) else str(i),
                self.fact(item.role('value') if item.kind=='FieldValue' else item,depth+1)) for i,item in enumerate(elems))
        if kind=='Ternary':
            cond=self.condition(node.role('cond'))
            if cond is not None:return self.fact(node.role('x' if cond else 'y'),depth+1)
            a,b=self.fact(node.role('x'),depth+1),self.fact(node.role('y'),depth+1)
            return a if a==b else UNKNOWN
        return UNKNOWN

    def normalize(self,value,typ):
        if typ.kind!='enum' or not value:return value
        if value[0]=='enum':return value if value[1] is typ.node else UNKNOWN
        if value[0]=='const' and type(value[1]) is int:return 'enum',typ.node,value[1]
        if value[0]=='atom':
            ordinal=-1
            for member in typ.node.group('members'):
                name=member.role('field') if member.kind=='FieldValue' else member
                if member.kind=='FieldValue':
                    n=self.r.constant_int(member.role('value'))
                    if n is None:return UNKNOWN
                    ordinal=n
                else:ordinal+=1
                if name.text==value[1]:return 'enum',typ.node,ordinal
        return UNKNOWN

    def condition(self,node):
        if node is None:return None
        if node.kind=='Paren':return self.condition(node.role('expr'))
        if node.kind=='Unary' and node.data.get('op')=='!':
            value=self.condition(node.role('expr'));return not value if value is not None else None
        if node.kind=='Binary':
            op=node.data.get('op');left,right=node.role('left'),node.role('right')
            if op in {'&&','||'}:
                a,b=self.condition(left),self.condition(right)
                if op=='&&':return False if a is False or b is False else True if a is True and b is True else None
                return True if a is True or b is True else False if a is False and b is False else None
            if op in {'==','!='}:
                typ=self.r.type_of(left)
                a,b=self.normalize(self.fact(left),typ),self.normalize(self.fact(right),typ)
                if a and b and not self.ambient(a) and not self.ambient(b):return (a==b) if op=='==' else (a!=b)
        value=self.fact(node)
        return value[1] if value and value[0]=='const' and type(value[1]) is bool else None

    def begin(self,proc,args):
        from .caller_metadata import params
        self.values={};self.terminated=False
        self.escaped=set()
        for (name,field),value in zip(params(proc.role('type')),args):
            self.values[name]=self.normalize(value,self.r.type_node(field.role('type')))

    def detach(self,value,names,name,depth=0):
        if depth>16:return UNKNOWN
        if not value:return value
        if value[0] in {'const','atom'} and name not in names:return UNKNOWN
        if value[0]=='pointer':return 'pointer',None,self.detach(self.deref(value),names,name,depth+1)
        if value[0]=='record':return 'record',tuple((k,self.detach(v,names,k,depth+1)) for k,v in value[1])
        return value

    def arguments(self,signature,groups):
        from .caller_metadata import params
        if signature not in self.interests:
            names=set()
            body=signature.parent.role('body') if signature.parent else None
            for node in descendants(body) if body else []:
                if node.kind in {'If','Switch','When','WhenExpr','Ternary','Call'} or node.kind=='Binary' and node.data.get('op') in {'&&','||'}:
                    for child in node.children:
                        if child.kind!='Block':names.update(n.text for n in descendants(child) if n.kind=='Ident')
            self.interests[signature]=names
        result=[]
        for (name,field),args in zip(params(signature),groups):
            if field.data.get('variadic'):
                pack=[]
                for arg in args:
                    value=self.fact(arg)
                    parent=arg.parent;call=parent.parent if parent and parent.kind=='FieldValue' else parent
                    ellipsis=call.data.get('roles',{}).get('ellipsis') if call and call.kind=='Call' else None
                    packed=call and call.kind=='Call' and (parent.kind=='FieldValue' or ellipsis and ellipsis[0]>=0 and arg.start>=ellipsis[1])
                    if packed:
                        if value and value[0]=='pack':pack.extend(value[1]);continue
                        typ=self.r.type_of(arg);pack.append(('spread',typ.elem));continue
                    if arg.kind=='Ellipsis' or arg.kind=='Unary' and arg.data.get('op')=='..':
                        spread=self.fact(arg.role('expr'))
                        if spread and spread[0]=='pack':pack.extend(spread[1]);continue
                        typ=self.r.type_of(arg.role('expr'))
                        pack.append(('spread',typ.elem));continue
                    pack.append(value or ('typed',self.r.type_of(arg)))
                value='pack',tuple(pack)
            else:value=self.fact(args[0]) if len(args)==1 else UNKNOWN
            result.append(self.detach(value,self.interests[signature],name.text,0))
        return tuple(result)

    def bind(self,node):
        values=node.group('values')
        for i,name in enumerate(node.group('names')):self.values[name]=self.fact(values[i]) if i<len(values) else self.c.random.zero(name) or UNKNOWN

    def assign(self,node,value):
        if node.kind=='Paren':return self.assign(node.role('expr'),value)
        if node.kind=='Ident' and node.text=='_':return
        if node.kind=='Ident' and (symbol:=self.r.symbol(node)) and not symbol.projection:
            if key:=self.key(node):self.values[key]=value
            else:self.forget(value)
            return
        symbol=self.r.symbol(node)
        base=symbol.projection[0] if symbol and symbol.projection else node
        self.forget(value)
        while base.role('expr'):base=base.role('expr')
        key=self.key(base);typ=self.r.type_of(base)
        if key is not None and typ.kind not in {'pointer','slice','dynamic','map'}:self.values[key]=UNKNOWN
        self.opaque_write()

    def forget(self,value):
        if not value:return
        if value[0]=='pointer':
            if value[1] is None:self.opaque_write()
            else:self.escaped.add(value[1]);self.values[value[1]]=UNKNOWN
        elif value[0]=='record':
            for _,item in value[1]:self.forget(item)
        elif value[0]=='pack':
            for item in value[1]:self.forget(item)
        elif value[0] in {'stream','buffered','buffered_reader'}:self.forget(value[1])

    def clear_pointees(self,value):
        if not value:return value
        if value[0]=='pointer':return 'pointer',value[1],UNKNOWN
        if value[0]=='record':return 'record',tuple((k,self.clear_pointees(v)) for k,v in value[1])
        if value[0]=='pack':return 'pack',tuple(self.clear_pointees(v) for v in value[1])
        if value[0] in {'stream','buffered','buffered_reader'}:return value[0],self.clear_pointees(value[1])
        return value



from .caller_metadata import arrange

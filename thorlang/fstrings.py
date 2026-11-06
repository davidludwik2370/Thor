"""Call-site f-string temporaries live through the enclosing expression.

Only syntactic, anonymous f-string arguments participate. Named strings and
ordinary string-returning procedure contracts retain explicit ownership.
"""
from dataclasses import dataclass


@dataclass
class Frame:
    pointer: str
    targets: set


class FStringLifetimes:
    expressions={'Call','FieldValue','Paren','Interpolation','Binary','Unary',
                 'Ternary','WhenExpr','Index','Slice','Selector','Deref','Compound',
                 'SwitchExpr','CompileSwitchExpr','SwitchResults','Cast','AutoCast','OrElse','OrPanic'}
    queries={'type_of','typeid_of','size_of','align_of','offset_of','type_info_of',
             '#assert','#config','#defined'}

    def __init__(self,expressions):
        self.e=expressions
        self.s=expressions.source
        self.frames=[]
        self.plans=None
        self.busy=set()
        self.capturing=set()
        self.querying=0

    @property
    def current(self):return self.frames[-1] if self.frames else None

    def module(self):return self.e.module('temporaries')
    def pointer(self):return self.s.prefix+'fstring_scope'
    def parameter(self):return self.pointer()+': ^'+self.module()+'.Scope'

    def captured(self,node):
        while node.kind=='Paren':node=node.role('expr')
        return bool(self.current and node in self.current.targets)

    def manages(self,node):
        return bool(self.current and any(node.start<=target.start<target.end<=node.end
                                         for target in self.current.targets))

    def argument(self,node):
        """Find a real argument use before a binding or formatting field."""
        for ancestor in node.ancestors():
            if ancestor.kind=='Call':
                callee=ancestor.role('callee')
                if callee.text in self.queries and self.s.resolver.symbol(callee) is None:return False
                if any(arg.start<=node.start<node.end<=arg.end for arg in ancestor.group('args')):return ancestor
            if ancestor.kind in {'Interpolation','Procedure','Declaration','Return','Assignment','Field'}:return False
        return False

    def root(self,node):
        root=node
        ancestors=list(node.ancestors())
        # Adapted switch arms have native statement/block parents in the AST.
        # They are still part of the enclosing value expression.
        switches=[a for a in ancestors if a.kind in {'SwitchExpr','CompileSwitchExpr'}]
        for ancestor in ancestors:
            if ancestor.kind in self.expressions:
                root=ancestor
            elif ancestor.kind in {'Case','Block','ExpressionStatement'} and any(
                    ancestor.start>=sw.start and ancestor.end<=sw.end for sw in switches):continue
            else:break
        return root

    def discover(self):
        self.plans={}
        for node in self.s.nodes:
            if node.kind=='Interpolation' and not node.data.get('manual_lifetime'):
                call=self.argument(node)
                if call:self.plans.setdefault(self.root(call),set()).add(node)

    def signature(self,node):
        signature=self.s.signature(node)
        if signature is None:
            enabled=self.s.resolver.native_enabled
            self.s.resolver.native_enabled=True
            try:signature=self.s.signature(node)
            finally:self.s.resolver.native_enabled=enabled
        return signature

    def render(self,node,expected,constant):
        if not self.s.thor:return None
        if self.current and node in self.current.targets and node not in self.capturing:
            self.capturing.add(node)
            try:code=self.s._render(node,expected,constant)
            finally:self.capturing.remove(node)
            return self.s.mapping.wrap(f'{self.module()}.capture({self.current.pointer},{code})',
                                       self.s.path,node.start,node.end,'fstring-argument')
        if constant or self.querying or self.e.ownership.querying:return None
        if node in self.busy:return None
        if self.plans is None:self.discover()
        targets=self.plans.get(node)
        if not targets:return None
        # Query arity only for direct call roots. Other roots produce one value.
        statement=bool(node.parent and node.parent.kind=='ExpressionStatement')
        signature=self.signature(node) if node.kind=='Call' and not statement else None
        count=len(self.s.fields(signature.role('results'))) if signature else 1
        constructor=self.s.big.constructor(node) if node.kind=='Call' else None
        if constructor and any(self.e.shape(arg.role('value') if arg.kind=='FieldValue' else arg).kind=='string'
                               for arg in node.group('args')):count=2
        if statement:count=0
        result=False if count==0 else (None,)*count if count>1 else None
        optional=('optional_allocator_error' if signature.data.get('optional_allocator_error') else 'optional_ok') if signature and signature.data.get('optional_ok') else None
        if optional and count>1:
            status=self.e.module('fstring_runtime','base:runtime')+'.Allocator_Error' if optional=='optional_allocator_error' else 'bool'
            result=(*result[:-1],status)
        self.busy.add(node)
        try:
            self.querying+=1
            try:
                if count==0:types=[]
                elif isinstance(expected,tuple) and len(expected)==count and all(expected):
                    types=[self.e.type_code(hint) for hint in expected]
                elif count==1:types=[self.e.type_code(expected) if expected and not isinstance(expected,tuple) else self.e.ownership.native_type(node)]
                else:
                    self.s.needs_intrinsics=True
                    if constructor:types=[self.s.big.module()+'.'+constructor.name,'bool']
                    else:
                        call=self.s.render(node)
                        types=[f'{self.s.prefix}meta.type_proc_return_type(type_of({self.s.prefix}meta.procedure_of({call})),{i})' for i in range(count)]
            finally:self.querying-=1

            def body(hint):
                storage=self.s.prefix+f'fstring_entries_{node.start}'
                scope=storage+'_scope'
                lines=[f'{storage}: [{len(targets)}]{self.module()}.Entry',
                       f'{scope}: {self.module()}.Scope = {{entries={storage}[:]}}',
                       f'{self.pointer()} := &{scope}',f'defer {self.module()}.destroy({self.pointer()})']
                self.frames.append(Frame(self.pointer(),targets))
                try:code=self.s.render(node,hint)
                finally:self.frames.pop()
                lines.append(('return ' if count else '')+code)
                return '\n'.join(lines)
            name,args=self.e.closed(node,body,result=result,flavor='fstring_expression',optional_tag=optional if count else None)
            if isinstance(result,tuple):types=[typ for typ,fixed in zip(types,result) if fixed is None]
            return name+'('+', '.join([*args,*types])+')'
        finally:self.busy.remove(node)

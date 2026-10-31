"""Runtime numeric operations; Odin still decides native types/overloads."""
import json
from .compiler import Hint


class BigOperators:
    arithmetic={'+','-','*','/','%','%%','**','&','|','~','&~','<<','>>'}
    comparisons={'==','!=','<','<=','>','>='}
    kinds={'big_integer','big_rational'}

    def __init__(self,big):
        self.big=big
        self.s=big.s
        self.e=big.e

    def operands(self,node):
        return [node.role('left'),node.role('right')] if node.kind=='Binary' else [node.role('expr')]

    def handles(self,node,hint=None):
        if node.kind=='Binary' and node.data['op'] not in self.arithmetic|self.comparisons:return False
        if node.kind=='Unary' and node.data['op'] not in {'+','-','~'}:return False
        if node.kind not in {'Binary','Unary'}:return False
        values=self.operands(node)
        if any(self.s.denotes_type(v) for v in values):return False
        types=[self.big.shape(v) for v in values]
        if any(t.kind in self.kinds for t in types):return True
        if any(t.kind=='generic' or t.kind=='unknown' and v.kind!='ImplicitSelector' and v.text not in {'nil','---'} for v,t in zip(values,types)):return True
        expected=self.big.expected(hint)
        return bool(expected and expected.kind in self.kinds and all(self.s.resolver.membership_untyped(v) for v in values))

    def chosen(self,node,hint):
        expected=self.big.expected(hint)
        if expected and expected.kind in self.kinds:return self.e.type_code(hint)
        operands=self.operands(node)
        for kind in [self.kinds,{'generic','unknown'}]:
            for operand in operands:
                if self.big.shape(operand).kind in kind:return self.e.ownership.native_type(operand)
        return self.e.ownership.native_type(operands[0])

    def context_node(self,node,hint):
        expected=self.big.expected(hint)
        if expected and expected.kind in self.kinds:return hint.node
        values=self.operands(node)
        return next((v for v in values if self.big.shape(v).kind in self.kinds),
                    next((v for v in values if self.big.shape(v).kind in {'generic','unknown'}),values[0]))

    def render(self,node,hint,constant):
        if not self.handles(node,hint):return None
        if constant:return self.big.error(node,'Compile-time Integer/Rational operators are not enabled yet.')
        op=node.data['op']
        concrete=[self.big.shape(v).kind for v in self.operands(node)]
        if 'big_rational' in concrete and op in {'&','|','~','&~','<<','>>'}:
            return self.big.error(node,'Rational does not support bitwise operators or shifts.')
        if node.kind=='Binary':
            right=node.role('right')
            number=self.s.resolver.constant_int(right)
            if op in {'<<','>>'} and number is not None and number<0:
                return self.big.error(right,'Integer shift counts must be nonnegative.')
            if op=='**' and number is not None and number<0 and 'big_integer' in concrete:
                return self.big.error(right,'Integer powers require a nonnegative exponent; use Rational for negative powers.')
            if op in {'/','%','%%'} and self.big.statically_zero(right):
                return self.big.error(right,'A big-number divisor must not be zero.')
        comparison=node.kind=='Binary' and op in self.comparisons
        def body(result):
            typ=self.chosen(node,hint) if comparison else self.e.type_code(result)
            lines=[f'when {typ} == {self.big.module()}.Integer || {typ} == {self.big.module()}.Rational {{']
            names=[]
            for i,value in enumerate(self.operands(node)):
                name=self.s.prefix+f'big_operand_{node.start}_{i}'
                origin=self.context_node(node,hint)
                expected=Hint(origin.source,origin,typ)
                code=self.big.owned(value,expected)
                lines.append(self.big.mapped(value,f'{name}: {typ} = {code}'))
                lines.append(f'defer {self.big.module()}.compiler_destroy(&{name})')
                names.append(name)
            method='compare' if comparison else 'binary' if node.kind=='Binary' else 'unary'
            lines.append('return '+self.big.module()+'.'+method+'('+', '.join([*names,json.dumps(op)])+')')
            self.big.busy.add(node)
            try:native=self.s.render(node,hint,constant)
            finally:self.big.busy.remove(node)
            lines.append('} else { return '+native+' }')
            return '\n'.join(lines)
        name,args=self.e.closed(node,body,result='bool' if comparison else None,flavor='big_operator')
        if not comparison:args=[*args,self.chosen(node,hint)]
        return name+'('+', '.join(args)+')'

    def assignment(self,node):
        op=node.data['op']
        if op=='=' or op[:-1] not in self.arithmetic:return None
        lhs,rhs=node.group('lhs'),node.group('rhs')
        if len(lhs)!=1 or len(rhs)!=1:return None
        kind=self.big.shape(lhs[0]).kind
        if kind not in self.kinds|{'generic','unknown'}:return None
        return self.update(node,lhs[0],rhs[0],op[:-1])

    def update(self,node,lhs,rhs,op):
        def body(_):
            lines=[]
            target=self.big.capture_targets([lhs],lines,node)[0]
            typ=self.e.ownership.native_type(lhs)
            lines.append(f'when {typ} == {self.big.module()}.Integer || {typ} == {self.big.module()}.Rational {{')
            old=self.s.prefix+f'big_update_old_{node.start}'
            right=self.s.prefix+f'big_update_rhs_{node.start}'
            result=self.s.prefix+f'big_update_result_{node.start}'
            lines.extend([f'{old} := {self.big.module()}.compiler_clone({target})',f'defer {self.big.module()}.compiler_destroy(&{old})'])
            value=self.big.owned(rhs,Hint(self.s,lhs,typ))
            lines.extend([self.big.mapped(rhs,f'{right}: {typ} = {value}','debug-capture'),f'defer {self.big.module()}.compiler_destroy(&{right})',
                          f'{result} := {self.big.module()}.binary({old},{right},{json.dumps(op)})'])
            self.big.commit([target],[result],lines)
            self.big.busy.add(node)
            try:native=self.s.render(node)
            finally:self.big.busy.remove(node)
            lines.append('} else { '+native+' }')
            return '\n'.join(lines)
        return self.big.statement(node,body)

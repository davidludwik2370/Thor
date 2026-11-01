"""Lifetimes for anonymous allocations made by Thor expressions.

Named values and arbitrary procedure results are borrowed here. Only Thor
producers establish ownership. Known consumers release inputs after their last
use; conditionals carry the selected value's ownership alongside the value.
"""
from dataclasses import dataclass, field


@dataclass
class Temporary:
    value: str
    owned: str = 'false'
    origin: object = None
    dependencies: list = field(default_factory=list)


class Ownership:
    math = {'|': 'union_of', '&': 'intersection', '-': 'difference', '~': 'symmetric_difference'}
    comparisons = {'==', '!=', '<', '<=', '>', '>='}

    def __init__(self, expressions):
        self.e = expressions
        self.s = expressions.source
        self.serial = 0
        self.querying = 0
        self.discarding = set()

    def fresh(self, label):
        self.serial += 1
        return self.s.prefix + f'owned_{label}_{self.serial}'

    def generic_math(self, n):
        if n.kind != 'Binary' or n.data['op'] not in self.math: return False
        left, right = n.role('left'), n.role('right')
        kinds = self.e.shape(left).kind, self.e.shape(right).kind
        return ('set' in kinds or all(k in {'generic', 'unknown'} for k in kinds)
                and left.text != 'nil' and right.text != 'nil'
                and not self.s.denotes_type(left) and not self.s.denotes_type(right))

    def may_own(self, n):
        if n is None: return False
        if n.kind == 'Paren': return self.may_own(n.role('expr'))
        if self.s.big.enabled and self.s.big.fresh(n): return True
        if n.kind == 'Interpolation': return not n.data.get('manual_lifetime',False)
        if n.kind == 'Compound' and n.role('type'):
            return self.s.resolver.type_node(n.role('type')).kind == 'set'
        if self.generic_math(n): return True
        if n.kind in {'Ternary', 'WhenExpr'}:
            return any(self.may_own(n.role(k)) for k in ['x', 'y'])
        if n.kind in {'SwitchExpr', 'CompileSwitchExpr'}:
            return any(self.may_own(self.s.at(c['result'], n)) for c in n.data['cases'])
        return False

    def borrows_temporary(self, n):
        if n.kind == 'Paren': return self.borrows_temporary(n.role('expr'))
        if self.s.big.enabled and self.s.big.property(n): return False
        return n.kind in {'Slice', 'Selector'} and (self.may_own(n.role('expr')) or self.borrows_temporary(n.role('expr')))

    def native_type(self, n):
        self.s.needs_default = True
        self.querying += 1
        try: code = self.s.render(n)
        finally: self.querying -= 1
        return f'type_of({self.s.prefix}default_value({code}))'

    def hint(self, expression, origin):
        from .compiler import Hint
        return Hint(self.s, origin, expression)

    def evaluate(self, n, code, hint=None):
        if n.kind == 'Paren': return self.evaluate(n.role('expr'), code, hint)
        if n.kind in {'Ternary', 'WhenExpr', 'SwitchExpr', 'CompileSwitchExpr'} and self.may_own(n):
            return self.select(n, code, hint)
        if self.borrows_temporary(n):
            return self.view(n, code)
        value = self.fresh('value')
        annotation = self.e.type_code(hint) if hint else None
        code.append(f'{value}: {annotation} = {self.s.render(n, hint)}' if annotation else f'{value} := {self.s.render(n)}')
        owned = 'false'
        if self.may_own(n) and not self.e.fstrings.captured(n):
            if self.s.big.enabled and self.s.big.fresh(n) and self.e.shape(n).kind in {'big_integer','big_rational'}:
                owned='true'
            elif self.s.big.enabled and self.s.big.fresh(n) and self.e.shape(n).kind in {'generic','unknown'}:
                typ=self.native_type(n)
                owned=f'({typ}=={self.s.big.module()}.Integer || {typ}=={self.s.big.module()}.Rational)'
                if self.generic_math(n):owned=f'({owned} || {self.e.set_trait("("+typ+"){ }")})'
            elif n.kind == 'Binary' and self.e.shape(n).kind != 'set':
                # The native type is authoritative for unconstrained generics.
                owned = f'(true when {self.e.set_trait("("+self.native_type(n)+"){ }")} else false)'
            else: owned = 'true'
        return Temporary(value, owned, n)

    def release(self, value, code, *, deferred=False):
        cleanup = []
        if value.owned != 'false':
            origin = value.origin
            kind = self.e.shape(origin).kind
            destroy = None
            if kind == 'string': destroy = f'delete({value.value})'
            elif kind == 'set': destroy = f'{self.e.module("sets")}.destroy({value.value})'
            elif kind in {'big_integer','big_rational'}: destroy=f'{self.s.big.module()}.compiler_destroy_value({value.value})'
            if destroy is None:
                self.s.needs_intrinsics = True
                typ = self.native_type(origin)
                destroy = (f'when {self.s.prefix}meta.type_is_string({typ}) {{delete({value.value})}} '
                           f'else when {self.e.set_trait("("+typ+"){ }")} {{{self.e.module("sets")}.destroy({value.value})}}')
                if self.s.big.enabled:
                    destroy+=f' else {{{self.s.big.module()}.compiler_destroy_value({value.value})}}'
            cleanup.append(destroy if value.owned == 'true' else f'if {value.owned} {{{destroy}}}')
        for dependency in reversed(value.dependencies): self.release(dependency, cleanup)
        if cleanup:
            text = '\n'.join(cleanup)
            code.append('defer {\n'+text+'\n}' if deferred else text)

    def select(self, n, code, hint):
        hint = hint or self.hint(self.native_type(n), n)
        value, owned = self.fresh('selected'), self.fresh('selected_flag')
        code.extend([f'{value}: {self.e.type_code(hint)}', f'{owned}: bool'])
        def arm(expr):
            if n.kind in {'SwitchExpr', 'CompileSwitchExpr'} and self.s.resolver.is_panic_call(expr):
                return self.s.render(expr)
            statements = []
            selected = self.evaluate(expr, statements, hint)
            # Dependencies cannot outlive a returned borrowed view.
            if selected.dependencies:
                return self.e.error(expr, 'A view into an anonymous allocation needs a named owner.')
            statements.extend([f'{value} = {selected.value}', f'{owned} = {selected.owned}'])
            return '\n'.join(statements)
        if n.kind in {'Ternary', 'WhenExpr'}:
            word = 'when' if n.kind == 'WhenExpr' else 'if'
            cond = self.s.render(n.role('cond'), constant=n.kind == 'WhenExpr')
            code.append(f'{word} {cond} {{\n{arm(n.role("x"))}\n}} else {{\n{arm(n.role("y"))}\n}}')
        else:
            cases = n.data['cases']
            if not any(not c['matches'] for c in cases):
                code.append(self.e.error(n, 'A switch expression requires a default arm.'))
            elif n.kind == 'SwitchExpr':
                lines=[]
                selector,kind=self.e.switches.runtime_selector(n,lines)
                lines.append('switch '+('' if kind else selector)+' {')
                for case in cases:
                    matches = self.e.switches.runtime_matches(n,case,selector,kind)
                    lines.append(f'case {matches}:\n{arm(self.s.at(case["result"], n))}')
                code.append('{\n'+'\n'.join([*lines, '}'])+'\n}')
            else:
                clauses = []
                for case in [c for c in cases if c['matches']]+[c for c in cases if not c['matches']]:
                    word = 'else when' if clauses else 'when'
                    clauses.append(f'{word} {self.e.switch_condition(n, case)} {{\n{arm(self.s.at(case["result"], n))}\n}}')
                code.append(f'when {self.e.validation(n)} {{'+ ' '.join(clauses)+'}')
        return Temporary(value, owned, n)

    def binary(self, n, expected, constant):
        """Consume Set operands, or owned strings used in comparisons."""
        op = n.data['op']
        left, right = n.role('left'), n.role('right')
        if self.s.denotes_type(left) or self.s.denotes_type(right):return None
        sets = self.generic_math(n)
        if constant or not (sets or op in self.comparisons and (self.may_own(left) or self.may_own(right))): return None
        # Comparisons of ordinary borrowed Sets use the existing direct path.
        if not sets and not (self.may_own(left) or self.may_own(right)): return None
        result_type = self.native_type(left) if sets else 'bool'
        def body(result_hint):
            code = []
            input_hint = result_hint if sets else self.hint(self.native_type(left), left)
            a = self.evaluate(left, code, input_hint)
            # Explicitly typed operands keep their types so native overload
            # errors describe the operation, rather than a fabricated coercion.
            b = self.evaluate(right, code, input_hint if right.kind in {'ImplicitSelector', 'Literal'} else None)
            result = self.fresh('result')
            if sets:
                known = self.e.shape(left).kind == 'set' or self.e.shape(right).kind == 'set'
                method = self.math[op] if known else 'compiler_'+{'|':'union', '&':'intersection', '-':'difference', '~':'symmetric_difference'}[op]
                call = f'{self.e.module("sets")}.{method}({a.value}, {b.value})'
                if op == '|' and a.owned != 'false':
                    # Reuse only anonymous owned tables. The first pair of
                    # operands is evaluated before its union; later operands
                    # are evaluated only after that union exists.
                    code.append(f'{result}: {self.e.type_code(result_hint)}')
                    trait = self.e.set_trait('('+self.e.type_code(result_hint)+'){ }')
                    reuse = f'{self.e.module("sets")}.union_assign(&{a.value},{b.value})\n{result}={a.value}'
                    assign = f'{result}={call}'
                    code.append(f'when {trait} {{\nif {a.owned} {{\n{reuse}\n}} else {{\n{assign}\n}}\n}} else {{\n{assign}\n}}')
                    a.owned = 'false'
                else:
                    code.append(f'{result} := {call}')
            else:
                # Set logical equality uses the support operation; ordinary
                # string/scalar comparisons retain their native operator.
                if op in {'==', '!='}:
                    known = self.e.shape(left).kind == 'set' or self.e.shape(right).kind == 'set'
                    if known:
                        expr = f'{self.e.module("sets")}.equal({a.value},{b.value})'
                        if op == '!=': expr = '!'+expr
                    else:
                        expr = f'{self.e.module("sets")}.compiler_equal({a.value},{b.value})'
                        if op == '!=': expr = '!'+expr
                else: expr = f'{a.value} {op} {b.value}'
                code.append(f'{result} := {expr}')
            self.release(b, code); self.release(a, code)
            code.append('return '+result)
            return '\n'.join(code)
        name, args = self.e.closed(n, body, result=None, flavor='consume')
        return f'{name}({", ".join([*args, result_type])})'

    def query(self, n, arg, operation):
        if not self.may_own(arg) and not self.borrows_temporary(arg): return None
        def body(_):
            code = []
            value = self.evaluate(arg, code)
            result = self.fresh('query')
            code.append(f'{result} := {operation(value.value)}')
            self.release(value, code)
            code.append('return '+result)
            return '\n'.join(code)
        name, args = self.e.closed(n, body, result='int', flavor='query')
        return f'{name}({", ".join(args)})'

    def membership(self, n):
        left, right = n.role('left'), n.role('right')
        if not (self.may_own(left) or self.may_own(right) or self.borrows_temporary(right)): return None
        def body(_):
            code = []
            # Container type supplies implicit enum/literal context without
            # evaluating the container ahead of the needle.
            typ = self.native_type(right)
            self.s.needs_intrinsics = True
            sets = self.e.module('sets')
            zero = '('+typ+'){ }'
            elem = (f'#type (type_of({sets}.element_witness({zero})) when {self.e.set_trait(zero)} '
                    f'else (type_of({sets}.key_witness({zero})) when {self.s.prefix}meta.type_is_map({typ}) '
                    f'else {self.s.prefix}meta.type_elem_type({typ})))')
            a = self.evaluate(left, code, self.hint(elem, left))
            b = self.evaluate(right, code)
            self.s.needs_membership = True
            result = self.fresh('membership')
            call = f'{self.s.prefix}contains(value={a.value},values={b.value})'
            code.append(f'{result} := '+('!' if n.data['op']=='not_in' else '')+call)
            self.release(b, code); self.release(a, code)
            code.append('return '+result)
            return '\n'.join(code)
        name, args = self.e.closed(n, body, result='bool', flavor='membership')
        return f'{name}({", ".join(args)})'

    def discard(self, n):
        """Consume unused values, including mixed blank destinations."""
        parent = n.parent
        if self.querying or n in self.discarding or not parent or not self.may_own(n): return None
        statement = parent.kind == 'ExpressionStatement'
        if parent.kind == 'Assignment':
            names, values = parent.group('lhs'), parent.group('rhs')
        elif parent.kind == 'Declaration' and parent.data.get('mutable', True):
            names, values = parent.group('names'), parent.group('values')
        elif statement:
            names, values = [], []
        else: return None
        if not statement and (len(names) != len(values) or n not in values or names[values.index(n)].text != '_'): return None
        def body(hint):
            self.discarding.add(n)
            try:
                code = []
                temp = self.evaluate(n, code, hint)
                self.release(temp, code)
                if not statement: code.append(f'return {self.e.type_code(hint)}{{ }}')
                return '\n'.join(code)
            finally: self.discarding.remove(n)
        name, args = self.e.closed(n, body, result=False if statement else None, flavor='discard')
        if statement: return f'{name}({", ".join(args)})'
        self.discarding.add(n)
        try: result = self.native_type(n)
        finally: self.discarding.remove(n)
        return f'{name}({", ".join([*args, result])})'

    def update(self, n, function):
        left, right = n.group('lhs')[0], n.group('rhs')[0]
        if n.data['op'] == '+=' or not self.may_own(right): return None
        post = n.parent and n.parent.kind == 'For' and n.parent.role('post') is n
        def body(_):
            code = []
            pointer = self.fresh('target')
            code.append(f'{pointer} := &({self.s.render(left)})')
            value = self.evaluate(right, code)
            code.append(f'{function}({pointer},{value.value})')
            self.release(value, code)
            if post: code.append('return true')
            return '\n'.join(code)
        name, args = self.e.closed(n, body, result='bool' if post else False, flavor='owned_update')
        return ('_ = ' if post else '')+f'{name}({", ".join(args)})'

    def view(self, n, code):
        if n.kind == 'Paren': return self.view(n.role('expr'), code)
        base = self.evaluate(n.role('expr'), code)
        value = self.fresh('view')
        if n.kind == 'Selector': expr = f'({base.value}).{self.s.render(n.role("field"))}'
        else:
            bounds = []
            for key in ['low', 'high']:
                bound = n.role(key)
                if bound:
                    local = self.fresh(key)
                    code.append(f'{local} := {self.s.render(bound)}')
                    bounds.append(f'{self.e.module("sequence")}.normalize(len({base.value}),{local})')
                else: bounds.append('')
            expr = f'({base.value})[{bounds[0]}:{bounds[1]}]'
        code.append(f'{value} := {expr}')
        return Temporary(value, origin=n, dependencies=[base])

    def index(self, n):
        base = n.role('expr')
        if not self.may_own(base) and not self.borrows_temporary(base): return None
        if self.e.writes(n):
            return self.e.error(n, 'An address or write into an anonymous allocation needs a named owner.')
        sequence = self.e.module('sequence')
        result_type = f'type_of({sequence}.element(({self.native_type(base)}){{ }}))'
        def body(hint):
            code = []
            value = self.evaluate(base, code)
            index = self.fresh('index')
            code.append(f'{index} := {self.s.render(n.role("index"))}')
            result = self.fresh('element')
            code.append(f'{result}: {self.e.type_code(hint)}')
            code.append(f'when {self.e.sequence_trait("("+self.native_type(base)+"){ }")} {{\n'
                        f'{result} = ({value.value})[{sequence}.normalize(len({value.value}),{index})]\n'
                        f'}} else {{\n{result} = ({value.value})[{index}]\n}}')
            self.release(value, code)
            code.append('return '+result)
            return '\n'.join(code)
        name, args = self.e.closed(n, body, result=None, flavor='owned_index')
        return f'{name}({", ".join([*args, result_type])})'

    def iterate(self, n):
        expr = n.role('expr')
        if not self.may_own(expr) and not self.borrows_temporary(expr): return None
        code = []
        value = self.evaluate(expr, code)
        self.release(value, code, deferred=True)
        loop = self.e.iterate(n, value.value)
        return '{\n'+'\n'.join([*code,loop])+'\n}'

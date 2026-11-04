"""Multiple switch results stay native multiple values, never heap tuples.

Exact destination types are supplied by Odin at the original expression site.
Compile-time selection of a whole statement retains native constant bindings
and leaves inactive arms unchecked.
"""
from .compiler import Hint


def unwrap(node):
    while node is not None and node.kind == 'Paren':
        node = node.role('expr')
    return node


class Switches:
    def __init__(self, expressions):
        self.e = expressions
        self.s = expressions.source
        self.replacements = {}
        self.statements = set()
        self.global_context = 0

    def arms(self, node):
        return [self.s.at(case['result'], node) for case in node.data['cases']]

    def closed(self, node, builder, **options):
        # Global initializers have no implicit context, even when a switch has
        # only one result and does not use the staged multi-result initializer.
        global_scope = not any(owner.kind == 'Procedure' for owner in node.ancestors())
        self.global_context += int(global_scope)
        try:
            return self.e.closed(node, builder, **options)
        finally:
            self.global_context -= int(global_scope)

    def default_error(self, node):
        defaults = [case for case in node.data['cases'] if not case['matches']]
        if len(defaults) > 1:
            return 'A switch may have only one default arm.'
        if defaults:
            return None
        if node.data.get('partial'):
            return 'A #partial switch expression requires a default arm.'
        selector = node.role('selector')
        if (node.kind != 'SwitchExpr' or selector is None or
                self.e.shape(selector).kind not in {'enum', 'generic', 'unknown'}):
            return 'A switch expression requires a default arm.'
        # Odin checks enum coverage, including aliases/ranges/imported types.
        # Unknown generic selectors are checked again at specialization below.
        return None

    def runtime_code(self, node, selector, kind, branches, *, returns=True):
        """Retain native enum coverage; never manufacture a result on fallthrough."""
        missing_default = not any(not case['matches'] for case, _ in branches)
        lines = []
        matched = None
        if missing_default:
            self.s.needs_intrinsics = True
            lines.append(f'when {self.s.prefix}meta.type_is_enum(type_of({selector})) {{')
            if not returns:
                matched = self.e.ownership.fresh('switch_matched')
                lines.append(f'{matched} := false')
        directive = '#partial ' if node.data.get('partial') else ''
        lines.append(directive+'switch '+('' if kind else selector)+' {')
        for case, content in branches:
            matches = self.runtime_matches(node, case, selector, kind)
            mark = f'{matched} = true; ' if matched else ''
            lines.append(f'case {matches}: {mark}{content}')
        lines.append('}')
        if missing_default:
            # The hygiene pass qualifies generated builtins if user bindings
            # shadow them; user-authored panic calls keep their own meaning.
            name = 'panic' if self.e.has_context(node) and not self.global_context else 'panic_contextless'
            panic = f'{name}("Unmatched enum value in switch expression")'
            lines.append(f'if !{matched} {{{panic}}}' if matched else panic)
            lines.append('} else { #panic("A switch expression requires a default arm unless its selector is an enum") }')
        return '\n'.join(lines)

    def runtime_selector(self,node,code):
        selector=node.role('selector')
        kind=self.e.shape(selector).kind if selector else None
        if self.s.big.enabled and kind in {'big_integer','big_rational'}:
            problem=self.s.big.constants.validate_cases(node)
            if problem:code.append(self.e.error(node,problem))
            name=self.e.ownership.fresh('switch_selector')
            code.append(f'{name} := {self.s.big.owned(selector)}')
            code.append(f'defer {self.s.big.module()}.compiler_destroy(&{name})')
            return name,kind
        return (self.s.render(selector) if selector else ''),None

    def runtime_matches(self,node,case,selector,kind):
        if kind is None:return ', '.join(self.s.render(self.s.at(v,node)) for v in case['matches'])
        big=self.s.big
        hint=big.type_hint(kind)
        matches=[]
        for span in case['matches']:
            match=self.s.at(span,node)
            if match.kind=='Binary' and match.data['op'] in {'..','..<','..='}:
                low,high=(big.owned(match.role(side),hint) for side in ['left','right'])
                value=f'{big.module()}.compiler_switch_range({selector},{low},{high},{str(match.data["op"]=="..=").lower()})'
            else:value=f'{big.module()}.compiler_switch_case({selector},{big.owned(match,hint)})'
            # Diagnostics retain the case operand's span. Debug stepping skips
            # the synthetic comparison so a case-line breakpoint stops on its
            # actual result rather than twice on comparison and result.
            matches.append(big.mapped(match,value,'debug-capture'))
        return ', '.join(matches)

    def values(self, arm):
        return arm.group('args') if arm.kind == 'SwitchResults' else [arm]

    def deferred_call(self, arm):
        """Generic callbacks and conditional signatures need native checking."""
        node=unwrap(arm)
        if node.kind!='Call':return False
        if self.s.resolver.conditional_callable(node.role('callee')):return True
        if self.s.resolver.is_diverging_call(node) or self.s.signature(node) or self.s.denotes_type(node.role('callee')):return False
        if self.s.purity and self.s.purity.builtin(node):return False
        owner=next((parent for parent in node.ancestors() if parent.kind=='Procedure'),None)
        return bool(owner and self.s.resolver.generic_parameters(owner.role('type')))

    def result_condition(self, arm, index=0):
        if not self.deferred_call(arm):return None
        self.s.needs_intrinsics=True
        return f'{self.s.prefix}meta.type_proc_return_count(type_of({self.s.prefix}meta.procedure_of({self.s.render(unwrap(arm))}))) > {index}'

    def branch(self, arm, value):
        """Keep valueless calls out of returns/assignments, without executing twice."""
        if self.s.resolver.is_diverging_call(arm):return self.s.render(arm)
        condition=self.result_condition(arm)
        if condition is None:return value()
        # Zero results alone do not imply divergence. Odin validates the -> !
        # wrapper's body, rejecting a specialized ordinary void callback here.
        name,args=self.closed(arm,lambda _:self.s.render(arm),result='!',flavor='switch_nonreturning')
        terminal=name+'('+', '.join(args)+')'
        return f'when {condition} {{\n{value()}\n}} else {{\n{terminal}\n}}'

    def queries(self, node, arms, index=0, scalar=False):
        candidates=[]
        for arm in arms:
            if arm.kind=='SwitchBreak' or self.s.resolver.is_diverging_call(arm):continue
            if arm.kind=='SwitchResults' and index>=len(arm.group('args')):continue
            query=self.s.render(arm) if scalar else self.result(arm,index)
            candidates.append((query,self.result_condition(arm,index)))
        if not candidates:return []
        fallback=next((query for query,condition in reversed(candidates) if condition is None),None)
        if fallback is None:
            fallback=self.e.error(node,'A switch expression with only nonreturning arms requires an expected result type.')
            for query,condition in reversed(candidates):fallback=f'({query} when {condition} else {fallback})'
        return [f'({query} when {condition} else {fallback})' if condition else query for query,condition in candidates]

    def special(self, node, expected=None):
        return node.kind in {'SwitchExpr', 'CompileSwitchExpr'} and (
            isinstance(expected, tuple) and len(expected) > 1
            or any(a.kind in {'SwitchResults', 'SwitchBreak'} for a in self.arms(node))
            or any(self.call_count(a) > 1 for a in self.arms(node)))

    def call_count(self, node):
        node = unwrap(node)
        if node.kind in {'SwitchExpr','CompileSwitchExpr'}: return self.count(node)
        if node.kind != 'Call': return 1
        if self.deferred_call(node):return 1
        signature = self.s.signature(node)
        return len(self.s.fields(signature.role('results'))) if signature else 1

    def count(self, node, expected=None):
        counts = [len(a.group('args')) if a.kind == 'SwitchResults' else self.call_count(a)
                  for a in self.arms(node) if a.kind != 'SwitchBreak' and not self.s.resolver.is_diverging_call(a)]
        return len(expected) if isinstance(expected, tuple) else max(counts, default=1)

    def hints(self, owner):
        s = self.s
        if owner.kind == 'Assignment':
            return tuple(None if lhs.text == '_' else s.type_hint(lhs) or Hint(s, lhs, f'type_of({s.render(lhs)})')
                         for lhs in owner.group('lhs'))
        if owner.kind == 'Declaration':
            annotation = owner.role('type')
            return (Hint(s, annotation) if annotation else None,) * len(owner.group('names'))
        if owner.kind == 'Return':
            procedure = next((a for a in owner.ancestors() if a.kind == 'Procedure'), None)
            fields = s.fields(procedure.role('type').role('results')) if procedure else []
            return tuple(s.type_hint(t) or Hint(s, t) for _, t in fields)
        return ()

    def result(self, node, index):
        """A native type witness for one result of an arm, without executing it."""
        s, e = self.s, self.e
        if node.kind == 'SwitchResults':
            args = node.group('args')
            return s.render(args[index]) if index < len(args) else e.error(node,'This switch arm supplies too few results.')
        node = unwrap(node)
        if node.kind in {'SwitchExpr', 'CompileSwitchExpr'}:
            types=self.types(node, (None,) * self.count(node))
            return '('+types[index]+'){}' if index<len(types) else e.error(node,'This switch arm supplies too few results.')
        if node.kind != 'Call':
            return s.render(node) if index == 0 else e.error(node,'This switch arm supplies too few results.')
        # procedure_of specializes generics using the actual call's arguments.
        s.needs_intrinsics = True
        return f'({s.prefix}meta.type_proc_return_type(type_of({s.prefix}meta.procedure_of({s.render(node)})), {index})){{}}'

    def types(self, node, hints):
        s = self.s
        types = []
        for i, hint in enumerate(hints):
            if hint:
                types.append(self.e.type_code(hint)); continue
            values = self.queries(node,self.arms(node),i)
            if not values:
                types.append(self.e.error(node, 'A switch expression with only nonreturning/break arms requires expected result types.'))
                continue
            query = values[-1]
            if node.kind == 'CompileSwitchExpr':
                selected = {}
                for case,arm in zip(node.data['cases'],self.arms(node)):
                    if arm.kind == 'SwitchBreak' or s.resolver.is_diverging_call(arm):
                        # Context-free nonreturning selection is diagnosed by
                        # the value renderer. Inactive arms stay unchecked.
                        selected[id(case)] = self.e.error(arm,'A selected nonreturning arm requires expected result types.')
                    else:
                        selected[id(case)] = self.result(arm,i)
                        condition=self.result_condition(arm,i)
                        if condition is not None:
                            invalid=self.e.error(arm,'A selected nonreturning arm requires expected result types.')
                            selected[id(case)]=f'({selected[id(case)]} when {condition} else {invalid})'
                default = next(c for c in node.data['cases'] if not c['matches'])
                query = selected[id(default)]
                for case in reversed([c for c in node.data['cases'] if c['matches']]):
                    query = f'({selected[id(case)]} when {self.e.switch_condition(node,case)} else {query})'
            else:
                for value in reversed(values[:-1]): query = f'(false ? ({value}) : ({query}))'
            s.needs_default = True
            origin=next((arm for arm in self.arms(node) if arm.kind!='SwitchBreak' and not s.resolver.is_diverging_call(arm)),node)
            if origin.kind=='SwitchResults' and i<len(origin.group('args')):origin=origin.group('args')[i]
            types.append(s.mapping.wrap(f'type_of({s.prefix}default_value({query}))',s.path,origin.start,origin.end,'switch-result-type'))
        return types

    def validate(self, node, count):
        cases = node.data['cases']
        problem = self.default_error(node)
        if problem:
            return self.e.error(node, problem)
        for arm in self.arms(node) if node.kind == 'SwitchExpr' else []:
            if arm.kind == 'SwitchBreak' or self.s.resolver.is_diverging_call(arm): continue
            unwrapped = unwrap(arm)
            if unwrapped.kind == 'Call' and (self.deferred_call(unwrapped) or not self.s.signature(unwrapped)): continue
            actual = len(arm.group('args')) if arm.kind == 'SwitchResults' else self.call_count(arm)
            if actual != count:
                error = self.e.error(arm, f'Every switch value arm must return {count} results; this arm returns {actual}.')
                return self.s.mapping.wrap(error,self.s.path,arm.start,arm.end,'switch-arity')
        if node.kind == 'CompileSwitchExpr':
            problem = self.e.validate_compile_cases(node, node.role('selector'))
            if problem: return self.e.error(node, problem)

    def arm_code(self, arm, hints, constant=False):
        unwrapped=unwrap(arm)
        known = arm.kind == 'SwitchResults' or unwrapped.kind != 'Call' or (not self.deferred_call(unwrapped) and self.s.signature(unwrapped))
        actual=len(arm.group('args')) if arm.kind == 'SwitchResults' else self.call_count(arm)
        if known and actual != len(hints):
            error=self.e.error(arm,f'Every switch value arm must return {len(hints)} results; this arm returns {actual}.')
            # Native constant declaration syntax requires one expression per
            # name even in inactive when blocks. Keep the error semantic/lazy.
            return self.s.mapping.wrap(', '.join([error]*len(hints)),self.s.path,arm.start,arm.end,'switch-arity')
        if arm.kind == 'SwitchResults':
            return ', '.join(self.s.big.owned(v, hint, constant) for v, hint in zip(arm.group('args'), hints))
        return self.s.big.owned(arm, hints[0], constant) if len(hints)==1 else self.s.render(arm,hints,constant)

    def consume(self, arm, hints, targets=None, discarded=()):
        """Release anonymous Thor values sent to blanks, after all RHS work."""
        e = self.e
        values = self.values(arm)
        blanks = discarded if targets is None else tuple(v == '_' for v in targets)
        big=self.s.big
        if big.enabled and (big.numeric_results(arm,len(hints)) or
                            any(big.expected(h) and big.expected(h).kind in {'big_integer','big_rational','generic'} for h in hints)):
            code=[]
            names=[e.ownership.fresh('big_switch') for _ in hints]
            if len(values)==len(hints):
                for name,value,hint in zip(names,values,hints):
                    code.append(f'{name}: {e.type_code(hint)} = {big.owned(value,hint)}')
            else: code.append(', '.join(names)+' := '+self.s.render(arm,hints))
            if targets is not None: big.commit(targets,names,code)
            else:
                for blank,name in zip(blanks,names):
                    if blank:code.append(f'{big.module()}.compiler_destroy(&{name})')
                code.append('return '+', '.join(names))
            return '\n'.join(code)
        if len(values) != len(hints) or not any(blank and (e.ownership.may_own(value) or e.ownership.borrows_temporary(value))
                                                for blank, value in zip(blanks, values)):
            return ('return ' if targets is None else ', '.join(targets)+' = ')+self.arm_code(arm, hints)
        code, temporaries = [], []
        for value, hint in zip(values, hints):
            temporaries.append(e.ownership.evaluate(value, code, hint))
        for blank, temp in zip(blanks, temporaries):
            if blank: e.ownership.release(temp, code)
        results = [self.e.type_code(hint)+'{}' if blank else temp.value
                   for hint, blank, temp in zip(hints, blanks, temporaries)]
        code.append(('return ' if targets is None else ', '.join(targets)+' = ')+', '.join(results))
        return '\n'.join(code)

    def render(self, node, expected, constant):
        s, e = self.s, self.e
        count = self.count(node, expected)
        problem = self.validate(node, count)
        if problem: return problem
        if any(a.kind == 'SwitchBreak' for a in self.arms(node)):
            return e.error(node, 'break in a switch expression is allowed only as the entire right-hand side of an ordinary = assignment.')
        hints = expected if isinstance(expected, tuple) else (expected,) if count == 1 else (None,) * count
        # Scalar switches still use the established single-value lowering.
        types = self.types(node, hints)
        parent = node
        while parent.parent and parent.parent.kind == 'Paren': parent = parent.parent
        owner = parent.parent
        names = owner.group('names') if owner and owner.kind == 'Declaration' else owner.group('lhs') if owner and owner.kind == 'Assignment' else []
        discarded = tuple(name.text == '_' for name in names)
        def body(generic):
            lines = []
            cases = node.data['cases']
            if node.kind == 'SwitchExpr':
                selector,kind=self.runtime_selector(node,lines)
            else:
                cases = [c for c in cases if c['matches']] + [c for c in cases if not c['matches']]
                lines.append('when '+e.validation(node)+' {')
            branches = []
            for i, case in enumerate(cases):
                arm = s.at(case['result'], node)
                value = self.branch(arm,lambda:self.consume(arm, generic, discarded=discarded))
                if node.kind == 'SwitchExpr':
                    branches.append((case, value))
                else:
                    lines.append(('when ' if i == 0 else 'else when ')+e.switch_condition(node, case)+' { '+value+' }')
            if node.kind == 'SwitchExpr':
                lines.append(self.runtime_code(node, selector, kind, branches))
            else:
                lines.append('}')
            return '\n'.join(lines)
        name, args = self.closed(node, body, result=(None,) * count, flavor='switch_multi')
        return name+'('+', '.join([*args, *types])+')'

    def statement(self, owner, constant):
        if owner in self.statements: return None
        role = {'Assignment': 'rhs', 'Declaration': 'values', 'Return': 'values'}.get(owner.kind)
        values = owner.group(role) if role else []
        if len(values) != 1: return None
        root, node = values[0], unwrap(values[0])
        if node.kind not in {'SwitchExpr','CompileSwitchExpr'}: return None
        if not self.special(node, self.hints(owner)): return None
        arms = self.arms(node)
        skip = any(a.kind == 'SwitchBreak' for a in arms)
        if skip and (owner.kind != 'Assignment' or owner.data['op'] != '='):
            return self.e.error(node, 'break in a switch expression requires an ordinary = assignment to existing destinations.')
        global_binding = (owner.kind == 'Declaration' and owner.data.get('mutable')
                          and len(owner.group('names')) > 1 and not any(a.kind == 'Procedure' for a in owner.ancestors()))
        if global_binding: return self.global_binding(owner,root,node)
        if not skip and node.kind != 'CompileSwitchExpr': return None
        if not skip and owner.parent and owner.parent.kind in {'For','If','Switch','Range'}:
            if owner.parent.role('init') is owner or owner.parent.role('post') is owner:
                return None # Header slots require a value-producing helper.
        hints = self.hints(owner)
        problem = self.validate(node, len(hints))
        if problem: return problem
        s, e = self.s, self.e

        def selected(arm):
            if arm.kind == 'SwitchBreak': return ''
            diverging = s.resolver.is_diverging_call(arm)
            if diverging and owner.kind != 'Declaration': return s.render(arm)
            self.statements.add(owner)
            if diverging:
                values=[]
                for i,hint in enumerate(hints):
                    if hint:
                        name,args=e.closed(arm,lambda _:s.render(arm),result=None,flavor=f'switch_decl_panic_{i}')
                        values.append(name+'('+', '.join([*args,e.type_code(hint)])+')')
                    else:
                        values.append(s.mapping.wrap(e.error(arm,'A selected nonreturning arm requires expected result types.'),
                                                     s.path,arm.start,arm.end,'switch-panic'))
                replacement=', '.join(values)
            elif self.deferred_call(arm):
                name,args=self.closed(arm,lambda generic:self.branch(arm,lambda:self.consume(arm,generic)),
                                      result=(None,)*len(hints),flavor='switch_selected_call')
                replacement=name+'('+', '.join([*args,*self.types(node,hints)])+')'
            else: replacement = self.arm_code(arm, hints, constant)
            names = owner.group('names') if owner.kind == 'Declaration' else owner.group('lhs') if owner.kind == 'Assignment' else []
            blanks = tuple(name.text == '_' for name in names)
            if (not constant and arm.kind == 'SwitchResults' and
                    any(blank and (e.ownership.may_own(value) or e.ownership.borrows_temporary(value))
                        for blank,value in zip(blanks,self.values(arm)))):
                name,args=e.closed(arm,lambda generic:self.consume(arm,generic,discarded=blanks),
                                   result=(None,)*len(hints),flavor='switch_selected_discard')
                replacement=name+'('+', '.join([*args,*self.types(node,hints)])+')'
            self.replacements[root] = replacement
            try: return s.render(owner, constant=constant)
            finally:
                self.replacements.pop(root)
                self.statements.remove(owner)

        def body(_):
            code = []
            # Capture storage locations before evaluating the switch, as in a
            # native assignment. A map index uses the header's address and a
            # separately captured key, since map elements have no address.
            lhs_codes = []
            local_hints = hints
            if skip:
                for i, lhs in enumerate(owner.group('lhs')):
                    target = unwrap(lhs)
                    name = s.prefix+f'switch_lhs_{node.start}_{i}'
                    if target.text == '_': lhs_codes.append('_'); continue
                    if target.kind == 'Index' and (e.shape(target.role('expr')).kind == 'map' or target in e.native_access):
                        base, key = target.role('expr'), target.role('index')
                        if e.addressable(base):
                            code.append(f'{name} := &({e.address(base)});')
                            access = name+'^'
                        else:
                            code.append(f'{name} := {s.render(base)};')
                            access = name
                        key_name = name+'_key'
                        code.append(f'{key_name}: type_of({e.module("sets")}.key_witness({access})) = {s.render(key)};')
                        lhs_codes.append(f'{access}[{key_name}]')
                    else:
                        code.append(f'{name} := &({e.address(lhs)});')
                        lhs_codes.append(name+'^')
                local_hints = tuple(None if lhs == '_' else Hint(s, original, f'type_of({lhs})')
                                    for lhs, original in zip(lhs_codes, owner.group('lhs')))
            cases = node.data['cases']
            if node.kind == 'SwitchExpr':
                selector,kind=self.runtime_selector(node,code)
            else:
                cases = [c for c in cases if c['matches']] + [c for c in cases if not c['matches']]
                code.append('when '+e.validation(node)+' {')
            branches = []
            for i, case in enumerate(cases):
                arm = s.at(case['result'], node)
                if skip and arm.kind != 'SwitchBreak' and not s.resolver.is_diverging_call(arm):
                    # Supply a native type for blank destinations only when
                    # cleanup needs a temporary to hold their value.
                    typed = tuple(hint or Hint(s,arm,self.types(node,(None,)*len(local_hints))[j])
                                  for j,hint in enumerate(local_hints))
                    content = self.branch(arm,lambda:self.consume(arm, typed, lhs_codes))
                else: content = selected(arm)
                if node.kind == 'SwitchExpr':
                    branches.append((case, content))
                else:
                    code.append(('when ' if i == 0 else 'else when ')+e.switch_condition(node, case)+' { '+content+' }')
            if node.kind == 'SwitchExpr':
                code.append(self.runtime_code(node, selector, kind, branches, returns=False))
            else:
                code.append('}')
            return '\n'.join(code)

        if not skip: return body(None)
        post = owner.parent and owner.parent.kind == 'For' and owner.parent.role('post') is owner
        def helper(_): return body(None)+ ('\nreturn true' if post else '')
        native = '_'.join(str(index.start) for index in sorted(e.native_access, key=lambda v:v.start))
        name, args = e.closed(owner, helper, result='bool' if post else False, flavor='switch_assignment_'+native)
        return ('_ = ' if post else '')+name+'('+', '.join(args)+')'

    def global_binding(self, owner, root, node):
        """Odin forbids global multi-value initializers: stage one stack record.

        One private global holds the returned headers/values. The actual names
        depend on that initializer, preserving native global dependency order.
        No heap allocation or delayed init procedure is introduced.
        """
        s,e=self.s,self.e
        hints=self.hints(owner)
        problem=self.validate(node,len(hints))
        if problem:return problem
        self.global_context += 1
        try: types=self.types(node,hints)
        finally: self.global_context -= 1
        record='struct{'+', '.join(f'value_{i}: {typ}' for i,typ in enumerate(types))+'}'
        def body(generic):
            values=[s.prefix+f'result_{i}' for i in range(len(hints))]
            fields=tuple(Hint(s,node,f'type_of({generic.expression}{{}}.value_{i})') for i in range(len(hints)))
            declarations=[f'{value}: {e.type_code(hint)}' for value,hint in zip(values,fields)]
            expression=s.render(node,fields)
            return '\n'.join([*declarations,', '.join(values)+' = '+expression,'return '+generic.expression+'{'+', '.join(values)+'}'])
        self.global_context += 1
        try: name,args=e.closed(owner,body,result=None,flavor='switch_global')
        finally: self.global_context -= 1
        temporary=s.prefix+f'global_results_{node.start}'
        call=name+'('+', '.join([*args,record])+')'
        self.statements.add(owner)
        self.replacements[root]=', '.join(f'{temporary}.value_{i}' for i in range(len(hints)))
        try: declaration=s.render(owner)
        finally:
            self.replacements.pop(root)
            self.statements.remove(owner)
        # Insert support storage with the other generated declarations. Keeping
        # it outside the replacement preserves attributes on the real bindings.
        key=e.helper_scope(owner),temporary
        e.helpers[key]=s.mapping.wrap(f'\n@(private="file") {temporary} := {call}\n',
                                      s.path,owner.start,owner.end,'expression-helper')
        return declaration

"""Typed interval values and checked array/slice index bindings."""
import json


class TypedLoops:
    def __init__(self, source):
        self.s = source

    def mapped(self, text, node, kind='typed-loop-check'):
        return self.s.mapping.wrap(text,self.s.path,node.start,node.end,kind)

    def invalid(self, annotation, message):
        token=self.s.prefix+f'typed_loop_error_{annotation.start}'
        self.s.error_messages[token]=message
        return self.mapped(f'#assert(false, {json.dumps(token)})',annotation)

    def native_index(self, node):
        return self.s.prefix+f'loop_index_{node.start}'

    def prologue(self, block):
        loop=block.parent
        if not loop or loop.kind!='Range' or loop.role('body') is not block:return ''
        annotation=loop.role('index_type')
        if not annotation:return ''
        name=loop.group('names')[1]
        target=self.s.render(annotation)
        module=self.s.expressions.module('sequence')
        separator=' = ' if name.text=='_' else ' := '
        return self.mapped(f'\n{self.s.copy(name.start,name.end)}{separator}{module}.checked_loop_index({self.native_index(loop)}, {target})\n',annotation)

    def render(self, node):
        if node.kind!='Range':return None
        value_type,index_type=node.role('value_type'),node.role('index_type')
        if not value_type and not index_type:
            extra=next((binding.role('value') for binding in node.group('bindings') if binding.kind=='FieldValue'),None)
            return self.invalid(extra,'Typed loops support at most a value binding and an index binding.') if extra else None
        s=self.s
        expr=node.role('expr')
        interval=expr
        while interval.kind=='Paren':interval=interval.role('expr')
        is_range=interval.kind=='Binary' and interval.data['op'] in {'..','..<','..='}
        names=node.group('names')
        if len(names)>2:
            return self.invalid(index_type or value_type,'Typed loops support at most a value binding and an index binding.')
        if len(names)==2 and names[0].text.lstrip('&')==names[1].text and names[1].text!='_':
            return self.invalid(index_type or value_type,'Duplicate loop binding name: '+names[1].text)
        if value_type and (not is_range or len(node.group('names'))>2):
            return self.invalid(value_type,'A typed value binding is supported only for numeric ranges; collection element types are inferred.')
        if index_type and is_range:
            return self.invalid(index_type,'A typed index binding is supported only for arrays and slices, not numeric ranges.')
        if index_type and expr.kind=='Call':
            plan=s.resolver.resolve_call(expr) if s.resolver else None
            if plan and len(s.resolver.fields(plan.signature.role('results')))!=1:
                return self.invalid(index_type,'Typed index bindings are supported only for arrays and slices; iterator procedures keep their inferred bindings.')
        if any(name.kind=='Unary' for name in node.group('names')[1:]):
            return self.invalid(index_type or value_type,'An index binding cannot be a mutable reference.')
        s.needs_intrinsics=True
        meta=s.prefix+'meta'
        code=[]
        init=node.role('init')
        # Odin collects a when condition before local variables in its own
        # block. Keep a lifted initializer in a parent block so type_of of an
        # initializer binding remains usable in annotations and type witnesses.
        initialization=s.render(init)+'\n{\n' if init else ''
        replacements=[]
        if init:
            first=node.data['lists']['bindings'][0][0]
            replacements.append((init.start,first,s.comments(init.end,first)))
        for i,binding in enumerate(node.group('bindings')):
            name=node.group('names')[i]
            replacements.append((binding.start,binding.end,
                self.mapped(self.native_index(node),name) if i==1 and index_type else s.copy(name.start,name.end)))
        if value_type:
            target=s.render(value_type)
            numeric=f'({meta}.type_is_integer({target}) || {meta}.type_is_float({target}))'
            token=s.prefix+f'typed_loop_numeric_{value_type.start}'
            s.error_messages[token]='A typed numeric range requires a native integer or floating-point type.'
            code.append(self.mapped(f'#assert({numeric}, {json.dumps(token)})\n',value_type))
            helper=s.prefix+f'loop_bound_{node.start}'
            scope=s.expressions.helper_scope(node)
            declaration=f'\n{helper} :: proc "contextless"($T: typeid, value: T) -> T {{return value}}\n'
            s.expressions.helpers[scope,helper]=self.mapped(declaration,value_type,'expression-helper')
            bounds=[]
            for side in ['left','right']:
                bound=interval.role(side)
                bounds.append((bound.start,bound.end,self.mapped(f'{helper}({target}, {s.render(bound)})',bound,'typed-range-bound')))
            replacements.append((expr.start,expr.end,s.splice(expr,bounds)))
            condition=numeric
        else:
            target=s.render(index_type)
            module=s.expressions.module('sequence')
            typ=s.expressions.ownership.native_type(expr)
            code.append(self.mapped(f'{module}.validate_loop_index({typ}, {target})\n',index_type))
            # Native iteration still owns evaluation/addressability of its input.
            # Its type query never executes a getter or an iterator call.
            def collection(t):
                return f'(({meta}.type_is_array({t}) && !{meta}.type_is_enumerated_array({t})) || {meta}.type_is_slice({t}) || {meta}.type_is_dynamic_array({t}) || {meta}.type_is_fixed_capacity_dynamic_array({t}))'
            trait=f'({collection(meta+".type_elem_type("+typ+")")} when {meta}.type_is_pointer({typ}) else {collection(typ)})'
            condition=f'{trait} && {meta}.type_is_integer({target})'
            ownership=s.expressions.ownership
            if ownership.may_own(expr) or ownership.borrows_temporary(expr):
                value=ownership.evaluate(expr,code)
                ownership.release(value,code,deferred=True)
                replacements.append((expr.start,expr.end,value.value))
        body=node.role('body')
        replacements.append((body.start,body.end,s.render(body)))
        # Copy unannotated children such as a native iterable expression, but
        # never emit the Field_Value representation used for annotated names.
        covered=lambda child:any(a<=child.start and child.end<=b for a,b,_ in replacements)
        replacements.extend((child.start,child.end,s.render(child)) for child in node.children if not covered(child))
        loop=s.splice(node,replacements)
        code.append(self.mapped('when '+condition+' {\n',value_type or index_type)+loop+'\n}')
        return '{\n'+initialization+'\n'.join(code)+('\n}' if init else '')+'\n}'

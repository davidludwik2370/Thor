"""Purity contracts, borrowed input provenance, and compiler scratch lifetimes.

Native Odin checks exact instantiated types and inactive when branches. This
pass tracks effects and provenance; it never treats contextless as purity.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from .compiler import Hint
from .semantics import descendants
from .pureborrow import Borrowing, Permission, OWNED, READONLY, permission


@dataclass(eq=False)
class Flow:
    borrowed: object = OWNED
    fields: dict = field(default_factory=dict)
    element: object = None
    numeric_copy: bool = False
    target: object = None
    location: object = OWNED
    locations: set = field(default_factory=set)
    origins: set = field(default_factory=set)
    copied_from: object = None
    aliases: tuple = ()
    owner: object = None
    stack: set = field(default_factory=set)
    kind: str = 'unknown'
    copied_base: object = None
    typ: object = None

    def __post_init__(self):
        self.borrowed=permission(self.borrowed)
        self.location=permission(self.location)
        if self.locations and not self.origins:self.origins=set(self.locations)

    def merge(self, other, seen=None):
        if other is self: return False
        seen=set() if seen is None else seen
        if (id(self),id(other)) in seen: return False
        seen.add((id(self),id(other)))
        numeric_copy=(self.numeric_copy and other.numeric_copy if self.borrowed else other.numeric_copy) if other.borrowed else self.numeric_copy
        borrowed=self.borrowed|other.borrowed
        changed=borrowed!=self.borrowed or numeric_copy!=self.numeric_copy
        self.numeric_copy=numeric_copy
        self.borrowed=borrowed
        if not other.origins.issubset(self.origins):self.origins.update(other.origins);changed=True
        if not other.stack.issubset(self.stack):self.stack.update(other.stack);changed=True
        for name,value in other.fields.items():
            if name not in self.fields: self.fields[name]=value;changed=True
            else: changed |= self.fields[name].merge(value,seen)
        if other.element:
            if self.element is None:self.element=other.element;changed=True
            else:changed |= self.element.merge(other.element,seen)
        if other.target and self.target is None:self.target=other.target;changed=True
        elif other.target and other.target is not self.target:
            targets=self.target.aliases or (self.target,)
            incoming=other.target.aliases or (other.target,)
            combined=tuple(dict.fromkeys([*targets,*incoming]))
            if len(combined)>len(targets):self.target=Flow(aliases=combined);changed=True
        return changed

    def member(self,name):
        if self.kind=='pointer' or self.typ and self.typ.kind=='pointer':return self.deref().member(name)
        resolver=self.typ.node.source.resolver if self.typ and self.typ.node else None
        path=resolver.field_path(self.typ,name) if resolver else None
        if path and len(path)>1:
            value=self
            for component in path:value=value.member(component)
            return value
        value=self.fields.setdefault(name,Flow(owner=self.owner))
        member=resolver.field(self.typ,name) if resolver else None
        if member:value.typ=member[0]
        elif self.typ and name in self.typ.members:value.typ=self.typ.members[name]
        value.origins.update(origin.step(('field',name)) for origin in self.origins if len(origin.path)<24)
        if self.borrowed:
            value.borrowed|=self.borrowed.project(('field',name))
            value.numeric_copy=False
        if self.locations:
            value.locations.update(location.step(('field',name)) for location in self.locations if len(location.path)<24)
            value.location=self.location_for(('field',name))
        elif self.copied_from and self.origins:
            value.location=self.origin_location(('field',name))&Permission('type',('pointer',self.copied_from))
        else:value.location=self.location
        # An elided aggregate may not yet have an exact record type. Its
        # positional contents still taint a subsequently selected member.
        if self.element:value.merge(self.element)
        return value

    def item(self):
        if self.element is None:self.element=Flow(owner=self.owner if self.kind in {'array','fixed','generic','unknown'} else None)
        if self.typ and self.typ.elem:self.element.typ=self.typ.elem
        self.element.origins.update(origin.step(('item',)) for origin in self.origins if len(origin.path)<24)
        if self.borrowed:
            self.element.borrowed|=self.borrowed.project(('item',))
            self.element.numeric_copy=False
        if self.locations:
            self.element.locations.update(location.step(('item',)) for location in self.locations if len(location.path)<24)
            self.element.location=self.location_for(('item',))
        else:self.element.location=self.borrowed
        return self.element

    def location_for(self,step):
        result=OWNED
        for location in self.locations:
            if len(location.path)>=24:return READONLY
            result|=Permission('access',(location.step(step,'location'),))
        if self.copied_from:
            trait='pointer' if step[0]=='field' else 'sequence'
            return result&Permission('type',(trait,self.copied_from))
        return result

    def origin_location(self,step):
        result=OWNED
        for origin in self.origins:
            if len(origin.path)>=24:return READONLY
            result|=Permission('access',(origin.step(step,'location'),))
        return result

    def deref(self):
        if self.target and self.target.aliases:return Flow.joined(self.target.aliases)
        if self.target is None:
            if self.copied_base is not None:
                self.target=self.copied_base.deref()
                return self.target
            if any(len(origin.path)>=24 for origin in self.origins):return Flow(READONLY,location=READONLY)
            self.target=Flow(self.borrowed.project(('deref',)),location=self.origin_location(('deref',)),
                             locations={origin.step(('deref',)) for origin in self.origins})
        if self.typ and self.typ.kind=='pointer':self.target.typ=self.typ.elem
        return self.target

    def header_copy(self,typ,node=None,seen=None,owner=None):
        seen={} if seen is None else seen
        if id(self) in seen:return seen[id(self)]
        result=Flow(self.borrowed,numeric_copy=self.numeric_copy,target=self.target,copied_from=node,owner=owner,stack=set(self.stack),kind=typ.kind,typ=typ)
        if typ.kind in {'pointer','generic','unknown'}:result.copied_base=self
        result.origins=set(self.origins)
        seen[id(self)]=result
        if typ.kind=='struct':
            fields=typ.node.role('fields') if typ.node else None
            for field in fields.children if fields else []:
                if field.kind=='Field':
                    for name in field.group('names'):
                        result.fields[name.text]=self.member(name.text).header_copy(field.source.resolver.type_node(field.role('type'),typ.substitutions),node,seen,owner)
            result.borrowed=OWNED
        elif typ.kind in {'array','fixed'}:
            result.borrowed=OWNED
            result.element=self.item().header_copy(typ.elem,node,seen,owner)
        else:
            result.fields=dict(self.fields);result.element=self.element
        return result

    @staticmethod
    def joined(values,seen=None):
        values=tuple(dict.fromkeys(values))
        if len(values)==1:return values[0]
        seen={} if seen is None else seen
        key=tuple(map(id,values))
        if key in seen:return seen[key]
        result=Flow(aliases=values);seen[key]=result
        for value in values:
            result.borrowed|=value.borrowed;result.location|=value.location
            result.locations.update(value.locations);result.origins.update(value.origins)
            result.stack.update(value.stack)
        names={name for value in values for name in value.fields}
        result.fields={name:Flow.joined([value.member(name) for value in values],seen) for name in names}
        if any(value.element for value in values):result.element=Flow.joined([value.item() for value in values],seen)
        targets=[value.target for value in values if value.target]
        if targets:result.target=Flow(aliases=tuple(targets))
        return result

    def write(self,other,seen=None):
        seen=set() if seen is None else seen
        if id(self) in seen:return False
        seen.add(id(self));changed=self.merge(other)
        for alias in self.aliases:changed|=alias.write(other,seen)
        return changed

    def stack_owners(self,seen=None):
        seen=set() if seen is None else seen
        if id(self) in seen:return set()
        seen.add(id(self));result=set(self.stack)
        children=[*self.fields.values(),self.element,self.target,*self.aliases]
        for child in children:
            if child:result.update(child.stack_owners(seen))
        return result


class Purity:
    storage_message='Pure code cannot inspect Integer/Rational implementation storage; use numeric operators and public properties.'
    builtins={'len','cap','size_of','align_of','offset_of','type_of','min','max','abs','clamp',
              'complex','quaternion','real','imag','jmag','kmag','conj','swizzle',
              'make','copy','delete','assert','assert_contextless','panic'}
    sequence_mutators={'append','clear','pop','pop_safe','pop_front','pop_front_safe'}
    builtins |= sequence_mutators
    type_queries={'type_of','size_of','align_of','offset_of'}
    libraries={('thor:format','format'),('thor:format','format_plain'),('thor:format','format_options'),('thor:format','format_spec'),
               ('thor:format','group_integer'),('core:strings','clone'),('core:slice','clone')}
    libraries |= {('thor:big',name) for name in ['from_integer','rational_from_integer','fraction','parse_integer','parse_rational','parse_fraction','clone','numerator','denominator','to_string','equal','is_zero','sum','ascending','descending']}
    native_constants={('core:math',name) for name in ['PI','TAU','E','SQRT_TWO','SQRT_THREE','SQRT_FIVE']}
    # These APIs perform observable I/O, rather than formatting into owned or
    # caller-provided memory. Reject them at the call, before cloning their
    # variadic/callback-heavy implementation into the checked call graph.
    io_libraries={('core:fmt',name) for name in [
        'print','println','printf','printfln','eprint','eprintln','eprintf','eprintfln',
        'fprint','fprintln','fprintf','fprintfln','fprint_type','fprint_typeid',
        'wprint','wprintln','wprintf','wprintfln','wprint_type','wprint_typeid']}
    io_libraries |= {('thor:basic',name) for name in ['print','input','read','write']}
    scalar_intrinsics = {'sqrt', 'fused_mul_add', 'count_ones', 'count_zeros',
        'count_leading_zeros', 'count_trailing_zeros', 'count_leading_ones',
        'count_trailing_ones', 'reverse_bits', 'byte_swap', 'saturating_add',
        'saturating_sub', 'constant_log2', 'expect', 'likely', 'unlikely'}
    source_identity_intrinsics = {'type_canonical_name', 'type_name_of'}
    structural_intrinsics = {'type_base_type','type_core_type','type_elem_type',
        'type_union_tag_type','type_union_tag_offset','type_union_base_tag_value',
        'type_union_variant_count','type_variant_type_of','type_variant_index_of',
        'type_bit_set_elem_type','type_bit_set_underlying_type','type_field_type',
        'type_field_bit_size','type_field_bit_offset','type_proc_parameter_count',
        'type_proc_return_count','type_proc_parameter_type','type_proc_return_type',
        'type_proc_calling_convention','type_struct_field_count','type_struct_has_implicit_padding',
        'type_polymorphic_record_parameter_count','type_field_index_of',
        'type_fixed_capacity_dynamic_array_len_offset','type_enum_is_contiguous',
        'type_convert_variants_to_pointers','type_merge','type_integer_to_unsigned','type_integer_to_signed'}

    def __init__(self,project):
        self.project=project
        self.r=project.resolver
        self.problems={}
        self.conditions={}
        self.analyzed=set()
        self.eval_mode=False
        self.rendering=set()
        self.active=set()
        self.contracts={}
        self.inner_bodies=set()
        self.scalar_casts=set()
        self.invalid_defaults=set()
        self.storage_reads={}
        self.storage_writes={}
        self.pointer_checks={}
        self.entry_roots=set()
        self.borrowing=Borrowing(self)
        from .purecalls import PureCalls
        self.calls=PureCalls(self)
        for source in self.r.sources:source.purity=self

    @staticmethod
    def owner(n):return next((a for a in n.ancestors() if a.kind=='Procedure' or a.data.get('checked_comprehension')),None)

    @staticmethod
    def pure(proc):return proc and proc.kind=='Procedure' and proc.data.get('pure',False)

    @staticmethod
    def contextual(proc):
        cc=proc.role('type').data.get('calling_convention','')
        if cc=='foreign':
            foreign=next((a for a in proc.ancestors() if a.kind=='ForeignBlock'),None)
            cc=foreign.data.get('calling_convention','c') if foreign else 'c'
        return cc in {'','odin'}

    def checked(self,proc):return bool(proc and (self.pure(proc) or self.eval_mode and proc in self.active or proc in self.calls.emitting))

    def invalid_default(self,node):return any(parent in self.invalid_defaults for parent in (node,*node.ancestors()))

    def module(self,source):return source.expressions.module('pure')

    def problem(self,n,message,condition=None):
        if condition:
            self.conditions.setdefault(n,[]).append((condition,message))
        else:self.problems.setdefault(n,message)

    def reference(self,n):
        if n.kind!='Ident':return False
        p=n.parent
        return not(p and (p.kind in {'Declaration','Field','Range','TypeSwitch'} and n in p.group('names')
                         or p.kind in {'Selector','FieldValue'} and p.role('field') is n
                         or p.kind in {'PolyType','EnumType','ImplicitSelector'}))

    def imported(self,callee):
        if callee is None:return None
        if callee.kind=='Selector':
            base=callee.role('expr')
            if base.kind=='Ident' and base.text in callee.source.imports and self.r.lookup(base.text,base) is None:
                return callee.source.imports.get(base.text),callee.role('field').text
        if callee.kind=='Ident':
            symbol=self.r.lookup(callee.text,callee)
            if symbol is None and callee.text in callee.source.selected:
                namespace,name=callee.source.selected[callee.text].split('.',1)
                return callee.source.imports.get(namespace),name
            if symbol and not symbol.projection and symbol.scope.kind=='File' and symbol.node.source.path.parent!=callee.source.path.parent:
                # A namespace using resolves to the original exported symbol.
                # Retain its package identity for reviewed library contracts,
                # without giving a same-named local helper that identity.
                for path in callee.source.imports.values():
                    directory=callee.source.import_dir(path)
                    if directory is None and self.r.native:directory=self.r.native.directory(callee.source,path)
                    if directory==symbol.node.source.path.parent:return path,symbol.name
        return None

    def library(self,call):
        def resolve(callee,seen):
            if callee is None or callee in seen:return None
            seen.add(callee)
            if callee.kind=='Paren':return resolve(callee.role('expr'),seen)
            imported=self.imported(callee)
            if imported:return imported
            symbol=self.r.symbol(callee)
            if symbol and not symbol.declaration.data.get('mutable') and not symbol.immutable:
                value=symbol.value
                if value and value.kind in {'Ident','Selector','Paren'}:return resolve(value,seen)
            return None
        return resolve(call.role('callee'),set())

    @staticmethod
    def arguments(call,names):
        """Bind effect-sensitive arguments by parameter, not source order."""
        bound={};position=0
        for arg in call.group('args'):
            if arg.kind=='FieldValue':bound[arg.role('field').text]=arg.role('value')
            else:
                if position<len(names):bound[names[position]]=arg
                position+=1
        return [bound.get(name) for name in names]

    def call_error(self,call):
        callee=call.role('callee');symbol=self.r.symbol(callee);library=self.library(call)
        if library and library[0]=='base:intrinsics':
            return f"Pure code cannot call compiler intrinsic '{callee.text}': it is not an eligible pure operation."
        reason='indirect, foreign and unknown calls are forbidden.'
        if symbol:
            candidates=self.r.procedure_candidates(callee)
            if candidates and any(not signature.parent or signature.parent.kind!='Procedure' or not signature.parent.role('body') for _,signature in candidates):
                reason=f"The foreign or bodyless procedure '{callee.text}' has no inspectable implementation."
            elif symbol.declaration.kind in {'Field','Range','TypeSwitch'} or symbol.declaration.data.get('mutable') or symbol.immutable:
                reason=f"An indirect call through runtime procedure value '{callee.text}' is not eligible."
        elif callee.kind=='Ident' and not library:
            # Preserve Odin's actual undeclared-name diagnostic.
            return None
        return 'Pure code requires a resolved direct call to an eligible procedure; '+reason

    def io_procedure(self,proc):
        """Recognize library identity, never a user procedure's spelling."""
        if proc is None:return False
        declaration=proc.parent
        while declaration and declaration.kind=='Paren':declaration=declaration.parent
        if not declaration or declaration.kind!='Declaration':return False
        names={name.text for name in declaration.group('names')}
        source=proc.source
        basic=Path(__file__).resolve().parents[1]/'runtime'/'basic'
        if source.path.parent==basic:
            return any(('thor:basic',name) in self.io_libraries for name in names)
        native=getattr(self.project,'native_sources',None) or self.r.native
        if native and native.root and getattr(source,'native_inspected',False):
            if source.path.parent==(Path(native.root)/'core'/'fmt').resolve():
                return any(('core:fmt',name) in self.io_libraries for name in names)
        return False

    def io_call(self,call):
        def effect(callee,seen):
            if callee is None or callee in seen:return False
            seen.add(callee)
            if callee.kind=='Paren':return effect(callee.role('expr'),seen)
            imported=self.imported(callee)
            if imported in self.io_libraries:
                directory=callee.source.import_dir(imported[0])
                if imported[0]=='core:fmt' and directory is None:return True
                if imported[0]=='thor:basic' and directory==Path(__file__).resolve().parents[1]/'runtime'/'basic':return True
            symbol=self.r.symbol(callee)
            if not symbol or symbol.declaration.data.get('mutable') or symbol.immutable:return False
            value=symbol.value
            if value and value.kind=='Procedure':return self.io_procedure(value)
            if value and value.kind in {'Ident','Selector','Paren'}:return effect(value,seen)
            return False
        return effect(call.role('callee'),set())

    def io_message(self,call):
        subject='Pure code'
        proc=self.owner(call)
        if self.pure(proc):
            declaration=proc.parent
            while declaration and declaration.kind=='Paren':declaration=declaration.parent
            names=declaration.group('names') if declaration and declaration.kind=='Declaration' else []
            if len(names)==1:subject=f"Pure function '{names[0].text}'"
        return (f"{subject} cannot call '{call.role('callee').text}' because it performs I/O. "
                'Use proc for I/O and keep it outside fn/#run call chains.')

    def approved(self,call):
        if self.primitive(call.role('callee')):return True
        library=self.library(call)
        if library not in self.libraries:return False
        package=call.source.import_dir(library[0])
        if library[0] in {'thor:format','thor:big'}:
            return package==Path(__file__).resolve().parents[1]/'runtime'/library[0].split(':')[1]
        return package is None # installed native package, not a user override

    def primitive(self,callee,seen=None):
        if callee is None:return False
        seen=set() if seen is None else seen
        if callee in seen:return False
        seen.add(callee)
        imported=self.imported(callee)
        if imported and imported[0]=='base:intrinsics' and callee.source.import_dir(imported[0]) is None:
            return imported[1] in self.scalar_intrinsics|self.structural_intrinsics or imported[1].startswith(('type_is_','type_has_'))
        symbol=self.r.symbol(callee)
        if not symbol or symbol.declaration.data.get('mutable') or symbol.immutable:return False
        if symbol.value and symbol.value.kind in {'Ident','Selector','Paren'}:
            value=symbol.value.role('expr') if symbol.value.kind=='Paren' else symbol.value
            return self.primitive(value,seen)
        if not getattr(symbol.node.source,'native_inspected',False):return False
        foreign=next((a for a in symbol.declaration.ancestors() if a.kind=='ForeignBlock'),None)
        if not foreign or foreign.data.get('calling_convention')!='none':return False
        for attribute in symbol.declaration.group('attributes'):
            for item in attribute.group('elems'):
                if item.kind=='FieldValue' and item.role('field').text=='link_name':
                    name=item.role('value').text.strip('"')
                    if re.fullmatch(r'llvm\.(sin|cos|pow|exp|exp2|log|log2|log10|fmuladd|fma|sqrt|floor|ceil|trunc|round|roundeven)\.f(16|32|64)',name):return True
        return False

    def builtin_name(self,callee,seen=None):
        if callee is None:return None
        seen=set() if seen is None else seen
        if callee in seen:return None
        seen.add(callee)
        if callee.kind=='Paren':return self.builtin_name(callee.role('expr'),seen)
        imported=self.imported(callee)
        if imported and imported[0]=='base:builtin' and callee.source.import_dir(imported[0]) is None:
            return imported[1] if imported[1] in self.builtins else None
        symbol=self.r.symbol(callee)
        if symbol and not symbol.declaration.data.get('mutable') and not symbol.immutable and symbol.value:
            if symbol.value.kind in {'Ident','Selector','Paren'}:return self.builtin_name(symbol.value,seen)
        if callee.kind=='Ident' and symbol is None and not imported and callee.text in self.builtins:return callee.text
        return None

    def builtin(self,call):
        extrema=call.source.expressions.extrema.builtin(call.role('callee'))
        if extrema:return extrema
        callee=call.role('callee')
        if callee.kind=='Directive' and callee.data['name'] in {'assert','config'}:return '#'+callee.data['name']
        return self.builtin_name(callee)

    def scalar_transmute(self,node):
        types=[self.r.type_node(node.role('type')),self.r.type_of(node.role('expr'))]
        def safe(t):
            if t.kind in {'enum','bitset'}:return True
            if t.kind=='scalar':return t.name not in {'rawptr','any','typeid','cstring'}
            if t.kind in {'unknown','generic'}:return None
            return False
        allowed=[safe(t) for t in types]
        return False if False in allowed else None if None in allowed else True

    def targets(self,call):
        if self.eval_mode and call in self.calls.targets:return self.calls.targets[call]
        direct=self.r.symbol(call.role('callee'))
        if direct and direct.declaration.data.get('mutable',False):return []
        plan=self.r.resolve_call(call)
        candidates=[(plan.symbol,plan.signature)] if plan else self.r.procedure_candidates(call.role('callee'))
        result=[]
        for symbol,signature in candidates:
            if symbol.declaration.data.get('mutable',False):return []
            proc=signature.parent
            if proc and proc.kind=='Procedure' and proc.role('body'):result.append(proc)
            else:return []
        return result

    def validator(self,s):
        """Use shared AST field names, and native exact instantiated field types.

        This Odin release cannot unroll struct fields. A count assertion rejects
        external record schemas we cannot inspect instead of trusting them.
        """
        name=s.prefix+'pure_validate';key=s.root,name
        if key in s.expressions.helpers:return name
        names=sorted({n.text for unit in self.r.sources for record in unit.nodes if record.kind=='StructType'
                      for f in record.role('fields').children if f.kind=='Field' for n in f.group('names')})
        s.needs_intrinsics=True;meta=s.prefix+'meta';native=self.module(s)
        parts=[f'when Depth>24 {{#panic("Pure functions do not support recursive reference-containing types or value nesting deeper than 24 levels"+Site)}}']
        if not getattr(s,'native_inspected',False) and any(unit.path==Path(__file__).resolve().parents[1]/'runtime'/'big'/'big.odin' for unit in self.r.sources):
            parts.append(f'else when T=={s.big.module()}.Integer || T=={s.big.module()}.Rational {{when Result && !Allow_Owned {{#panic("Contextless compile-time procedures cannot return allocated numbers; use fn or a contextful proc"+Site)}}}}')
        parts.append(f'else when Allow_Refs && {meta}.type_is_pointer(T) && T!=rawptr {{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site)}}')
        parts.append(f'else when Allow_Refs && {meta}.type_is_fixed_capacity_dynamic_array(T) {{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site)}}')
        parts.append(f'else when {meta}.type_is_dynamic_array(T) {{when Result && !Allow_Owned {{#panic("Contextless compile-time procedures cannot return dynamic arrays; use fn or a contextful proc"+Site)}} else when Result || Allow_Refs {{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site)}} else {{{native}.validate(T,false,Depth,Site)}}}}')
        parts.append(f'else when {meta}.type_is_struct(T) {{')
        def direct_field(field):
            # type_has_field includes promoted `using` members. A one-field
            # structural probe checks physical fields without counting aliases.
            quoted=json.dumps(field)
            return f'({meta}.type_has_shared_fields(T,struct{{{field}:{meta}.type_field_type(T,{quoted})}}) when {meta}.type_has_field(T,{quoted}) else false)'
        def count_fields(fields):
            if not fields:return '0'
            if len(fields)==1:return f'(1 when {direct_field(fields[0])} else 0)'
            middle=len(fields)//2
            return '('+count_fields(fields[:middle])+'+'+count_fields(fields[middle:])+')'
        # Keep checker recursion logarithmic even when SDK access inference has
        # inspected many record schemas. A left-associated sum can exhaust the
        # native compiler's stack before it can report any useful diagnostic.
        count=count_fields(names)
        parts.append(f'when {meta}.type_struct_field_count(T)!=({count}) {{#panic("Pure record fields could not be fully resolved"+Site)}}')
        raw_shapes=set()
        from .imports import file_private
        for unit in self.r.sources:
            for record in unit.nodes:
                if record.kind!='StructType' or record.data.get('op')!='raw_union':continue
                declaration=record.parent
                raw_name=declaration.group('names')[0].text if declaration and declaration.kind=='Declaration' and len(declaration.group('names'))==1 else None
                accessible=(raw_name and unit.scope(declaration).kind=='File' and not record.role('params')
                            and not(file_private(declaration) and unit is not s))
                reference=self.r.access(s,unit,raw_name) if accessible else None
                if reference and (unit.path.parent==s.path.parent or reference!=raw_name):
                    parts.append(f'when T=={reference} {{#panic("Raw-union storage cannot be proved pure"+Site)}}')
                else:
                    raw_shapes.add(tuple(n.text for f in record.role('fields').children if f.kind=='Field' for n in f.group('names')))
        for shape in sorted(raw_shapes):
            matches=' && '.join([f'{meta}.type_struct_field_count(T)=={len(shape)}']+
                                [direct_field(name) for name in shape])
            parts.append(f'when {matches} {{#panic("Raw-union storage cannot be proved pure: an inaccessible raw-union schema has this field layout"+Site)}}')
        for field in names:
            predicate=direct_field(field)
            field=json.dumps(field)
            parts.append(f'when {predicate} {{{name}({meta}.type_field_type(T,{field}),Result,Allow_Owned,Allow_Refs,Depth+1,Site)}}')
        parts.extend(['}',f'else when {meta}.type_is_array(T)||{meta}.type_is_enumerated_array(T)||{meta}.type_is_slice(T) {{',
                      f'when Result && !Allow_Owned && {meta}.type_is_slice(T) {{#panic("Contextless compile-time procedures cannot return strings/slices; use fn or a contextful proc"+Site)}}',
                      f'else {{{name}({meta}.type_elem_type(T),Result,Allow_Owned,Allow_Refs,Depth+1,Site)}}','}',
                      f'else when {meta}.type_is_union(T) {{',
                      f'{name}_union(T,Result,Allow_Owned,Allow_Refs,Depth,0,Site)','}',
                      f'else {{{native}.validate(T,Result && !Allow_Owned,Depth,Site)}}'])
        code=f'\n@(private="file")\n{name}::proc "contextless"($T:typeid,$Result:bool,$Allow_Owned:bool,$Allow_Refs:bool,$Depth:int,$Site:string){{\n'+ '\n'.join(parts)+'\n}\n'
        code+=f'\n@(private="file")\n{name}_union::proc "contextless"($T:typeid,$Result:bool,$Allow_Owned:bool,$Allow_Refs:bool,$Depth:int,$Index:int,$Site:string){{\nwhen Index<{meta}.type_union_variant_count(T){{{name}({meta}.type_variant_type_of(T,Index),Result,Allow_Owned,Allow_Refs,Depth+1,Site);{name}_union(T,Result,Allow_Owned,Allow_Refs,Depth,Index+1,Site)}}\n}}\n'
        s.expressions.helpers[key]=s.mapping.wrap(code,s.path,0,0,'pure-type-validator')
        return name

    def type_site(self,s,site,description):
        start,end=site.start,site.end
        if end==start:
            token=re.match(rb'\w+|.',s.raw[start:])
            if token:end=start+len(token[0])
        if not hasattr(s,'pure_type_checks'):s.pure_type_checks={}
        if not hasattr(s,'pure_type_tokens'):s.pure_type_tokens={}
        s.pure_type_checks[start,end]=description
        token=f' [{s.prefix}pure_type_site_{start}_{end}]'
        s.pure_type_tokens[token]=(start,end)
        return token,start,end

    def walk(self,proc):
        def visit(n):
            yield n
            for child in n.children:
                if child.kind!='Procedure':yield from visit(child)
        nodes=list(visit(proc.role('body'))) if proc.role('body') else []
        for field in proc.role('type').role('params').children:
            default=field.role('default')
            if default:nodes.extend(visit(default))
        return nodes

    def flow(self,n,env):
        if n is None:return Flow()
        if n.kind=='Ident':
            symbol=self.r.symbol(n)
            if symbol and symbol.projection:
                owner,name=symbol.projection
                return self.flow(owner,env).member(name)
            return env.setdefault((symbol.declaration,symbol.name),Flow()) if symbol else Flow()
        if n.kind in {'Paren','Cast','AutoCast','TypeAssertion','OrReturn','OrBranch','OrPanic'}:return self.flow(n.role('expr'),env)
        if n.kind=='FieldValue':return self.flow(n.role('value'),env)
        if n.kind=='Unary' and n.data['op']=='&':
            target=self.flow(n.role('expr'),env)
            return Flow(target=target,stack={target.owner} if target.owner else set(),kind='pointer')
        if n.kind=='Deref':return self.flow(n.role('expr'),env).deref()
        if n.kind=='Selector':
            base=n.role('expr');value=self.flow(base,env)
            if value.typ is None:value.typ=self.r.type_of(base)
            if self.r.type_of(base).kind=='pointer':value=value.deref()
            return value.member(n.role('field').text)
        if n.kind=='Index':return self.flow(n.role('expr'),env).item()
        if n.kind=='Comprehension':
            # The new outer buffer is owned; reference-containing elements
            # retain their input provenance because construction is shallow.
            value=self.flow(n.role('output'),env)
            if n.data.get('spread'):value=value.item()
            return Flow(element=self.copied(value,n.role('output')))
        if n.kind=='Slice':
            base=self.flow(n.role('expr'),env)
            result=Flow(base.borrowed,element=base.item(),stack=set(base.stack))
            if self.r.type_of(n.role('expr')).kind in {'array','fixed','generic','unknown'} and base.owner:result.stack.add(base.owner)
            return result
        if n.kind=='Compound':
            result=Flow()
            typ=self.context_type(n)
            if typ.kind in {'array','fixed','struct'}:result.owner=self.owner(n)
            if typ.kind=='slice' and n.group('elems') and self.owner(n):result.stack.add(self.owner(n))
            fields=[]
            if typ.kind=='struct' and typ.node:
                for field in typ.node.role('fields').children:
                    if field.kind=='Field':fields.extend(name.text for name in field.group('names'))
            for i,elem in enumerate(n.group('elems')):
                if elem.kind=='FieldValue':result.fields[elem.role('field').text]=self.flow(elem.role('value'),env)
                elif i<len(fields):result.fields[fields[i]]=self.flow(elem,env)
                else:
                    if result.element is None:result.element=Flow()
                    result.element.merge(self.flow(elem,env))
            # An addressed literal denotes one abstract storage site. Rebuilding
            # its identity on each fixed-point pass grows pointer-alias sets
            # forever, even in an inactive generic branch such as slice.swap.
            # Preserve its identity while joining any evolving member provenance.
            stable=env.setdefault((n,'<literal storage>'),result)
            if stable is not result:stable.merge(result)
            return stable
        if n.kind in {'Ternary','WhenExpr','OrElse'}:
            result=Flow();result.merge(self.flow(n.role('x'),env));result.merge(self.flow(n.role('y'),env));return result
        if n.kind == 'SwitchResults':
            result=Flow()
            for value in n.group('args'):result.merge(self.flow(value,env))
            return result
        if n.kind in {'SwitchExpr','CompileSwitchExpr'}:
            result=Flow()
            for case in n.data['cases']:result.merge(self.flow(n.source.at(case['result'],n),env))
            return result
        if n.kind=='Call':
            args=n.group('args')
            if n.source.denotes_type(n.role('callee')):
                return self.flow(args[0],env) if args else Flow()
            if self.builtin(n) in {'pop','pop_safe','pop_front','pop_front_safe'}:
                target=self.arguments(n,['array'])[0]
                if target:return self.flow(target,env).deref().item().header_copy(self.r.type_of(n),n)
            if self.library(n)==('core:slice','clone'):
                value=self.arguments(n,['a'])[0]
                return Flow(element=self.flow(value,env).item()) if value else Flow()
            return Flow() # checked callees promote independent result storage
        return Flow()

    def context_type(self,node):
        """Recover aggregate context without changing native literal coercion."""
        typ=self.r.type_of(node)
        if typ.kind not in {'unknown','generic'} or node.kind!='Compound' or node.role('type'):return typ
        child=node;parent=node.parent
        while parent and parent.kind=='Paren':child,parent=parent,parent.parent
        if not parent:return typ
        if parent.kind=='Declaration' and parent.role('type'):return self.r.type_node(parent.role('type'))
        if parent.kind=='Assignment' and child in parent.group('rhs'):
            index=parent.group('rhs').index(child);lhs=parent.group('lhs')
            if index<len(lhs):return self.r.type_of(lhs[index])
        aggregate=parent.parent if parent.kind=='FieldValue' else parent
        if aggregate and aggregate.kind=='Compound':
            record=self.context_type(aggregate)
            if parent.kind=='FieldValue':
                field=self.r.field(record,parent.role('field').text)
                if field:return field[0]
            elif record.kind=='struct' and record.node and child in aggregate.group('elems'):
                fields=self.r.fields(record.node.role('fields'));index=aggregate.group('elems').index(child)
                if index<len(fields):return self.r.type_node(fields[index][1],record.substitutions)
            elif record.elem:return record.elem
        if aggregate and aggregate.kind=='Call':
            plan=self.r.resolve_call(aggregate)
            if plan:
                fields=self.r.fields(plan.signature.role('params'))
                if parent.kind=='FieldValue':
                    annotation=next((annotation for name,annotation in fields if name.lstrip('$')==parent.role('field').text),None)
                else:
                    positional=[arg for arg in aggregate.group('args') if arg.kind!='FieldValue']
                    index=positional.index(child) if child in positional else len(fields)
                    annotation=fields[index][1] if index<len(fields) else None
                if annotation:return self.r.type_node(annotation,plan.substitutions)
        return typ

    def copied(self,value,n):
        typ=self.r.type_of(n)
        # Thor clones direct logical-number assignments. Aggregate copies keep
        # shallow member headers, so their borrowed fields must stay tainted.
        if n.source.big.enabled and typ.kind in {'big_integer','big_rational'}:return Flow()
        if n.source.big.enabled and typ.kind in {'generic','unknown'} and (n.source.big.fresh(n) or n.source.big.generic_reference(n)):
            # Direct generic numeric copies are independent. Ordinary user
            # fields can still contain borrowed reference storage; retain its
            # provenance and let native type checking distinguish the cases.
            result=value.header_copy(typ,n)
            result.numeric_copy=True
            return result
        if typ.kind=='array':return Flow(element=value.item())
        if typ.kind=='struct':
            result=Flow()
            fields=typ.node.role('fields') if typ.node else None
            for field in fields.children if fields else []:
                if field.kind=='Field':
                    for name in field.group('names'):result.fields[name.text]=value.member(name.text)
            return result
        return value

    def explicit_allocator(self,n):
        args=n.group('args');library=self.library(n)
        named=any(a.kind=='FieldValue' and a.role('field').text=='allocator' for a in args)
        plan=self.r.resolve_call(n)
        fields=self.r.fields(plan.signature.role('params')) if plan else []
        if named:
            annotation=next((typ for name,typ in fields if name=='allocator'),None)
            actual=self.r.type_node(annotation,plan.substitutions) if annotation else None
            return not(actual and actual.kind in {'scalar','enum','bitset'} and actual.name not in {'any','rawptr','typeid','cstring'})
        positional=[a for a in args if a.kind!='FieldValue']
        if library in self.libraries and fields:
            return any(name=='allocator' for name,_ in fields[:len(positional)])
        if library in {('core:strings','clone'),('core:slice','clone'),('thor:format','group_integer')}:
            return len(positional)>1
        if library and library[0]=='thor:format':
            limit=5 if library[1] in {'format_options','format_spec'} else 4
            return len(positional)>limit
        return False

    def borrowed_write(self,lhs,env):
        conditions=[]
        storage=self.storage(lhs,env)
        if storage:conditions.append(storage)
        if lhs.source.big.enabled and lhs.kind in {'Selector','Index','Deref'} and self.flow(lhs,env).borrowed:
            kind=self.r.type_of(lhs).kind
            borrowed=self.flow(lhs,env).borrowed
            if kind in {'big_integer','big_rational'}:conditions.append(borrowed)
            elif kind in {'unknown','generic'}:conditions.append(borrowed&Permission('type',('big',lhs)))
        return conditions

    def storage(self,node,env):
        if node.kind=='Paren':return self.storage(node.role('expr'),env)
        if node.kind=='Index':
            base=node.role('expr');value=self.flow(base,env);typ=self.r.type_of(base)
            if typ.kind in {'slice','dynamic','string','map'}:return value.borrowed
            if typ.kind=='pointer':return value.deref().location
            if typ.kind in {'generic','unknown'}:
                pointer=Permission('type',('pointer',base));sequence=Permission('type',('sequence',base))
                # Arrays are copied by value; slice/pointer backing storage is
                # shared. A generic instantiation supplies the distinction.
                return (pointer&value.deref().location)|(sequence&value.borrowed)
            return self.storage(base,env)
        if node.kind=='Slice':return self.flow(node.role('expr'),env).borrowed
        return self.flow(node,env).location

    def unsafe_storage(self,typ,seen=None):
        """Local annotations must not introduce raw pointers or any boxing.

        Inferred generics originate in natively validated parameters/types.
        Fresh local dynamic buffers are allowed, but maps/sets and reference
        representations cannot provide a back door around the call whitelist.
        """
        seen=set() if seen is None else seen
        key=typ.kind,typ.node
        if key in seen:return False
        seen.add(key)
        if typ.kind in {'procedure','map','set'} or typ.name in {'any','typeid','rawptr','cstring'}:return True
        if typ.elem and self.unsafe_storage(typ.elem,seen):return True
        if typ.kind=='struct' and typ.node:
            if typ.node.data.get('op')=='raw_union':return True
            for field in typ.node.role('fields').children:
                if field.kind=='Field' and self.unsafe_storage(self.r.type_node(field.role('type'),typ.substitutions),seen):return True
        if typ.kind=='union' and typ.node:
            return any(self.unsafe_storage(self.r.type_node(v,typ.substitutions),seen) for v in typ.node.group('variants'))
        return False

    def pointer_comparison(self,typ,seen=None):
        """Whether a value comparison can inspect a pointer representation."""
        seen=set() if seen is None else seen
        key=typ.kind,typ.node
        if key in seen:return None
        seen.add(key)
        if typ.kind=='pointer' or typ.name in {'rawptr','cstring'}:return True
        if typ.kind in {'unknown','generic'}:return None
        if typ.kind in {'big_integer','big_rational','string'}:return False
        children=[]
        if typ.elem and typ.kind in {'array','fixed'}:children.append(typ.elem)
        if typ.kind=='struct' and typ.node:
            children.extend(self.r.type_node(field.role('type'),typ.substitutions) for field in typ.node.role('fields').children if field.kind=='Field')
        if typ.kind=='union' and typ.node:children.extend(self.r.type_node(value,typ.substitutions) for value in typ.node.group('variants'))
        results=[self.pointer_comparison(child,seen.copy()) for child in children]
        return True if True in results else None if None in results else False

    def source_location_dependent(self,n,seen=None):
        if n is None:return False
        if n.kind in {'Procedure','ProcedureGroup'}:return False
        seen=set() if seen is None else seen
        if n in seen:return False
        seen.add(n)
        if n.kind=='Directive' and n.data['name'] in {'file','directory','line','caller_location','defined'}:return True
        if n.kind=='Ident' and n.text=='ODIN_BUILD_PROJECT_NAME' and self.reference(n) and self.r.symbol(n) is None:return True
        if self.imported(n)==('base:builtin','ODIN_BUILD_PROJECT_NAME'):return True
        if self.imported(n) in {('base:intrinsics',name) for name in self.source_identity_intrinsics}:return True
        symbol=self.r.symbol(n) if n.kind in {'Ident','Selector'} else None
        if symbol and symbol.declaration.kind=='Declaration' and not symbol.declaration.data.get('mutable'):
            if self.source_location_dependent(symbol.value,seen):return True
        return any(self.source_location_dependent(child,seen) for child in n.children)

    def analyze(self,proc):
        enabled=self.r.native_enabled
        if self.r.native:self.r.native_enabled=True
        try:return self._analyze(proc)
        finally:self.r.native_enabled=enabled

    def _analyze(self,proc):
        if proc in self.analyzed:return
        self.analyzed.add(proc)
        if self.pure(proc) and not proc.role('body'):
            proc.source.error('Declared pure functions require an inspectable body; use proc for foreign or bodyless declarations.',proc.start,proc.start+2)
        if self.io_procedure(proc):
            # Overload groups can select this target only at instantiation.
            # Keep its signature/laziness, with a failing checked body instead
            # of traversing or compiling the actual I/O implementation.
            self.contracts[proc]=(proc,'Pure code cannot call this library procedure because it performs I/O. Use proc for I/O and keep it outside fn/#run call chains.')
            return
        nodes=self.walk(proc);env=self.borrowing.environment(proc)
        for field in proc.role('type').role('params').children:
            if field.kind=='Field' and field.data.get('using_field') and self.r.type_node(field.role('type')).kind in {'generic','unknown'}:
                self.contracts[proc]=(field,'Pure using parameters require a resolved record schema; access members explicitly for unconstrained generic inputs.')
        cc=proc.role('type').data.get('calling_convention','')
        if self.pure(proc) and cc not in {'','odin'}:
            self.problem(proc,'Declared pure functions require the Odin calling convention.')
        declaration=proc.parent
        if declaration and declaration.kind=='Declaration':
            for attribute in declaration.group('attributes'):
                names=[elem.role('field').text if elem.kind=='FieldValue' else elem.text for elem in attribute.group('elems')]
                if any(name in {'init','fini'} for name in names):
                    self.problem(proc,'Package initializers/finalizers are not eligible pure call targets.')
                if any(name.startswith('deferred_') for name in names):
                    self.contracts[proc]=(attribute,'Pure calls cannot use implicit deferred procedure callbacks; make eligible calls explicit in the body.')
        for n in nodes:
            if n in self.borrowing.escapes:self.problem(n,self.borrowing.escapes[n])
            typ=self.r.type_node(n.role('type')) if n.kind=='Declaration' and n.role('type') else self.r.type_of(n) if n.kind in {'Compound','Comprehension'} else None
            if typ and typ.kind=='struct' and typ.node and typ.node.data.get('op')=='raw_union':
                self.problem(n,'Raw-union storage cannot be proved pure.')
            if n.kind in {'Compound','Comprehension'} or n.kind=='Declaration' and (n.data.get('mutable') or n.data.get('immutable')):
                local_types=[typ] if typ else [self.r.type_of(value) for value in n.group('values')]
                if any(t and self.unsafe_storage(t) for t in local_types):
                    self.problem(n,'Pure local storage cannot contain raw pointers, any, procedure values, maps, sets or raw unions.')
            if n.kind=='Using' or n.kind=='Declaration' and n.data.get('using_field'):
                owners=n.group('values' if n.kind=='Using' else 'names')
                if any(self.r.type_of(owner).kind in {'generic','unknown'} for owner in owners):
                    self.problem(n,'Pure using requires a resolved record schema; unverified namespaces and unconstrained generic projections are not eligible.')
            if n.kind=='Selector' or n.kind=='Ident' and self.reference(n):
                if ((n.kind=='Ident' and n.text=='ODIN_BUILD_PROJECT_NAME' and self.r.symbol(n) is None) or
                    self.imported(n)==('base:builtin','ODIN_BUILD_PROJECT_NAME')):
                    self.problem(n,'Source-location-dependent constants are not eligible for pure execution yet; pass ordinary data explicitly.')
                if any(a.kind=='Call' and self.builtin(a) in self.type_queries and a.role('callee') is not n for a in n.ancestors() if a is not proc):continue
                if n.kind=='Selector' and n.role('field').text in {'_value','_numerator','_denominator'}:
                    base=n.role('expr');typ=self.r.type_of(base)
                    while typ.kind=='pointer':typ=typ.elem
                    kind=typ.kind
                    if kind in {'big_integer','big_rational'}:self.problem(n,self.storage_message)
                    elif kind in {'generic','unknown'} and (n.source.big.enabled or
                            not getattr(n.source,'native_inspected',False) and any(unit.path==Path(__file__).resolve().parents[1]/'runtime'/'big'/'big.odin' for unit in self.r.sources)):
                        assignment=next((parent for parent in n.ancestors() if parent.kind=='Assignment' and any(
                            lhs.start<=n.start and n.end<=lhs.end for lhs in parent.group('lhs'))),None)
                        if assignment:self.storage_writes.setdefault(assignment,[]).append(base)
                        else:self.storage_reads[n]=base
                symbol=self.r.symbol(n)
                if symbol and symbol.declaration.kind=='Declaration' and self.source_location_dependent(symbol.value):
                    self.problem(n,'Source-location-dependent constants are not eligible for pure execution yet; pass ordinary data explicitly.')
                if symbol and not(proc.start<=symbol.declaration.start<proc.end and symbol.declaration.source is proc.source):
                    if symbol.declaration.data.get('mutable',False) or symbol.immutable:
                        self.problem(n,f"Pure code cannot read or write runtime storage '{symbol.name}' outside the function.")
                imported=self.imported(n)
                if imported and symbol is None and n.source.import_dir(imported[0]) is None and imported not in self.native_constants:
                    call=n.parent
                    if not(call and call.kind=='Call' and call.role('callee') is n and
                           (self.io_call(call) or self.approved(call) or self.builtin(call) or self.targets(call))):
                        self.problem(n,'Pure code cannot read unverified native package storage or values; only approved constants/helpers are eligible.')
            if n.kind=='Implicit':self.problem(n,'Pure code cannot access the ambient context or implicit runtime state.')
            if n.kind=='Unsafe':self.problem(n,'Assembly, uninitialized storage and unsafe operations are not permitted in pure code.')
            operands=[]
            if n.kind=='Binary' and n.data['op'] in {'==','!=','<','<=','>','>=','in','not_in'}:
                operands=[n.role('left'),n.role('right')]
                if n.data['op'] in {'in','not_in'}:operands=operands[:1]
                if any(value.text=='nil' for value in operands):operands=[]
            elif n.kind=='Call' and self.builtin(n) in {'min','max','clamp'}:
                operands=[arg.role('value') if arg.kind=='FieldValue' else arg.role('expr') if arg.kind=='Unary' and arg.data['op']=='**' else arg for arg in n.group('args')]
            elif n.kind in {'Cast','Call'} and not(n.kind=='Cast' and n.data['op']=='transmute'):
                target=n.role('type') if n.kind=='Cast' else n.role('callee')
                if target and n.source.denotes_type(target):
                    value=n.role('expr') if n.kind=='Cast' else next(iter(n.group('args')),None)
                    if value:operands=[value]
            if operands and not self.calls.queried(n) and not n.source.denotes_type(n) and not any(value.source.denotes_type(value) for value in operands):
                shapes=[self.pointer_comparison(self.r.type_of(value)) for value in operands]
                message='Pure code cannot observe pointer identities or convert pointers to numeric values.'
                if True in shapes:self.problem(n,message)
                elif None in shapes:
                    # Enum shorthand obtains its type from the other operand;
                    # it cannot itself contain/observe a pointer representation.
                    self.pointer_checks[n]=[value for value in operands if value.kind!='ImplicitSelector' and value.text!='nil']
            if n.kind in {'Switch','SwitchExpr','CompileSwitchExpr'}:
                selector=n.role('selector')
                if selector is None and n.kind=='Switch':selector=next((child for child in reversed(n.children) if child.kind not in {'Block','Declaration'}),None)
                if selector:
                    if n.kind=='Switch':
                        cases=[child for block in n.children if block.kind=='Block' for child in block.children if child.kind=='Case']
                        matches=[child for case in cases for child in case.children if child.end<=case.data.get('roles',{}).get('colon',[0])[0]]
                    else:matches=[n.source.at(span,n) for case in n.data['cases'] for span in case['matches']]
                    if any(match.text!='nil' for match in matches):
                        kind=self.pointer_comparison(self.r.type_of(selector))
                        if kind is True:self.problem(selector,'Pure code cannot observe pointer identities in switch selection.')
                        elif kind is None:self.pointer_checks[selector]=[selector]
            if n.kind=='OrReturn' and self.wraps(proc):
                results=self.r.fields(proc.role('type').role('results'))
                if len(results)>1 and any(not name for name,_ in results):
                    self.problem(n,'or_return in a procedure with multiple results requires named return values.')
            if n.kind=='Cast' and n.data['op']=='transmute':
                allowed=self.scalar_transmute(n)
                message='Pure transmute requires scalar numeric, boolean, enum or bit-set values; pointer and aggregate representations are not eligible.'
                if allowed is False:self.problem(n,message)
                elif allowed is None:
                    self.scalar_casts.add(n)
            if n.kind=='Directive':
                if n.data['name'] in {'file','line','directory','caller_location','defined'}:
                    self.problem(n,'Source-location-dependent constants are not eligible for pure execution yet; pass ordinary data explicitly.')
                elif n.data['name'] not in {'assert','config','type','procedure'}:
                    self.problem(n,f"Directive '#{n.data['name']}' is not permitted in pure code.")
            if n.kind=='Call':
                builtin=self.builtin(n);library=self.library(n)
                if self.calls.queried(n):continue
                if self.io_call(n):
                    self.problem(n.role('callee'),self.io_message(n))
                elif builtin=='make':
                    args=n.group('args')
                    if not args or n.source.resolver.type_node(args[0]).kind not in {'slice','dynamic'}:
                        self.problem(n,'Pure allocation supports fresh slice/dynamic buffers only.')
                    elif len(args)>(3 if n.source.resolver.type_node(args[0]).kind=='dynamic' else 2) or any(a.kind=='FieldValue' and a.role('field').text=='allocator' for a in args):
                        self.problem(n,'Pure code cannot select an allocator or invoke allocator callbacks.')
                elif builtin in {'copy','delete'}:
                    if n.group('args'):
                        target=self.arguments(n,['dst'])[0] if builtin=='copy' else next(
                            (a.role('value') for a in n.group('args') if a.kind=='FieldValue' and a.role('field').text in {'array','str','m'}),n.group('args')[0])
                        provenance=self.flow(target,env)
                        if provenance.borrowed:
                            condition=provenance.borrowed
                            if provenance.numeric_copy:condition&=Permission('type',('not-big',target))
                            self.problem(n,'Pure code cannot mutate or delete storage borrowed from an input.',
                                         [condition])
                        if builtin=='delete':
                            positional=[arg for arg in n.group('args') if arg.kind!='FieldValue']
                            allocator=any(arg.kind=='FieldValue' and arg.role('field').text=='allocator' for arg in n.group('args'))
                            allocator |= len(positional)>1 and self.r.type_of(target).kind not in {'dynamic','map'}
                            if allocator:self.problem(n,'Pure code cannot select an allocator.')
                elif builtin in self.sequence_mutators:
                    # These operations change dynamic/fixed-capacity headers.
                    # Public pure inputs cannot contain either header type, so
                    # reachable headers are local. Borrowed element references
                    # are still propagated by Borrowing and protected on writes.
                    pass
                elif builtin or n.source.denotes_type(n.role('callee')):pass
                elif self.approved(n):
                    if self.explicit_allocator(n):
                        self.problem(n,'Pure code cannot select an allocator.')
                else:
                    targets=self.targets(n)
                    message=self.call_error(n) if not targets else None
                    if message:self.problem(n.role('callee'),message)
            # Odin represents a type-switch binding as an assignment with `in`.
            # It declares the alias; only writes in its case bodies are effects.
            if n.kind=='IncDec' or n.kind=='Assignment' and n.data['op']!='in':
                for lhs in n.group('lhs') if n.kind=='Assignment' else [n.role('expr')]:
                    checks=self.borrowed_write(lhs,env)
                    if checks:self.problem(n,'Pure code cannot mutate storage borrowed from an input.',checks)
            if n.kind=='Cast' or n.kind=='Call' and n.source.denotes_type(n.role('callee')):
                target=n.role('type') if n.kind=='Cast' else n.role('callee')
                typ=self.r.type_node(target)
                if typ.kind in {'pointer','procedure'} or typ.name in {'rawptr','cstring','any','typeid'}:
                    self.problem(n,'Pure code cannot convert values to pointers, procedure values, any or runtime type identities.')
        # Keep defaults syntactically valid. Replacing an effectful default
        # with an error expression corrupts inference and reports missing
        # parameters before the actual purity violation. Fail lazily in the
        # checked body, mapped to the original offending default expression.
        for field in proc.role('type').role('params').children:
            default=field.role('default')
            failures=[(node,message) for node,message in self.problems.items()
                      if default and node.source is default.source and default.start<=node.start<default.end]
            if failures:
                self.invalid_defaults.add(default)
                self.contracts.setdefault(proc,min(failures,key=lambda item:item[0].start))

    def predicate(self,checks,source):
        pieces=[]
        for check in checks:
            if isinstance(check,Permission):pieces.append(check.render(self,source))
            elif isinstance(check,str):pieces.append(check)
            elif isinstance(check,tuple):
                typ='type_of('+source.render(check[1])+')';big=source.big.module()
                predicate=f'({typ}=={big}.Integer || {typ}=={big}.Rational)'
                pieces.append('!'+predicate if check[0]=='not-big' else predicate)
            else:
                source.needs_intrinsics=True
                t=f'type_of({source.render(check)})';meta=source.prefix+'meta'
                pieces.append('('+' || '.join(f'{meta}.type_is_{kind}({t})' for kind in ['slice','string','dynamic_array','map','pointer','multi_pointer'])+')')
        return ' || '.join(pieces) or 'false'

    def validate_storage(self,source,value):
        source.needs_intrinsics=True;meta=source.prefix+'meta';big=source.big.module()
        name=source.prefix+'pure_big_storage_'+str(value.start);key=source.root,name
        code=(f'\n@(private="file")\n{name}::proc "contextless"($T:typeid){{'
              f'when {meta}.type_is_pointer(T){{{name}({meta}.type_elem_type(T))}}'
              f'else when T=={big}.Integer || T=={big}.Rational{{#panic({json.dumps(self.storage_message)})}}}}\n')
        source.expressions.helpers[key]=source.mapping.wrap(code,source.path,value.start,value.end,'pure-storage-type')
        return f'{name}(type_of({source.render(value)}))'

    def result_flow(self,n,index,env):
        if n.kind=='Paren':return self.result_flow(n.role('expr'),index,env)
        if n.kind=='SwitchResults':
            args=n.group('args')
            return self.flow(args[index],env) if index<len(args) else Flow()
        if n.kind in {'SwitchExpr','CompileSwitchExpr'}:
            result=Flow()
            for case in n.data['cases']:result.merge(self.result_flow(n.source.at(case['result'],n),index,env))
            return result
        return self.flow(n,env) if index==0 else Flow()

    def render(self,n,expected,constant):
        proc=n if n.kind=='Procedure' else self.owner(n)
        if n.kind=='Run':
            return n.source.expressions.error(n,'#run requires compile-time-known arguments and types at this source location.')
        if not self.checked(proc):return None
        self.analyze(proc)
        if self.invalid_default(n):return None
        if n in self.rendering:return None
        checked_body=self.calls.body(n,expected,constant)
        if checked_body is not None:return checked_body
        if n is proc.role('body') and proc in self.contracts:
            site,message=self.contracts[proc]
            return n.source.mapping.wrap('{#panic('+json.dumps(message)+')}',site.source.path,site.start,site.end,'pure-contract')
        if n.kind=='Directive' and n.data['name']=='procedure' and proc in self.inner_bodies:
            original=self.calls.procedure_name(n)
            if original is not None:return original
            return n.source.prefix+'procedure_name_'+str(proc.start)
        if n.kind=='Directive' and n.data['name']=='procedure':
            original=self.calls.procedure_name(n)
            if original is not None:return original
        if (n.kind=='Ident' and n.text=='_' and n.parent and n.parent.kind=='Field'
                and n.parent.parent is proc.role('type').role('params') and n in n.parent.group('names')):
            return self.parameter_name(n)
        message=self.problems.get(n)
        if message:
            if n.kind in {'Range','TypeSwitch'}:
                return '#panic('+json.dumps(message)+')\n'
            if n.kind=='Declaration' and not(n.parent and n.parent.role('init') is n):
                added=set(descendants(n))-self.rendering
                self.rendering.update(added)
                try:code=n.source._render(n,expected,constant)
                finally:self.rendering.difference_update(added)
                return code+'\n#panic('+json.dumps(message)+')\n'
            return n.source.expressions.error(n,message)
        checked_callee=self.calls.callee(n)
        if checked_callee is not None:return checked_callee
        checked_params=self.calls.parameters(n)
        if checked_params is not None:return checked_params
        queried_type=self.calls.query(n)
        if queried_type is not None:return queried_type
        if n in self.pointer_checks:
            self.rendering.add(n)
            try:
                source=n.source;source.needs_default=True
                def body(_):
                    checks=[]
                    for value in self.pointer_checks[n]:
                        token,_,_=self.type_site(source,value,'pointer-free comparison/conversion operand')
                        checks.append(f'{self.validator(source)}(type_of({source.prefix}default_value({value.source.render(value)})),false,true,false,0,{json.dumps(token)})')
                    return '\n'.join([*checks,'return '+source._render(n,expected,constant)])
                result='bool' if n.kind=='Binary' and n.data['op'] in {'==','!=','<','<=','>','>=','in','not_in'} else None
                name,args=source.expressions.closed(n,body,result=result,flavor='pure_pointer_identity')
                if result is None:args.append(source.expressions.ownership.native_type(n))
                return name+'('+', '.join(args)+')'
            finally:self.rendering.remove(n)
        if n in self.storage_reads:
            self.rendering.add(n)
            try:
                source=n.source
                def body(_):
                    return self.validate_storage(source,self.storage_reads[n])+'\nreturn '+source._render(n,expected,constant)
                name,args=source.expressions.closed(n,body,result=None,flavor='pure_storage')
                args.append(source.expressions.ownership.native_type(n))
                return name+'('+', '.join(args)+')'
            finally:self.rendering.remove(n)
        if n in self.storage_writes:
            self.rendering.add(n)
            try:
                source=n.source
                def body(_):
                    checks=[self.validate_storage(source,value) for value in self.storage_writes[n]]
                    return '\n'.join([*checks,source._render(n,expected,constant)])
                name,args=source.expressions.closed(n,body,result=False,flavor='pure_storage_write')
                return name+'('+', '.join(args)+')'
            finally:self.rendering.remove(n)
        if (n.kind=='ExpressionStatement' and n.role('expr').kind=='Call' and self.builtin(n.role('expr'))=='panic'
                and n.parent and n.parent.kind=='Block'):
            # Odin rejects a defer followed directly by a diverging call. The
            # compiler's scratch/promotion defers must not invalidate user panic.
            # A nested statement scope retains divergence and all user lifetimes.
            self.rendering.add(n)
            try:
                if n.source.raw[n.parent.start:n.parent.start+1]!=b'{':n.source.expressions.convert_do(n.parent)
                return '{'+n.source._render(n,expected,constant)+'}'
            finally:self.rendering.remove(n)
        if n in self.scalar_casts:
            self.rendering.add(n)
            try:
                source=n.source;source.needs_intrinsics=True;meta=source.prefix+'meta'
                def body(_):
                    def scalar(t):return '('+' || '.join(f'{meta}.type_is_{kind}({t})' for kind in ['numeric','boolean','enum','bit_set'])+')'
                    condition=f'{scalar(source.render(n.role("type")))} && {scalar("type_of("+source.render(n.role("expr"))+")")}'
                    return f'when {condition} {{return '+source._render(n,expected,constant)+'} else {#panic("Pure transmute requires scalar values; pointer and aggregate representations are not eligible.")}'
                name,args=source.expressions.closed(n,body,result=None,flavor='pure_transmute')
                args.append(source.render(n.role('type')))
                return name+'('+', '.join(args)+')'
            finally:self.rendering.remove(n)
        if n.kind=='Return' and proc in self.inner_bodies:
            selected=n.source.expressions.switches.statement(n,constant)
            if selected is not None:return selected
            s=n.source;results=self.r.fields(proc.role('type').role('results'))
            if n.parent and n.parent.kind == 'Block' and s.raw[n.parent.start:n.parent.start+1] != b'{':
                # Promotion expands a single return into a block. Odin's `do`
                # syntax accepts a normal statement, so remove that keyword
                # just as expression-helper lowering does for synthetic blocks.
                s.expressions.convert_do(n.parent)
            named=self.result_names(proc)
            values_=n.group('values')
            from .switches import unwrap
            if len(values_)==1 and unwrap(values_[0]).kind in {'SwitchExpr','CompileSwitchExpr'}:
                rhs=s.render(values_[0],expected=tuple(s.type_hint(t) or Hint(s,t) for _,t in results))
            else:
                rhs=','.join(s.big.owned(value,hint=s.type_hint(typ) or Hint(s,typ))
                             for value,(_,typ) in zip(values_,results)) if values_ else ','.join(named)
            # Preserve native arity errors, including a single multi-result call.
            if len(values_)>len(results):rhs+=','+','.join(s.render(v) for v in values_[len(results):])
            assignment=','.join(named)+'='+rhs+';' if values_ else ''
            # The native return is ignored. A final defer promotes the named
            # results while locals live. Explicit casts acknowledge those
            # intentionally temporary stack views to Odin's escape checker.
            code='{'+assignment+'return '+','.join(f'transmute(type_of({name})){name}' for name in named)+'}'
            return s.mapping.wrap(code,s.path,n.start,n.end,'pure-return')
        if n is proc.role('body') and proc not in self.inner_bodies and self.wraps(proc):
            return self.wrapper(proc,n)
        if n.kind=='Call' and self.approved(n) and self.library(n) and self.library(n)[0]=='thor:format' and n not in self.rendering:
            self.rendering.add(n)
            try:
                source=n.source
                args=n.group('args')
                value=next((a.role('value') for a in args if a.kind=='FieldValue' and a.role('field').text=='value'),args[0] if args else None)
                if value:
                    def body(_):
                        return f'{source.expressions.module("format")}.validate_pure_value(type_of({source.render(value)}))\nreturn '+source._render(n,expected,constant)
                    name,args=source.expressions.closed(n,body,result='string',flavor='pure_format')
                    return f'{name}({", ".join(args)})'
            finally:self.rendering.remove(n)
        conditions=self.conditions.get(n)
        if conditions:
            self.rendering.add(n)
            try:
                def body(_):
                    code=n.source._render(n,expected,constant)
                    for checks,message in conditions:
                        predicate=self.predicate(checks,n.source)
                        code=f'when {predicate} {{#panic({json.dumps(message)})}} else {{\n{code}\n}}'
                    return code
                post=n.parent and n.parent.kind=='For' and n.parent.role('post') is n
                if post:
                    name,args=n.source.expressions.closed(n,lambda _:body(None)+'\nreturn true',result='bool',flavor='pure_write')
                    return f'_ = {name}({", ".join(args)})'
                if any(not isinstance(check,str) for checks,_ in conditions for check in checks):
                    # Odin collects when branches before local runtime
                    # declarations. Put type-dependent guards in an explicitly
                    # captured helper, where the inspected value is a parameter.
                    name,args=n.source.expressions.closed(n,body,result=False,flavor='pure_write')
                    return f'{name}({", ".join(args)})'
                return body(None)
            finally:self.rendering.remove(n)
        return None

    @staticmethod
    def parameter_name(name):
        return name.source.prefix+'unused_'+str(name.start) if name.text=='_' else name.text.lstrip('$')

    def owned_type(self,typ,seen=None):
        seen=set() if seen is None else seen
        key=typ.kind,typ.node
        if key in seen:return True
        seen.add(key)
        if typ.kind in {'string','slice','dynamic','generic','unknown','big_integer','big_rational'}:return True
        if typ.elem:return self.owned_type(typ.elem,seen)
        if typ.kind=='struct' and typ.node:
            return any(self.owned_type(self.r.type_node(f.role('type'),typ.substitutions),seen.copy())
                       for f in typ.node.role('fields').children if f.kind=='Field')
        if typ.kind=='union' and typ.node:
            return any(self.owned_type(self.r.type_node(v,typ.substitutions),seen.copy()) for v in typ.node.group('variants'))
        return False

    def wraps(self,proc):
        return self.contextual(proc) and any(
            self.owned_type(self.r.type_node(typ)) for _,typ in self.r.fields(proc.role('type').role('results')) if typ)

    def result_names(self,proc):
        return [name if name and name!='_' else proc.source.prefix+'result_'+str(proc.start)+'_'+str(i)
                for i,(name,_) in enumerate(self.r.fields(proc.role('type').role('results')))]

    def wrapper(self,proc,block):
        s=block.source;prefix=s.prefix+'scratch_'+str(proc.start)
        signature=proc.role('type');params=signature.role('params')
        name=s.prefix+'body_'+str(proc.start)
        hidden=s.prefix+'procedure_name_'+str(proc.start)
        results=self.r.fields(signature.role('results'))
        if any(not name or name=='_' for name,_ in results):
            for node in self.walk(proc):
                if node.kind=='Return' and not node.group('values'):
                    self.problem(node,'Pure procedure results are unnamed; return values explicitly.')
        outputs=[s.prefix+'output_'+str(proc.start)+'_'+str(i) for i in range(len(results))]
        extra=','.join(f'{slot}:^${slot}_T' for slot in outputs)+','+hidden+':string,'+prefix+'_caller:$'+prefix+'_Allocator'
        parameter_code=s.splice(params,[(c.start,c.end,s.render(c)) for c in params.children],
                                [(params.start,extra+(',' if self.r.fields(params) else ''))])
        returns='->('+','.join(name+':'+s.render(typ,constant=True) for name,(_,typ) in zip(self.result_names(proc),results))+')'
        header=s.splice(signature,[(params.start,params.end,parameter_code),(params.end+1,signature.end,returns)],
                        [(params.start,extra)] if params.start==params.end else [])
        clauses=proc.group('where')
        if clauses:header+=' where '+', '.join(s.render(c,constant=True) for c in clauses)
        self.inner_bodies.add(proc)
        try:body=s._render(block)
        finally:self.inner_bodies.remove(proc)
        names=[s.prefix+'returned_'+str(proc.start)+'_'+str(i) for i in range(len(results))]
        args=['&'+value for value in names]+['#procedure',prefix+'_caller']
        after_variadic=False
        for field in params.children:
            if field.kind!='Field':continue
            for param in field.group('names'):
                arg=self.parameter_name(param)
                if field.data.get('variadic'):
                    arg='..'+arg
                    after_variadic=True
                elif after_variadic:
                    arg=self.parameter_name(param)+'='+arg
                args.append(arg)
        code=self.prologue(block)+f'\n{name}::{header} {body}\n'
        code+='\n'.join(f'{value}:{s.render(typ,constant=True)}' for value,(_,typ) in zip(names,results))+'\n'
        code+=f'{name}('+','.join(args)+')\n'
        code+='return '+','.join(names)+'\n'
        return '{'+code+'}'

    def promote_results(self,proc):
        s=proc.source;prefix=s.prefix+'scratch_'+str(proc.start)
        return ';'.join(s.prefix+'output_'+str(proc.start)+'_'+str(i)+'^='+self.module(s)+'.clone('+name+','+prefix+'_caller)'
                        for i,name in enumerate(self.result_names(proc)))+';'

    def prologue(self,block):
        proc=block.parent
        if not(proc and proc.kind=='Procedure' and proc.role('body') is block and self.checked(proc)):return ''
        if proc in self.inner_bodies:
            return 'defer {'+self.promote_results(proc)+'}\n'
        self.analyze(proc)
        s=block.source;module=self.module(s);prefix=s.prefix+'scratch_'+str(proc.start)
        code=[]
        declaration=proc.parent
        while declaration and declaration.kind=='Paren':declaration=declaration.parent
        names=declaration.group('names') if declaration and declaration.kind=='Declaration' else []
        function=names[0].text if names else 'anonymous procedure'
        def validate(site,expression,role):
            token,start,end=self.type_site(s,site,f"{role} in pure call '{function}'")
            return s.mapping.wrap(expression[:-1]+','+json.dumps(token)+')',s.path,start,end,'pure-type-check')
        for field in proc.role('type').role('params').children:
            if field.kind!='Field':continue
            for name in field.group('names'):
                if name.kind=='PolyType':
                    annotation=field.role('type')
                    if annotation and (annotation.kind=='TypeidType' or annotation.text=='typeid'):
                        code.append(validate(name,f'{self.validator(s)}({name.text.lstrip("$")},false,true,{str(self.borrowing.internal(proc)).lower()},0)',f"type parameter '{name.text}'"))
                    continue
                code.append(validate(field.role('type') or name,f'{self.validator(s)}(type_of({self.parameter_name(name)}),false,true,{str(self.borrowing.internal(proc)).lower()},0)',f"parameter '{name.text}'"))
        for index,(name,typ) in enumerate(self.r.fields(proc.role('type').role('results'))):
            if typ:code.append(validate(typ,f'{self.validator(s)}({"typeid" if typ.kind=="TypeidType" else self.r.template(typ)},true,{str(self.contextual(proc)).lower()},false,0)',f"result '{name}'" if name else f'result {index+1}'))
        if not self.contextual(proc):
            return s.mapping.wrap('\n'+'\n'.join(code)+'\n',s.path,proc.start,proc.role('type').end,'pure-contract')
        if self.wraps(proc):code.append(f'{prefix}_caller:=context.allocator')
        if self.borrowing.internal(proc) and proc in self.borrowing.mutates:
            # Out parameters can receive buffers allocated by a helper. Keep
            # those buffers in the enclosing invocation's scratch lifetime.
            return s.mapping.wrap('\n'+'\n'.join(code)+'\n',s.path,proc.start,proc.role('type').end,'pure-contract')
        code += [f'{prefix}: {module}.Scratch',f'{module}.scratch_init(&{prefix})',
                 f'defer {module}.scratch_destroy(&{prefix})',
                 f'context.allocator = {module}.scratch_allocator(&{prefix})',
                 'context.temp_allocator = context.allocator']
        return s.mapping.wrap('\n'+'\n'.join(code)+'\n',s.path,proc.start,proc.role('type').end,'pure-contract')

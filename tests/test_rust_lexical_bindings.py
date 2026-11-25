"""Resolver binding environments agree with Python and restore lexical state."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from thorlang.driver import Project,frontend_binary
from thorlang.evaluation import Evaluator
from thorlang.semantics import Type
from thorlang.specialization import Binding,Unresolved,type_code
from tests.native_paths import rust_binary
from tests import test_rust_compiler as native


class RustLexicalBindingTests(unittest.TestCase):
    maxDiff=None

    @classmethod
    def setUpClass(cls):
        build=subprocess.run(['cargo','build','--release','--locked','--offline','--manifest-path',str(native.ROOT/'rust/Cargo.toml'),
                              '--example','lexical_protocol'],capture_output=True,text=True,timeout=600)
        if build.returncode:
            raise RuntimeError(build.stdout+build.stderr)
        cls.binary=rust_binary('lexical_protocol', example=True)
        cls.frontend=frontend_binary('odin')[0]

    def compare(self,body,*,annotation='int',constant=None,prefix=''):
        constant={'kind':'integer','value':'7'} if constant is None else constant
        with tempfile.TemporaryDirectory(prefix='thor-lexical-') as directory:
            source=Path(directory).resolve()/'main.thor'
            source.write_text('package main\n'+prefix+'\ngeneric::proc($T:typeid,$N:'+annotation+'){'+body+'}\nmain::proc(){}')
            project=Project(source,self.frontend,['-debug'],cache_frontend=False)
            r=project.resolver
            # The evaluator supplies the recursive constant-integer callback
            # used by the real generic scheduling path.
            evaluator=Evaluator(project,'odin')
            state=evaluator.specializations
            proc=next(node for node in project.sources[source].nodes if node.kind=='Procedure' and node.parent
                      and any(name.text=='generic' for name in node.parent.group('names')))
            bindings={}
            for field in proc.role('type').role('params').children:
                for name in field.group('names'):
                    text=name.text.lstrip('$')
                    symbol=r.by_decl[field,text]
                    bindings[field,text]=Binding(symbol,r.type_node(field.role('type')) if text=='N' else Type('scalar','int'),constant if text=='N' else None)

            captured={}
            def packet(types=None):
                out={}
                for node in project.sources[source].nodes:
                    if node.kind!='Call' or node.role('callee').text not in {'query','query_bool'}:
                        continue
                    label,arg=node.group('args')
                    key=json.loads(label.text)
                    typ=types[key] if types is not None else r.type_node(arg) if arg.source.denotes_type(arg) else r.type_of(arg)
                    if types is None:
                        captured[key]=typ
                    arg.source.evaluation_context=arg
                    def code(typ):
                        try:
                            return type_code(typ,arg.source,r)
                        except Unresolved:
                            return None
                    fields={}
                    if typ.kind=='struct':
                        names=typ.members or dict(r.fields(typ.node.role('fields')))
                        fields={name:code(r.field(typ,name)[0]) for name in names}
                    integer=r.constant_int(arg)
                    boolean=evaluator.boolean(arg) if node.role('callee').text=='query_bool' and evaluator.known(arg) else None
                    out[key]={'type':code(typ),'integer':str(integer) if integer is not None else None,'fields':fields,'boolean':boolean}
                return out
            before=packet()
            with state.using(bindings):
                bound=packet()
                held_types=dict(captured)
                with state.using({}):
                    cleared=packet()
                    held=packet(held_types)
                restored=packet()
            result=subprocess.run([str(self.binary),str(source),json.dumps(constant)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),{'before':before,'bound':bound,'cleared':cleared,'restored':restored,'held':held})

    def test_bound_types_constants_and_repeated_queries(self):
        self.compare('query("type",T);query("constant",N);query("arithmetic",N+1);query("array",[N]T)')

    def test_shadowing_in_a_nested_block_does_not_use_the_outer_binding(self):
        self.compare('query("outerT",T);query("outerN",N);{T::bool;N::11;query("innerT",T);query("innerN",N)}')

    def test_nested_generic_parameter_has_its_own_identity(self):
        self.compare('query("outer",T);inner::proc($T:typeid){query("innerT",T);query("capturedN",N)}')

    def test_local_record_members_capture_the_bound_dimension_and_type(self):
        self.compare('Row::struct{values:[N]T};query("record",Row)')

    def test_boolean_constant_binding_is_not_confused_with_a_type(self):
        self.compare('query("value",N);query_bool("comparison",N==true)',annotation='bool',constant={'kind':'bool','value':True})

    def test_string_constant_binding_preserves_utf8_contents(self):
        self.compare('query("value",N);query_bool("comparison",N=="héllo 😀")',annotation='string',
                     constant={'kind':'string','bytes':'héllo 😀'.encode().hex()})

    def test_enum_constant_binding_keeps_its_enum_identity(self):
        self.compare('query("value",N);query_bool("comparison",N==.second)',annotation='Kind',prefix='Kind::enum{first,second}',
                     constant={'kind':'integer','value':'1'})

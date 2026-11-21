"""Existing callback-default fixtures exercise the native artifact directly."""
from unittest.mock import patch
from tests import test_thor as base
from tests import test_defaults as defaults
from tests import test_rust_compiler as native


class RustDefaultFixtureTests(defaults.DefaultCallbackTests):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)

    def invoke(self, *args, **kwargs):
        with patch.object(base, 'THOR', native.RUST):
            return base.ThorTests.invoke(self, *args, **kwargs)

    def test_fn_defaults_remain_replaceable_runtime_callbacks(self):
        code=self.program('''values:=[7,3]
if choose(values)!=3||choose(values,greater)!=7{panic("replaceable fn default")}
''','''choose::proc(data:[2]$E,less:=fn(a,b:E)->bool{return a<b})->E{return less(data[0],data[1])?data[0]:data[1]}
greater::fn(a,b:int)->bool{return a>b}
''')
        for flags in ([],native.UNCHECKED):
            with self.subTest(flags=flags):self.invoke(code,flags=flags,expected='')

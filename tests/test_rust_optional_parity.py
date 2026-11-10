"""Run the unchanged optional-result acceptance fixtures with the native engine.

Keep the original output layout so artifact/source-map assertions inspect the
native executable, not a reference executable from a differential preflight.
"""
from unittest.mock import patch
from tests import test_thor as base
from tests import test_rust_compiler as native
from tests import test_or_panic as optional


class RustOptionalFixtureTests(optional.OrPanicTests):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)

    def invoke(self, *args, **kwargs):
        with patch.object(base, 'THOR', native.RUST):
            return base.ThorTests.invoke(self, *args, **kwargs)

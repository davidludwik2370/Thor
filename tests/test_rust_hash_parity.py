"""Hash fixtures also test contextual literals crossing native any parameters."""
from unittest.mock import patch
from tests import test_thor as base
from tests import test_hash as reference
from tests import test_rust_compiler as native


class RustHashFixtureTests(reference.HashTests):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)

    def invoke(self, *args, **kwargs):
        with patch.object(base, 'THOR', native.RUST):
            return base.ThorTests.invoke(self, *args, **kwargs)

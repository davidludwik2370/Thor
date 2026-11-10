"""Original pure-I/O fixtures exercise native aliases, selection and lazy bodies."""
from unittest.mock import patch
from tests import test_thor as base
from tests import test_pure_io as reference
from tests import test_rust_compiler as native


class RustPureIOFixtureTests(reference.PureIOTests):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)

    def invoke(self, *args, **kwargs):
        with patch.object(base, 'THOR', native.RUST):
            return base.ThorTests.invoke(self, *args, **kwargs)

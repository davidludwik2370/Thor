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

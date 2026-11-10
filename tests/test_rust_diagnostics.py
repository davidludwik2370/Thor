"""Run the original diagnostic fixtures against the native compiler."""
from tests import test_diagnostic_hardening as hardening
from tests import test_diagnostics as reference
from tests import test_pure_call_context as purity
from tests import test_rust_compiler as native


class RustDiagnosticFixtureTests(reference.DiagnosticTests):
    compiler = native.RUST
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)


class RustDiagnosticHardeningFixtureTests(hardening.DiagnosticHardeningTests):
    compiler = native.RUST
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)


class RustPureCallContextFixtureTests(purity.PureCallContextTests):
    compiler = native.RUST
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)

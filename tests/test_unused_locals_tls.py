"""Live unused-local warnings over real TLS, including unsaved buffers."""
import unittest

from tests import test_tls as base


class UnusedLocalTLSTests(unittest.TestCase):
    setUpClass = classmethod(base.TLSTests.setUpClass.__func__)
    open = base.TLSTests.open
    position = staticmethod(base.TLSTests.position)

    def test_warning_severity_exact_range_and_unsaved_correction(self):
        text = 'package main\nmain::proc(){/* 😀 */if true do unused:=1}\n'
        client, path = self.open(text)
        errors = client.diagnostic(path.as_uri(), contains='declared but not used')
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0]['severity'], 2, errors)
        self.assertEqual(errors[0]['range']['start'], self.position(text, 'unused', 0))
        self.assertEqual(errors[0]['range']['end'], self.position(text, 'unused', len('unused')))
        corrected = text.replace('do unused:=1', '{unused:=1;_=unused}')
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': 2},
                                             'contentChanges': [{'text': corrected}]})
        client.diagnostic(path.as_uri(), empty=True)
        self.assertEqual(path.read_text(), text)


if __name__ == '__main__':
    unittest.main()

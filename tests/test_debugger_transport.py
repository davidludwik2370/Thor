"""Debugger test transport deadlines and cleanup need no native backend."""
from collections import deque
import io
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_debugger import DAP


class DebuggerTransportTests(unittest.TestCase):
    def client(self):
        client=object.__new__(DAP)
        client.messages=queue.Queue()
        client.responses={}
        client.events=[]
        client.commands={7:'configurationDone'}
        client.recent=deque(maxlen=24)
        client.log=io.BytesIO(b'backend diagnostic')
        client.process=SimpleNamespace(poll=lambda:None)
        return client

    def test_response_timeout_reports_request_backend_and_recent_output(self):
        client=self.client()
        client.recent.append('event output: Launching test executable')
        with self.assertRaises(TimeoutError) as raised:
            client.response(7,timeout=0)
        text=str(raised.exception)
        self.assertIn('configurationDone',text)
        self.assertIn('Launching test executable',text)
        self.assertIn('backend diagnostic',text)

    def test_empty_transport_has_the_same_useful_timeout(self):
        client=self.client()
        with self.assertRaisesRegex(TimeoutError,'configurationDone'):
            client.next(0,waiting='configurationDone')

    def test_output_events_do_not_extend_a_request_deadline(self):
        client=self.client()
        client.messages.put({'type':'event','event':'output','body':{'output':'still launching'}})
        with patch('tests.test_debugger.time.monotonic',side_effect=[0,.25,1.01]):
            with self.assertRaisesRegex(TimeoutError,'configurationDone'):
                client.response(7,timeout=1)
        self.assertEqual(len(client.events),1)
        self.assertIn('still launching',client.recent[-1])

    def test_unrelated_events_do_not_extend_an_event_deadline(self):
        client=self.client()
        client.messages.put({'type':'event','event':'module','body':{}})
        with patch('tests.test_debugger.time.monotonic',side_effect=[0,.25,1.01]):
            with self.assertRaisesRegex(TimeoutError,'event stopped'):
                client.event('stopped',timeout=1)

    def test_response_body_and_backend_errors_are_preserved(self):
        for success in [True,False]:
            with self.subTest(success=success):
                client=self.client()
                message={'type':'response','command':'configurationDone','request_seq':7,
                         'success':success,'body':{'value':42},'message':'native launch failed'}
                client.messages.put(message)
                if success:
                    body=client.response(7)
                    self.assertEqual(body,{'value':42})
                else:
                    with self.assertRaisesRegex(AssertionError,'native launch failed'):
                        client.response(7)
                self.assertNotIn(7,client.commands)

    def test_stalled_disconnect_does_not_mask_the_primary_failure(self):
        client=self.client()
        client.request=Mock(side_effect=TimeoutError('native backend stalled'))
        client.process=Mock()
        client.process.poll.side_effect=[None,0]
        client.process.stdin=io.BytesIO()
        client.process.stdout=io.BytesIO()
        client.reader=Mock()
        client.close()
        client.request.assert_called_once_with('disconnect',{'terminateDebuggee':True},timeout=2)
        self.assertTrue(client.process.stdin.closed)
        self.assertTrue(client.process.stdout.closed)
        self.assertTrue(client.log.closed)
        client.process.kill.assert_not_called()

    def test_cleanup_kills_a_bridge_that_ignores_termination(self):
        client=self.client()
        client.request=Mock(side_effect=TimeoutError('native backend stalled'))
        client.process=Mock()
        client.process.poll.return_value=None
        client.process.wait.side_effect=[subprocess.TimeoutExpired('adapter',3),
                                        subprocess.TimeoutExpired('adapter',3),0]
        client.process.stdin=io.BytesIO()
        client.process.stdout=io.BytesIO()
        client.reader=Mock()
        client.close()
        client.process.terminate.assert_called_once()
        client.process.kill.assert_called_once()
        self.assertTrue(client.log.closed)

    @unittest.skipUnless(os.name=='posix' and shutil.which('node'),
                         'POSIX executable script and Node required')
    def test_real_bridge_reports_an_early_backend_exit_without_hanging(self):
        with tempfile.TemporaryDirectory(prefix='thor-failed-debug-backend-') as temporary:
            directory=Path(temporary)
            executable=directory/'adapter/codelldb'
            executable.parent.mkdir()
            executable.write_text('#!/usr/bin/env node\nprocess.exit(23);\n')
            executable.chmod(0o755)
            with patch.dict(os.environ,{'THOR_CODELLDB':str(directory)}):
                client=DAP(directory)
            try:
                with self.assertRaisesRegex(AssertionError,'CodeLLDB exited.*code 23'):
                    client.request('initialize',{},timeout=3)
            finally:client.close()

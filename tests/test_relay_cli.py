import unittest
import contextlib
import io
import json
import tempfile
from pathlib import Path

from relay_cli import apply_request, demo, main
from relay_core import Store, ValidationError, _text


class RelayCliTests(unittest.TestCase):
    def test_quiet_is_not_an_empty_publishable_payload(self):
        for value in ("", " ", "\n"):
            with self.assertRaises(ValidationError):
                _text(value)

    def test_demo_has_corrected_selected_candidate_and_no_action(self):
        result = demo()
        self.assertEqual(result["external_actions"], 0)
        self.assertEqual(result["action_authority"], "none")
        self.assertEqual(result["record"]["selected_revision"], "r2")
        self.assertEqual(result["record"]["revisions"][-1]["text"], "Mara, I read the question differently.")
        self.assertEqual(result["record"]["deliveries"], [])

    def test_unknown_method_cannot_access_connection(self):
        store = Store(":memory:")
        try:
            for request in ({"method": "_db"}, {"method": "close"},
                            {"method": "recover", "shell": "anything"}):
                with self.assertRaises(ValueError):
                    apply_request(store, request)
        finally:
            store.close()

    def test_packet_cli_outputs_minimized_author_review(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / 'case.sqlite')
            store = Store(db)
            store.create_case('c', {
                'author_route': 'private-route', 'actor_id': 'example-page',
                'actor_kind': 'page', 'destination_id': 'example-target',
                'action': 'comment', 'context_ref': 'private-context',
            }, {'allowed_actor_ids': ['example-page'], 'forbidden_actor_ids': [],
                'protected_destinations': {}})
            store.add_revision('c', 'r', 'Exact words.', 'private-source')
            store.close()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(['packet', '--db', db, '--case', 'c', '--revision', 'r'])
            self.assertEqual(status, 0)
            value = json.loads(output.getvalue())
            self.assertTrue(value['local_only'])
            self.assertFalse(value['result']['executes'])
            self.assertNotIn('private-source', output.getvalue())
            self.assertNotIn('example-target', output.getvalue())

    def test_media_cli_invalid_input_is_structured_error(self):
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / 'request.json'
            request.write_text('{}', encoding='utf-8')
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                status = main(['inspect-media', '--request', str(request)])
            self.assertEqual(status, 2)
            self.assertFalse(json.loads(error.getvalue())['ok'])

    def test_session_cli_persists_context_and_refuses_duplicate_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = str(root / 'session.sqlite')
            request = root / 'request.json'

            def call(method, arguments, expected=0):
                request.write_text(json.dumps({'method': method, 'arguments': arguments}), encoding='utf-8')
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    status = main(['session', '--db', db, '--request', str(request)])
                self.assertEqual(status, expected, error.getvalue())
                return json.loads(output.getvalue() if status == 0 else error.getvalue())

            call('create_session', {'session_id': 's', 'author_route': 'synthetic-saved-thread',
                 'origin': 'observed', 'source': {'ref': 'synthetic-comment', 'exact_text': 'A question?'},
                 'context': 'The surrounding exchange.', 'addressee': 'Example author',
                 'audience': 'private working conversation', 'request': 'Consider the question.'})
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(['session-packet', '--db', db, '--session', 's', '--mode', 'intake'])
            self.assertEqual(status, 0)
            packet = json.loads(output.getvalue())['result']
            self.assertEqual(packet['source']['exact_text'], 'A question?')
            self.assertEqual(packet['context'], 'The surrounding exchange.')
            self.assertEqual(packet['addressee'], 'Example author')
            self.assertFalse(packet['executes'])
            call('prepare_turn', {'session_id': 's', 'turn_id': 't', 'exact_message': 'Coda: Consider this question.'})
            claimed = call('begin_dispatch', {'session_id': 's', 'turn_id': 't', 'attempt_id': 'attempt-1'})
            self.assertEqual(claimed['result']['host_step'], 'send_once_using_supported_tools')
            refused = call('begin_dispatch', {'session_id': 's', 'turn_id': 't', 'attempt_id': 'attempt-2'}, 2)
            self.assertFalse(refused['ok'])
            resumed = call('next_step_packet', {'session_id': 's'})
            self.assertEqual(resumed['result']['host_step'], 'reconcile_before_retry')

    def test_session_cli_rejects_unknown_methods_and_missing_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / 'request.json'
            request.write_text('{"method":"close"}', encoding='utf-8')
            for arguments in (
                ['session', '--db', str(root / 's.sqlite'), '--request', str(request)],
                ['session-packet', '--db', str(root / 's.sqlite'), '--session', 's', '--mode', 'result'],
            ):
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    self.assertEqual(main(arguments), 2)
                self.assertFalse(json.loads(error.getvalue())['ok'])


    def test_action_cli_native_gif_claim_and_uncertainty_use_actual_action_packet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = str(root / 'delivery.sqlite')
            request = root / 'request.json'

            def call(method, arguments, expected=0):
                request.write_text(json.dumps({'method': method, 'arguments': arguments}), encoding='utf-8')
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    status = main(['action', '--db', db, '--request', str(request)])
                self.assertEqual(status, expected, error.getvalue())
                return json.loads(output.getvalue() if status == 0 else error.getvalue())

            created = call('create_action', {
                'action_id': 'gif-message', 'action': 'message',
                'actor': {'actor_id': 'example-page', 'actor_kind': 'page', 'source_ref': 'observed-active-actor'},
                'target': {'destination_id': 'example-thread', 'surface': 'message_thread', 'audience': 'private'},
                'candidate': {'candidate_id': 'gif', 'text': '', 'source_ref': 'author-choice',
                              'action_data': {'platform_media': {'kind': 'gif', 'ref': 'exact-gif-selection'}}},
                'selection_ref': 'explicit-selection', 'authority': {'source_ref': 'test-user-scope'},
                'scope': 'live_test',
            })
            self.assertEqual(created['result']['action'], 'message')
            self.assertEqual(created['result']['candidate']['text'], '')
            self.assertNotIn('media', created['result']['candidate'])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['action-packet', '--db', db, '--action', 'gif-message']), 0)
            packet = json.loads(output.getvalue())['result']
            self.assertFalse(packet['executes'])
            self.assertEqual(packet['host_step'], 'begin_action')
            claim = call('begin_action', {'action_id': 'gif-message', 'operation_id': 'send-once'})['result']
            self.assertTrue(claim['newly_claimed'])
            replay = call('begin_action', {'action_id': 'gif-message', 'operation_id': 'send-once'})['result']
            self.assertFalse(replay['newly_claimed'])
            self.assertEqual(replay['host_step'], 'inspect_existing_operation')
            call('record_outcome', {'operation_id': 'send-once', 'outcome': 'uncertain',
                                   'evidence': {'observation': 'Synthetic interruption after host step', 'source_ref': 'synthetic-observation'}})
            resumed = call('action_packet', {'action_id': 'gif-message'})['result']
            self.assertEqual(resumed['host_step'], 'reconcile_before_retry')
            self.assertEqual(resumed['record']['candidate']['action_data']['platform_media']['ref'], 'exact-gif-selection')
            self.assertFalse(call('close', {}, 2)['ok'])


if __name__ == "__main__":
    unittest.main()

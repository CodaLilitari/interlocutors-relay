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


if __name__ == "__main__":
    unittest.main()

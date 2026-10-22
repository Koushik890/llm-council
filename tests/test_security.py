import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from backend import storage
from backend.main import app


class ConversationSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'conversations'
        self.config = patch.object(storage, 'DATA_DIR', str(self.data))
        self.config.start()
        self.addCleanup(self.config.stop)
        self.addCleanup(self.temp.cleanup)

    def test_valid_conversation_lifecycle(self):
        conversation_id = str(uuid4())
        storage.create_conversation(conversation_id)
        storage.add_user_message(conversation_id, "O'Brien's question")
        storage.update_conversation_title(conversation_id, 'A title')
        stored = storage.get_conversation(conversation_id)
        self.assertEqual(stored['messages'][0]['content'], "O'Brien's question")
        self.assertEqual(storage.list_conversations()[0]['title'], 'A title')
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/conversations/' + conversation_id).status_code, 200)

    def test_storage_rejects_unsafe_identifiers_without_touching_outside_file(self):
        outside = self.root / 'outside.json'
        outside.write_text('{"unchanged": true}')
        unsafe = ['../outside', '..\\outside', 'C:\\outside', '\\\\host\\share',
                  '/outside', '', 'not-a-uuid', str(uuid4()).replace('-', '')]
        for identifier in unsafe:
            with self.subTest(identifier=identifier):
                for action in (storage.get_conversation, storage.create_conversation):
                    with self.assertRaises(ValueError):
                        action(identifier)
                with self.assertRaises(ValueError):
                    storage.save_conversation({'id': identifier, 'messages': []})
        self.assertEqual(outside.read_text(), '{"unchanged": true}')

    def test_all_http_identifier_routes_reject_before_storage_or_provider_work(self):
        with TestClient(app) as client, patch.object(storage, 'get_conversation') as get:
            for identifier in ['..%5Coutside', 'C:%5Coutside', 'not-a-uuid']:
                path = '/api/conversations/' + identifier
                self.assertEqual(client.get(path).status_code, 422)
                for suffix in ['/message', '/message/stream']:
                    self.assertEqual(client.post(path + suffix, json={'content': 'test'}).status_code, 422)
            get.assert_not_called()

    def test_document_cannot_redirect_a_later_save(self):
        conversation_id = str(uuid4())
        storage.create_conversation(conversation_id)
        path = self.data / (conversation_id + '.json')
        document = json.loads(path.read_text())
        document['id'] = '..\\outside'
        path.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            storage.add_user_message(conversation_id, 'test')
        self.assertFalse((self.root / 'outside.json').exists())

    def test_bundled_launcher_is_loopback_only(self):
        with patch('uvicorn.run') as run:
            runpy.run_module('backend.main', run_name='__main__')
        self.assertEqual(run.call_args.kwargs['host'], '127.0.0.1')


if __name__ == '__main__':
    unittest.main()

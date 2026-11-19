import json
import unittest

from seesaw.web import SeesawConnection


class FakeSession(object):
    def __init__(self, session_id):
        self.session_id = session_id


class FakeClient(object):
    '''Stands in for a SeesawConnection: records what emit would send.'''
    def __init__(self, session_id):
        self.session = FakeSession(session_id)
        self.sent = []

    def emit(self, event_name, message):
        # Serialize as the real emit does, so later mutation of the shared
        # message dict cannot leak into what this client "received".
        self.sent.append(json.loads(json.dumps(
            {'event_name': event_name, 'message': message})))


class BroadcastTest(unittest.TestCase):
    def setUp(self):
        self.saved_clients = SeesawConnection.clients
        self.a = FakeClient('session-a')
        self.b = FakeClient('session-b')
        SeesawConnection.clients = set([self.a, self.b])

    def tearDown(self):
        SeesawConnection.clients = self.saved_clients

    def test_every_client_gets_the_same_message_id(self):
        SeesawConnection.broadcast('timestamp', {'timestamp': 1.0})

        [sent_a] = self.a.sent
        [sent_b] = self.b.sent
        self.assertEqual('timestamp', sent_a['event_name'])
        self.assertEqual(sent_a['message']['message_id'],
                         sent_b['message']['message_id'])
        self.assertEqual('session-a', sent_a['message']['session_id'])
        self.assertEqual('session-b', sent_b['message']['session_id'])

    def test_message_id_is_prefixed_per_process_and_increases(self):
        SeesawConnection.broadcast('warrior.status', {'status': 'A'})
        SeesawConnection.broadcast('warrior.status', {'status': 'B'})

        prefix = SeesawConnection.message_id_prefix + '-'
        self.assertEqual(32, len(SeesawConnection.message_id_prefix))
        ids = [m['message']['message_id'] for m in self.a.sent]
        self.assertTrue(all(i.startswith(prefix) for i in ids), ids)
        counters = [int(i[len(prefix):]) for i in ids]
        self.assertEqual(counters[0] + 1, counters[1])

    def test_message_id_is_distinct_for_identical_content(self):
        SeesawConnection.broadcast('item.output', {'item_id': 'x', 'data': ''})
        SeesawConnection.broadcast('item.output', {'item_id': 'x', 'data': ''})

        ids = [m['message']['message_id'] for m in self.a.sent]
        self.assertNotEqual(ids[0], ids[1])

    def test_empty_message_is_passed_through_unchanged(self):
        SeesawConnection.broadcast('project.refresh', None)

        [sent_a] = self.a.sent
        self.assertIsNone(sent_a['message'])

    def test_counter_advances_once_per_broadcast_not_per_client(self):
        SeesawConnection.broadcast('timestamp', {'timestamp': 1.0})
        SeesawConnection.broadcast('timestamp', {'timestamp': 2.0})

        prefix = SeesawConnection.message_id_prefix + '-'
        first = int(self.a.sent[0]['message']['message_id'][len(prefix):])
        second = int(self.b.sent[1]['message']['message_id'][len(prefix):])
        self.assertEqual(first + 1, second)


if __name__ == '__main__':
    unittest.main()

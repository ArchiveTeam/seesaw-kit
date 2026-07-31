import unittest

from seesaw.event import Event


class EventTest(unittest.TestCase):
    def setUp(self):
        self.event = Event()
        self.calls = []

    def test_starts_with_no_handlers(self):
        self.assertEqual(0, len(self.event))
        self.assertEqual(0, self.event.getHandlerCount())

    def test_fires_a_registered_handler(self):
        self.event += self.calls.append
        self.event('hello')

        self.assertEqual(['hello'], self.calls)

    def test_fires_with_positional_and_keyword_arguments(self):
        def handler(*args, **kwargs):
            self.calls.append((args, kwargs))

        self.event += handler
        self.event.fire(1, 2, key='value')

        self.assertEqual([((1, 2), {'key': 'value'})], self.calls)

    def test_fires_every_registered_handler(self):
        self.event += self.calls.append
        self.event.handle(lambda value: self.calls.append(value * 2))
        self.event(3)

        self.assertEqual(sorted([3, 6]), sorted(self.calls))

    def test_registering_the_same_handler_twice_fires_it_once(self):
        self.event += self.calls.append
        self.event += self.calls.append
        self.event('hello')

        self.assertEqual(1, len(self.event))
        self.assertEqual(['hello'], self.calls)

    def test_unhandle_removes_the_handler(self):
        self.event += self.calls.append
        self.event -= self.calls.append
        self.event('hello')

        self.assertEqual(0, len(self.event))
        self.assertEqual([], self.calls)

    def test_unhandle_of_an_unregistered_handler_raises(self):
        self.assertRaises(ValueError, self.event.unhandle, self.calls.append)

    def test_handle_and_unhandle_return_the_event_for_chaining(self):
        self.assertIs(self.event, self.event.handle(self.calls.append))
        self.assertIs(self.event, self.event.unhandle(self.calls.append))

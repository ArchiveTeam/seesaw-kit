import unittest

from seesaw.item import Item


class MockPipeline(object):
    pass


class ItemTest(unittest.TestCase):
    def setUp(self):
        self.item = Item(MockPipeline(), 'FakeID', 1, prepare_data_directory=False)

    def test_get_returns_none_for_undefined_keys(self):
        self.assertEquals(None, self.item.get('undefined_key'))

    def test_get_returns_property(self):
        self.item['foo'] = 'bar'

        self.assertEquals('bar', self.item.get('foo'))

    def test_property_events(self):
        non_local_dict = {}

        def my_callback(item, key, new_value, old_value):
            self.assertEquals(self.item, item)
            non_local_dict['callback_fired'] = True
            self.assertEqual('blah', key)
            self.assertEqual('blahblah', new_value)
            self.assertEqual(None, old_value)

        self.item.on_property.handle(my_callback)
        self.item['blah'] = 'blahblah'

        self.assertTrue(non_local_dict.get('callback_fired'))

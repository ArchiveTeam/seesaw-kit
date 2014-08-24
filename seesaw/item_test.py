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

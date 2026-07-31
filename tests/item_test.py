# encoding=utf8

import os
import os.path
import shutil
import tempfile
import unittest

from seesaw.item import Item, ItemData, ItemInterpolation, ItemValue


class MockPipeline(object):
    '''Stands in for a Pipeline.

    An Item only ever reads ``data_dir`` off its pipeline, so a real
    Pipeline is not needed to exercise item behaviour.
    '''
    def __init__(self, data_dir=None):
        self.data_dir = data_dir


class ItemDataTest(unittest.TestCase):
    def setUp(self):
        self.data = ItemData()
        self.events = []
        self.data.on_property += \
            lambda data, key, new, old: self.events.append((key, new, old))

    def test_behaves_like_a_mapping(self):
        self.data['a'] = 1
        self.data['b'] = 2

        self.assertEqual(2, len(self.data))
        self.assertEqual({'a', 'b'}, set(self.data))
        self.assertEqual({'a': 1, 'b': 2}, dict(self.data))
        self.assertIn('a', self.data)

    def test_wraps_an_existing_dict(self):
        properties = {'a': 1}
        data = ItemData(properties=properties)

        data['b'] = 2

        self.assertEqual({'a': 1, 'b': 2}, properties)
        self.assertIs(properties, data.properties)

    def test_setting_a_new_key_fires_on_property(self):
        self.data['a'] = 1

        self.assertEqual([('a', 1, None)], self.events)

    def test_changing_a_value_fires_on_property_with_the_old_value(self):
        self.data['a'] = 1
        self.data['a'] = 2

        self.assertEqual([('a', 1, None), ('a', 2, 1)], self.events)

    def test_setting_the_same_value_does_not_fire_on_property(self):
        self.data['a'] = 1
        self.data['a'] = 1

        self.assertEqual([('a', 1, None)], self.events)

    def test_deleting_a_key_fires_on_property(self):
        self.data['a'] = 1
        del self.data['a']

        self.assertEqual([('a', 1, None), ('a', None, 1)], self.events)
        self.assertNotIn('a', self.data)

    def test_deleting_a_falsy_value_does_not_fire_on_property(self):
        self.data['a'] = 0
        del self.data['a']

        self.assertEqual([('a', 0, None)], self.events)

    def test_missing_key_raises_key_error(self):
        self.assertRaises(KeyError, lambda: self.data['nope'])


class ItemTest(unittest.TestCase):
    def setUp(self):
        self.item = Item(MockPipeline(), 'FakeID', 1,
                         prepare_data_directory=False)

    def test_get_returns_none_for_undefined_keys(self):
        self.assertEqual(None, self.item.get('undefined_key'))

    def test_get_returns_property(self):
        self.item['foo'] = 'bar'

        self.assertEqual('bar', self.item.get('foo'))

    def test_property_events(self):
        non_local_dict = {}

        def my_callback(item, key, new_value, old_value):
            self.assertEqual(self.item, item)
            non_local_dict['callback_fired'] = True
            self.assertEqual('blah', key)
            self.assertEqual('blahblah', new_value)
            self.assertEqual(None, old_value)

        self.item.on_property.handle(my_callback)
        self.item['blah'] = 'blahblah'

        self.assertTrue(non_local_dict.get('callback_fired'))

    def test_exposes_its_identity(self):
        self.assertEqual('FakeID', self.item.item_id)
        self.assertEqual(1, self.item.item_number)
        self.assertEqual(hash('FakeID'), hash(self.item))

    def test_starts_running_and_unfinished(self):
        self.assertEqual(Item.ItemState.running, self.item.item_state)
        self.assertFalse(self.item.finished)
        self.assertFalse(self.item.completed)
        self.assertFalse(self.item.failed)
        self.assertFalse(self.item.canceled)
        self.assertTrue(self.item.start_time)
        self.assertEqual(None, self.item.end_time)

    def test_description_uses_the_item_name(self):
        self.assertEqual('Item ', self.item.description())

        self.item['item_name'] = 'example'

        self.assertEqual('Item example', self.item.description())

    def test_str_includes_name_id_and_state(self):
        self.item['item_name'] = 'example'

        self.assertEqual("<Item 'example' FakeID running>", str(self.item))


class ItemStateTest(unittest.TestCase):
    def setUp(self):
        # keep_data skips clear_data_directory, which would look for the
        # "data_dir" property that prepare_data_directory would have set.
        self.item = Item(MockPipeline(), 'FakeID', 1, keep_data=True,
                         prepare_data_directory=False)
        self.states = []
        self.legacy = []
        self.item.on_item_state += \
            lambda item, state: self.states.append(state)
        self.item.on_complete += lambda item: self.legacy.append('complete')
        self.item.on_fail += lambda item: self.legacy.append('fail')
        self.item.on_cancel += lambda item: self.legacy.append('cancel')
        self.item.on_finish += lambda item: self.legacy.append('finish')

    def test_complete(self):
        self.item.complete()

        self.assertTrue(self.item.completed)
        self.assertTrue(self.item.finished)
        self.assertTrue(self.item.end_time)
        self.assertEqual([Item.ItemState.completed], self.states)
        self.assertEqual(['complete', 'finish'], self.legacy)

    def test_fail(self):
        self.item.fail()

        self.assertTrue(self.item.failed)
        self.assertTrue(self.item.finished)
        self.assertEqual(['fail', 'finish'], self.legacy)

    def test_cancel(self):
        self.item.cancel()

        self.assertTrue(self.item.canceled)
        self.assertTrue(self.item.finished)
        self.assertEqual(['cancel', 'finish'], self.legacy)

    def test_completing_twice_is_refused(self):
        self.item.complete()

        self.assertRaises(AssertionError, self.item.complete)

    def test_failing_twice_is_refused(self):
        self.item.fail()

        self.assertRaises(AssertionError, self.item.fail)

    def test_canceling_twice_is_refused(self):
        self.item.cancel()

        self.assertRaises(AssertionError, self.item.cancel)


class ItemTaskStatusTest(unittest.TestCase):
    def setUp(self):
        self.item = Item(MockPipeline(), 'FakeID', 1,
                         prepare_data_directory=False)
        self.changes = []
        self.item.on_task_status += \
            lambda item, task, new, old: self.changes.append((task, new, old))

    def test_records_and_announces_a_status(self):
        self.item.set_task_status('a-task', Item.TaskStatus.running)

        self.assertEqual({'a-task': Item.TaskStatus.running},
                         self.item.task_status)
        self.assertEqual([('a-task', Item.TaskStatus.running, None)],
                         self.changes)

    def test_announces_the_previous_status_on_change(self):
        self.item.set_task_status('a-task', Item.TaskStatus.running)
        self.item.set_task_status('a-task', Item.TaskStatus.completed)

        self.assertEqual(
            ('a-task', Item.TaskStatus.completed, Item.TaskStatus.running),
            self.changes[-1])

    def test_setting_the_same_status_twice_announces_once(self):
        self.item.set_task_status('a-task', Item.TaskStatus.running)
        self.item.set_task_status('a-task', Item.TaskStatus.running)

        self.assertEqual(1, len(self.changes))


class ItemLoggingTest(unittest.TestCase):
    def setUp(self):
        self.item = Item(MockPipeline(), 'FakeID', 1,
                         prepare_data_directory=False)
        self.output = []
        self.item.on_output += lambda item, data: self.output.append(data)

    def test_appends_a_trailing_newline(self):
        self.item.log_output('hello')

        self.assertEqual(['hello\n'], self.output)

    def test_separates_a_line_from_an_unterminated_previous_one(self):
        self.item.log_output('no newline', full_line=False)
        self.item.log_output('next line')

        self.assertEqual(['no newline', '\nnext line\n'], self.output)

    def test_does_not_add_a_separator_after_a_terminated_line(self):
        self.item.log_output('first')
        self.item.log_output('second')

        self.assertEqual(['first\n', 'second\n'], self.output)

    def test_passes_partial_output_through_untouched(self):
        self.item.log_output('partial', full_line=False)

        self.assertEqual(['partial'], self.output)

    def test_empty_output_is_passed_through(self):
        self.item.log_output('')

        self.assertEqual([''], self.output)

    def test_decodes_utf8_bytes(self):
        self.item.log_output('héllo wörld'.encode('utf8'))

        self.assertEqual(['héllo wörld\n'], self.output)

    def test_replaces_undecodable_bytes(self):
        self.item.log_output(b'\xff\xfe')

        self.assertEqual(1, len(self.output))
        self.assertNotIn(b'\xff', self.output[0].encode('utf8', 'replace'))

    def test_log_error_records_and_announces(self):
        errors = []
        self.item.on_error += \
            lambda item, task, *args: errors.append((task, args))

        self.item.log_error('a-task', 'boom', 42)

        self.assertEqual([('a-task', ('boom', 42))], errors)


class ItemDataDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.pipeline = MockPipeline(data_dir=os.path.join(self.temp_dir,
                                                           'data'))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_creates_the_data_directory(self):
        item = Item(self.pipeline, 'the-id', 1)

        self.assertEqual(os.path.join(self.pipeline.data_dir, 'the-id'),
                         item['data_dir'])
        self.assertTrue(os.path.isdir(item['data_dir']))

    def test_replaces_a_stale_data_directory(self):
        item = Item(self.pipeline, 'the-id', 1)
        stale_file = os.path.join(item['data_dir'], 'stale.txt')
        with open(stale_file, 'w') as file_obj:
            file_obj.write('stale')

        Item(self.pipeline, 'the-id', 2)

        self.assertTrue(os.path.isdir(item['data_dir']))
        self.assertFalse(os.path.exists(stale_file))

    def test_clear_data_directory_removes_it(self):
        item = Item(self.pipeline, 'the-id', 1)

        item.clear_data_directory()

        self.assertFalse(os.path.exists(item['data_dir']))

    def test_clear_data_directory_tolerates_a_missing_directory(self):
        item = Item(self.pipeline, 'the-id', 1)
        shutil.rmtree(item['data_dir'])

        item.clear_data_directory()

    def test_keep_data_preserves_the_directory(self):
        item = Item(self.pipeline, 'the-id', 1, keep_data=True)

        item.complete()

        self.assertTrue(os.path.isdir(item['data_dir']))

    def test_finishing_an_item_clears_the_directory(self):
        item = Item(self.pipeline, 'the-id', 1)

        item.complete()

        self.assertFalse(os.path.exists(item['data_dir']))


class ItemValueTest(unittest.TestCase):
    def test_realize_reads_the_key(self):
        self.assertEqual('bar', ItemValue('foo').realize({'foo': 'bar'}))

    def test_fill_writes_the_key(self):
        item = {}

        ItemValue('foo').fill(item, 'bar')

        self.assertEqual({'foo': 'bar'}, item)

    def test_str_shows_the_key(self):
        self.assertEqual('<foo>', str(ItemValue('foo')))


class ItemInterpolationTest(unittest.TestCase):
    def test_realize_formats_against_the_item(self):
        interpolation = ItemInterpolation('%(data_dir)s/%(item_name)s')

        result = interpolation.realize({'data_dir': '/tmp/x',
                                        'item_name': 'thing'})

        self.assertEqual('/tmp/x/thing', result)

    def test_realize_works_with_a_real_item(self):
        item = Item(MockPipeline(), 'FakeID', 1,
                    prepare_data_directory=False)
        item['item_name'] = 'thing'

        self.assertEqual('got thing',
                         ItemInterpolation('got %(item_name)s').realize(item))

    def test_str_shows_the_format_string(self):
        self.assertEqual("<'%(item_name)s'>",
                         str(ItemInterpolation('%(item_name)s')))


class ItemLegacyEventTest(unittest.TestCase):
    def test_an_unknown_state_is_rejected(self):
        item = Item(MockPipeline(), 'FakeID', 1, keep_data=True,
                    prepare_data_directory=False)

        self.assertRaises(Exception, item._dispatch_legacy_events, item,
                          'not-a-real-state')


class ItemValueFillTest(unittest.TestCase):
    def test_filling_through_the_class_with_a_non_item_value_is_rejected(self):
        # fill() inspects `self`, so calling it unbound is the only way to
        # reach the guard.
        self.assertRaises(Exception, ItemValue.fill, 'not an ItemValue',
                          {}, 'value')

    def test_filling_through_the_class_with_none_does_nothing(self):
        item = {}

        ItemValue.fill(None, item, 'value')

        self.assertEqual({}, item)

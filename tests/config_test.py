# encoding=utf8

import unittest

from seesaw.config import ConfigInterpolation, ConfigValue, \
    NumberConfigValue, StringConfigValue, realize
from seesaw.item import ItemValue


class Realizable(object):
    '''Minimal object implementing the realize protocol.'''
    def __init__(self, value):
        self.value = value

    def realize(self, item):
        return self.value


class RealizeTest(unittest.TestCase):
    def test_passes_through_plain_values(self):
        self.assertEqual('hello', realize('hello'))
        self.assertEqual(42, realize(42))
        self.assertEqual(None, realize(None))

    def test_calls_realize_on_realizable(self):
        self.assertEqual('done', realize(Realizable('done')))

    def test_passes_item_to_realize(self):
        self.assertEqual('bar', realize(ItemValue('foo'), {'foo': 'bar'}))

    def test_realizes_list_recursively(self):
        result = realize(['a', Realizable('b'), ['c', Realizable('d')]])

        self.assertEqual(['a', 'b', ['c', 'd']], result)

    def test_realizes_dict_recursively(self):
        result = realize({'a': Realizable(1), 'b': {'c': Realizable(2)}})

        self.assertEqual({'a': 1, 'b': {'c': 2}}, result)

    def test_does_not_mutate_the_original_dict(self):
        original = {'a': Realizable(1)}

        realize(original)

        self.assertIsInstance(original['a'], Realizable)


class ConfigValueTest(unittest.TestCase):
    def tearDown(self):
        # A test that fails midway through collecting must not leak the
        # collector into unrelated tests.
        ConfigValue.collector = None

    def test_default_is_stored_as_the_value(self):
        config_value = ConfigValue(name='thing', default='initial')

        self.assertEqual('initial', config_value.value)
        self.assertEqual(None, config_value.error)

    def test_realize_returns_the_value(self):
        config_value = ConfigValue(name='thing', default='initial')

        self.assertEqual('initial', config_value.realize(None))
        self.assertEqual('initial', realize(config_value))

    def test_set_value_accepts_anything_by_default(self):
        config_value = ConfigValue(name='thing')

        self.assertTrue(config_value.set_value('anything'))
        self.assertEqual('anything', config_value.value)

    def test_is_valid_requires_a_value(self):
        self.assertFalse(ConfigValue(name='thing').is_valid())
        self.assertTrue(ConfigValue(name='thing', default='x').is_valid())

    def test_str_includes_name_and_value(self):
        self.assertEqual('<thing:5>', str(ConfigValue(name='thing',
                                                      default=5)))

    def test_collecting_gathers_values_constructed_in_between(self):
        ConfigValue.start_collecting()
        first = ConfigValue(name='first')
        second = StringConfigValue(name='second')
        collected = ConfigValue.stop_collecting()

        self.assertEqual([first, second], collected)
        self.assertEqual(None, ConfigValue.collector)

    def test_values_constructed_outside_collecting_are_not_gathered(self):
        ConfigValue(name='before')
        ConfigValue.start_collecting()
        during = ConfigValue(name='during')
        collected = ConfigValue.stop_collecting()
        ConfigValue(name='after')

        self.assertEqual([during], collected)


class StringConfigValueTest(unittest.TestCase):
    def test_accepts_any_string_without_a_regex(self):
        config_value = StringConfigValue(name='nick')

        self.assertTrue(config_value.set_value('  anything at all  '))
        self.assertEqual('  anything at all  ', config_value.value)

    def test_accepts_a_value_matching_the_regex(self):
        config_value = StringConfigValue(name='nick',
                                         regex='^[a-z]{3,10}$')

        self.assertTrue(config_value.set_value('someone'))
        self.assertEqual('someone', config_value.value)

    def test_rejects_a_value_not_matching_the_regex(self):
        config_value = StringConfigValue(name='nick', title='Nickname',
                                         regex='^[a-z]{3,10}$',
                                         default='someone')

        self.assertFalse(config_value.set_value('!!'))
        self.assertEqual('Invalid value for nickname.', config_value.error)
        self.assertEqual('someone', config_value.value)

    def test_regex_is_checked_against_the_stripped_value(self):
        config_value = StringConfigValue(name='nick',
                                         regex='^[a-z]{3,10}$')

        self.assertTrue(config_value.set_value('  someone  '))

        # check_value strips, but convert_value stores the raw string.
        self.assertEqual('  someone  ', config_value.value)


class NumberConfigValueTest(unittest.TestCase):
    def test_converts_the_default_to_an_int(self):
        config_value = NumberConfigValue(name='count', default='7')

        self.assertEqual(7, config_value.value)

    def test_accepts_a_numeric_string(self):
        config_value = NumberConfigValue(name='count', default=1)

        self.assertTrue(config_value.set_value(' 12 '))
        self.assertEqual(12, config_value.value)

    def test_rejects_a_non_numeric_string(self):
        config_value = NumberConfigValue(name='count', default=1)

        self.assertFalse(config_value.set_value('twelve'))
        self.assertEqual('Invalid number.', config_value.error)
        self.assertEqual(1, config_value.value)

    def test_rejects_a_value_below_the_minimum(self):
        config_value = NumberConfigValue(name='count', default=3, min=2, max=6)

        self.assertFalse(config_value.set_value('1'))
        self.assertEqual('Number must be 2 or greater.', config_value.error)

    def test_rejects_a_value_above_the_maximum(self):
        config_value = NumberConfigValue(name='count', default=3, min=2, max=6)

        self.assertFalse(config_value.set_value('7'))
        self.assertEqual('Number must be 6 or smaller.', config_value.error)

    def test_accepts_the_boundary_values(self):
        config_value = NumberConfigValue(name='count', default=3, min=2, max=6)

        self.assertTrue(config_value.set_value('2'))
        self.assertTrue(config_value.set_value('6'))
        self.assertEqual(6, config_value.value)


class ConfigInterpolationTest(unittest.TestCase):
    def test_interpolates_plain_values(self):
        interpolation = ConfigInterpolation('%s/%s', ('a', 'b'))

        self.assertEqual('a/b', interpolation.realize(None))

    def test_realizes_both_sides_before_interpolating(self):
        interpolation = ConfigInterpolation(
            '%(host)s/%(name)s',
            {'host': StringConfigValue(name='host', default='example.com'),
             'name': ItemValue('item_name')})

        self.assertEqual('example.com/thing',
                         interpolation.realize({'item_name': 'thing'}))

    def test_str_shows_the_format_string(self):
        self.assertEqual("<'%s/%s'>", str(ConfigInterpolation('%s/%s', ())))

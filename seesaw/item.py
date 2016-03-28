'''Managing work units.'''
import os
import os.path
import shutil
import traceback
import time
import collections

from seesaw.event import Event
import seesaw.six


class ItemData(collections.MutableMapping):
    '''Base item data property container.

    Args:
        properties (dict): Original dict
        on_property (Event): Fired whenever a property changes.
            Callback accepts:

            1. self
            2. key
            3. new value
            4. old value
    '''
    def __init__(self, properties=None):
        super(ItemData, self).__init__()
        self._properties = properties or {}
        self.on_property = Event()

    @property
    def properties(self):
        return self._properties

    def __getitem__(self, key):
        return self._properties[key]

    def __setitem__(self, key, value):
        old_value = self._properties.get(key, None)

        self._properties[key] = value

        if old_value != value:
            self.on_property(self, key, value, old_value)

    def __delitem__(self, key):
        old_value = self.properties.get(key, None)

        del self.properties[key]

        if old_value:
            self.on_property(self, key, None, old_value)

    def __len__(self):
        return len(self._properties)

    def __iter__(self):
        return iter(self._properties)


class Item(ItemData):
    '''A thing, or work unit, that needs to be downloaded.

    It has properties that are filled by the :class:`Task`.

    An Item behaves like a mutable mapping.

    .. note::
        State belonging to a item should be stored on the actual item
        itself. That is, do not store variables onto a :class:`Task` unless
        you know what you are doing.
    '''

    class ItemState(object):
        '''State of the item.'''
        running = "running"
        canceled = "canceled"
        completed = "completed"
        failed = "failed"

    class TaskStatus(object):
        '''Status of happened on a task.'''
        running = "running"
        completed = "completed"
        failed = "failed"

    def __init__(self, pipeline, item_id, item_number,
                 keep_data=False, prepare_data_directory=True,
                 **kwargs):
        super(Item, self).__init__(**kwargs)

        self._pipeline = pipeline
        self._item_id = item_id
        self._item_number = item_number
        self._keep_data = keep_data
        self.may_be_canceled = False

        self._item_state = self.ItemState.running
        self._task_status = {}
        self._start_time = time.time()
        self._end_time = None
        self._errors = []
        self._last_output = ""

        self.on_output = Event()
        self.on_error = Event()
        self.on_item_state = Event()
        self.on_task_status = Event()

        # Legacy events
        self.on_cancel = Event()
        self.on_complete = Event()
        self.on_fail = Event()
        self.on_finish = Event()
        self.on_item_state.handle(self._dispatch_legacy_events)

        if prepare_data_directory:
            self.prepare_data_directory()

    def __hash__(self):
        return hash(self._item_id)

    @property
    def item_state(self):
        return self._item_state

    @property
    def task_status(self):
        return self._task_status

    @property
    def start_time(self):
        return self._start_time

    @property
    def end_time(self):
        return self._end_time

    @property
    def canceled(self):
        return self._item_state == self.ItemState.canceled

    @property
    def completed(self):
        return self._item_state == self.ItemState.completed

    @property
    def failed(self):
        return self._item_state == self.ItemState.failed

    @property
    def finished(self):
        return self.canceled or self.completed or self.failed

    @property
    def pipeline(self):
        return self._pipeline

    @property
    def item_id(self):
        return self._item_id

    @property
    def item_number(self):
        return self._item_number

    def prepare_data_directory(self):
        dirname = os.path.join(self._pipeline.data_dir, self._item_id)
        self["data_dir"] = dirname
        if os.path.isdir(dirname):
            shutil.rmtree(dirname)
        os.makedirs(dirname)

    def clear_data_directory(self):
        if not self._keep_data:
            dirname = self["data_dir"]
            if os.path.isdir(dirname):
                shutil.rmtree(dirname)

    def log_output(self, data, full_line=True):
        if isinstance(data, seesaw.six.binary_type):
            try:
                data = data.decode('utf8', 'replace')
            except UnicodeError:
                data = data.decode('ascii', 'replace')

        if full_line and len(data) > 0:
            if data[0] != "\n" and len(self._last_output) > 0 and \
                    self._last_output[-1] != "\n":
                data = "\n" + data
            if data[-1] != "\n":
                data += "\n"
        self._last_output = data
        self.on_output(self, data)

    def log_error(self, task, *args):
        self._errors.append((task, args))
        self.on_error(self, task, *args)

    def set_task_status(self, task, status):
        if task in self._task_status:
            old_status = self._task_status[task]
        else:
            old_status = None
        if status != old_status:
            self._task_status[task] = status
            self.on_task_status(self, task, status, old_status)

    def cancel(self):
        assert not self.canceled
        self.clear_data_directory()
        self._item_state = self.ItemState.canceled
        self._end_time = time.time()
        self.on_item_state(self, self._item_state)

    def complete(self):
        assert not self.completed
        self.clear_data_directory()
        self._item_state = self.ItemState.completed
        self._end_time = time.time()
        self.on_item_state(self, self._item_state)

    def fail(self):
        assert not self.failed
        self.clear_data_directory()
        self._item_state = self.ItemState.failed
        self._end_time = time.time()
        self.on_item_state(self, self._item_state)

    def _dispatch_legacy_events(self, item, state):
        if state == self.ItemState.failed:
            self.on_fail(item)
        elif state == self.ItemState.completed:
            self.on_complete(item)
        elif state == self.ItemState.canceled:
            self.on_cancel(item)
        else:
            raise Exception('Unknown event')

        self.on_finish(item)

    def description(self):
        return "Item %s" % self.properties.get("item_name", "")

    def __str__(self):
        return "<Item '{0}' {1} {2}>".format(
            self.properties.get("item_name", ""),
            self._item_id, self._item_state
        )
        # s = "Item " + ("FAILED " if self.failed else "") + str(self.properties)
        # for err in self._errors:
        #     for e in err[1]:
        #         # TODO this isn't how exceptions work?
        #         if isinstance(e, Exception):
        #             s += "%s\n" % traceback.format_exception(Exception, e,
        #                                                      None)
        #         else:
        #             s += "%s\n" % str(e)
        #     s += "\n  " + str(err)
        # return s


class ItemValue(object):
    '''Get an item's value during :func:`realize`.'''
    def __init__(self, key):
        self.key = key

    def realize(self, item):
        return item[self.key]

    def fill(self, item, value):
        if isinstance(self, ItemValue):
            item[self.key] = value
        elif self is None:
            pass
        else:
            raise Exception("Attempting to fill " + str(type(self)))

    def __str__(self):
        return "<" + self.key + ">"


class ItemInterpolation(object):
    '''Formats a string using the percent operator during :func:`realize`.'''
    def __init__(self, s):
        self.s = s

    def realize(self, item):
        return self.s % item

    def __str__(self):
        return "<'" + self.s + "'>"

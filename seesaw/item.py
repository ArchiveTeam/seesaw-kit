'''Managing work units.'''
import traceback
import os
import os.path
import shutil

from seesaw.event import Event


class Item(object):
    '''A thing, or work unit, that needs to be downloaded.

    It has properties that are filled by the :class:`Task`.

    An Item behaves like a mutable mapping.

    .. note::
        State belonging to a item should be stored on the actual item 
        itself. That is, do not store variables onto a :class:`Task` unless
        you know what you are doing.
    '''
    def __init__(self, pipeline, item_id, item_number, properties=None, keep_data=False):
        self.pipeline = pipeline
        self.item_id = item_id
        self.item_number = item_number
        self.properties = properties or {}
        self.keep_data = keep_data

        self.may_be_canceled = False
        self.canceled = False
        self.completed = False
        self.failed = False
        self._errors = []
        self._last_output = ""
        self.task_status = {}

        self.on_output = Event()
        self.on_error = Event()
        self.on_task_status = Event()
        self.on_property = Event()
        self.on_cancel = Event()
        self.on_complete = Event()
        self.on_fail = Event()
        self.on_finish = Event()

        self.prepare_data_directory()

    def prepare_data_directory(self):
        dirname = os.path.join(self.pipeline.data_dir, self.item_id)
        self["data_dir"] = dirname
        if os.path.isdir(dirname):
            shutil.rmtree(dirname)
        os.makedirs(dirname)

    def clear_data_directory(self):
        if not self.keep_data:
            dirname = self["data_dir"]
            if os.path.isdir(dirname):
                shutil.rmtree(dirname)

    def log_output(self, data, full_line=True):
        if full_line and len(data) > 0:
            if data[0] != "\n" and len(self._last_output) > 0 and self._last_output[-1] != "\n":
                data = "\n" + data
            if data[-1] != "\n":
                data = data + "\n"
        self._last_output = data
        self.on_output(self, data)

    def log_error(self, task, *args):
        self._errors.append((task, args))
        self.on_error(self, task, *args)

    def set_task_status(self, task, status):
        if task in self.task_status:
            old_status = self.task_status[task]
        else:
            old_status = None
        if status != old_status:
            self.task_status[task] = status
            self.on_task_status(self, task, status, old_status)

    def cancel(self):
        assert not self.canceled
        self.clear_data_directory()
        self.canceled = True
        self.finished = True
        self.on_cancel(self)
        self.on_finish(self)

    def complete(self):
        assert not self.completed
        self.clear_data_directory()
        self.completed = True
        self.finished = True
        self.on_complete(self)
        self.on_finish(self)

    def fail(self):
        assert not self.failed
        self.clear_data_directory()
        self.failed = True
        self.finished = True
        self.on_fail(self)
        self.on_finish(self)

    def description(self):
        return "Item %s" % (self.properties["item_name"] if "item_name" in self.properties else "")

    def __contains__(self, key):
        return key in self.properties

    def __getitem__(self, key):
        return self.properties[key]

    def __setitem__(self, key, value):
        old_value = self.properties[key] if key in self.properties else None
        self.properties[key] = value
        if old_value != value:
            self.on_property(self, key, value, old_value)

    def __delitem__(self, key):
        old_value = self.properties[key] if key in self.properties else None
        del self.properties[key]
        if old_value:
            self.on_property(self, key, None, old_value)

    def __str__(self):
        s = "Item " + ("FAILED " if self.failed else "") + str(self.properties)
        for err in self._errors:
            for e in err[1]:
                # TODO this isn't how exceptions work?
                if isinstance(e, Exception):
                    s += "%s\n" % traceback.format_exception(Exception, e, None)
                else:
                    s += "%s\n" % str(e)
            s += "\n  " + str(err)
        return s

    class TaskStatus(object):
        running = "running"
        completed = "completed"
        failed = "failed"


class ItemValue(object):
    '''Get an item's value during :func:`realize`.'''
    def __init__(self, key):
        self.key = key

    def realize(self, item):
        return item[self.key]

    def fill(self, item, value):
        if isinstance(self, ItemValue):
            item[self.key] = value
        elif self == None:
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

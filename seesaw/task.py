'''Managing steps in a work unit.'''
import contextlib
import os
import traceback

import tornado.stack_context

from seesaw.event import Event
from seesaw.item import Item
from seesaw.config import realize


class Task(object):
    '''A step in the download process of an :class:`Item`.
    '''
    def __init__(self, name):
        self.name = name
        self.cwd = os.getcwd()
        self.on_start_item = Event()
        self.on_complete_item = Event()
        self.on_fail_item = Event()
        self.on_finish_item = Event()

    def start_item(self, item):
        item.set_task_status(self, Item.TaskStatus.running)
        self.on_start_item(self, item)

    def fail_item(self, item):
        item.set_task_status(self, Item.TaskStatus.failed)
        self.on_fail_item(self, item)
        self.on_finish_item(self, item)

    def complete_item(self, item):
        item.set_task_status(self, Item.TaskStatus.completed)
        self.on_complete_item(self, item)
        self.on_finish_item(self, item)

    @contextlib.contextmanager
    def task_cwd(self):
        curdir = os.getcwd()
        try:
            os.chdir(self.cwd)
            yield
        finally:
            os.chdir(curdir)

    def fill_ui_task_list(self, task_list):
        task_list.append((self, self.name))

    def __str__(self):
        return self.name

    # Helper to run "inner" tasks while still calling the correct tasks's item failure
    # handler on exceptions.
    def _enqueue_inner_task_with_except(self, inner_task, item):
        @contextlib.contextmanager
        def handle_item_exception(e_type, e_value, tb):
            item.log_output("Failed %s for %s\n" % (inner_task, item.description()))
            item.log_output(
                "".join(traceback.format_exception(e_type, e_value, tb))
            )
            item.log_error(self, e_value)
            inner_task.fail_item(item)

        with tornado.stack_context.NullContext():
            with tornado.stack_context.ExceptionStackContext(
                    handle_item_exception):
                inner_task.enqueue(item)


class SimpleTask(Task):
    '''A subclassable :class:`Task` that should do one small thing well.

    Example::

        class MyTask(SimpleTask):
            def process(self, item):
                item['my_message'] = 'hello world!'
    '''
    def __init__(self, name):
        Task.__init__(self, name)

    def enqueue(self, item):
        self.start_item(item)
        item.log_output("Starting %s for %s\n" % (self, item.description()))
        try:
            with self.task_cwd():
                self.process(item)
        except Exception as e:
            item.log_output("Failed %s for %s\n" % (self, item.description()))
            item.log_output("%s\n" % traceback.format_exc())
            item.log_error(self, e)
            self.fail_item(item)
        else:
            item.log_output("Finished %s for %s\n" % (self,
                                                      item.description()))
            self.complete_item(item)

    def process(self, item):
        # TODO: should this raise NotImplemented or be decorated with
        # abc.abstractmethod?
        pass

    def __str__(self):
        return self.name


class LimitConcurrent(Task):
    '''Restricts the number of tasks of the same type that can be run at once.
    '''
    def __init__(self, concurrency, inner_task):
        Task.__init__(self, "LimitConcurrent")
        self.concurrency = concurrency
        self.inner_task = inner_task
        self.inner_task.on_complete_item += self._inner_task_complete_item
        self.inner_task.on_fail_item += self._inner_task_fail_item
        self._queue = []
        self._working = 0

    def enqueue(self, item):
        if self._working < realize(self.concurrency, item):
            self._working += 1
            self._enqueue_inner_task_with_except(self.inner_task, item)
        else:
            self._queue.append(item)

    def _inner_task_complete_item(self, task, item):
        self._working -= 1
        if len(self._queue) > 0:
            self._working += 1
            self._enqueue_inner_task_with_except(self.inner_task, self._queue.pop(0))
        self.complete_item(item)

    def _inner_task_fail_item(self, task, item):
        self._working -= 1
        if len(self._queue) > 0:
            self._working += 1
            self._enqueue_inner_task_with_except(self.inner_task, self._queue.pop(0))
        self.fail_item(item)

    def fill_ui_task_list(self, task_list):
        self.inner_task.fill_ui_task_list(task_list)

    def __str__(self):
        return "LimitConcurrent({0} x {1} )".format(
            self.concurrency, self.inner_task)


class ConditionalTask(Task):
    '''Runs a task optionally.'''
    def __init__(self, condition_function, inner_task):
        Task.__init__(self, "Conditional")
        self.condition_function = condition_function
        self.inner_task = inner_task
        self.inner_task.on_complete_item += self._inner_task_complete_item
        self.inner_task.on_fail_item += self._inner_task_fail_item

    def enqueue(self, item):
        if self.condition_function(item):
            self._enqueue_inner_task_with_except(self.inner_task, item)
        else:
            item.log_output("Skipping tasks for this item.")
            self.complete_item(item)

    def _inner_task_complete_item(self, task, item):
        self.complete_item(item)

    def _inner_task_fail_item(self, task, item):
        self.fail_item(item)

    def fill_ui_task_list(self, task_list):
        self.inner_task.fill_ui_task_list(task_list)

    def __str__(self):
        return "Conditional(" + str(self.inner_task) + ")"


class SetItemKey(SimpleTask):
    '''Set a value onto a task.'''
    def __init__(self, key, value):
        SimpleTask.__init__(self, "SetItemKey")
        self.key = key
        self.value = value

    def process(self, item):
        item[self.key] = realize(self.value, self)

    def __str__(self):
        return "SetItemKey(" + str(self.key) + ": " + str(self.value) + ")"


class PrintItem(SimpleTask):
    '''Output the name of the :class:`Item`.'''
    def __init__(self):
        SimpleTask.__init__(self, "PrintItem")

    def process(self, item):
        item.log_output("%s\n" % str(item))

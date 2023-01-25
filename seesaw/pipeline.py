import os
import traceback

from seesaw.event import Event
from seesaw.task import Task
from seesaw.item import Item

class Pipeline:
    '''The sequence of steps that complete a :class:`Task`.

    Your pipeline will probably be something like this:

    1. Request an assignment from the tracker.
    2. Run Wget to download the file.
    3. Upload the downloaded file with rsync.
    4. Tell the tracker that the assignment is done.
    '''
    def __init__(self, *tasks: Task):
        self.cwd = os.getcwd()
        self.data_dir = os.path.join(self.cwd, "data")
        self.on_start_item = Event()
        self.on_complete_item = Event()
        self.on_fail_item = Event()
        self.on_cancel_item = Event()
        self.on_finish_item = Event()
        self.on_cleanup = Event()
        self.on_stop_requested = Event()
        self.on_stop_canceled = Event()
        self.project = None

        self.items_in_pipeline = set()
        self.tasks = []
        for task in tasks:
            self.add_task(task)

    def add_task(self, task: Task):
        task.on_complete_item += self._task_complete_item
        task.on_fail_item += self._task_fail_item
        self.tasks.append(task)

    def enqueue(self, item: Item):
        print(f"1/3 enqueuing {self} {item}")
        self.items_in_pipeline.add(item)
        print(f"2/3 enqueuing {self} {item}")
        self.on_start_item(self, item)
        print(f"3/3 enqueuing {self} {item}")
        self._enqueue_with_except(self.tasks[0], item)
        print(f"done enqueuing {self} {item}")

    def _enqueue_with_except(self, task: Task, item: Item):
        task.enqueue(item)
        def handle_item_exception(e_type, e_value, tb):
            item.log_output(f"Failed {task} for {item.description()}\n")
            item.log_output(
                "".join(traceback.format_exception(e_type, e_value, tb))
            )
            item.log_error(self, e_value)
            task.fail_item(item)

            try:
                task.enqueue(item)
            except Exception:
                handle_item_exception()

    def _task_complete_item(self, task: Task, item: Item):
        task_index = self.tasks.index(task)
        if len(self.tasks) <= task_index + 1:
            self._complete_item(item)
        else:
            self._enqueue_with_except(self.tasks[task_index + 1], item)

    def _task_fail_item(self, task, item: Item):
        self._fail_item(item)

    def _cancel_item(self, item):
        if item in self.items_in_pipeline:
            item.cancel()
            self.items_in_pipeline.remove(item)
            self.on_cancel_item(self, item)
            self.on_finish_item(self, item)
        else:
            # XXX: Reaching here indicates a programming problem.
            # Refactoring is required.
            item.log_output(
                'Warning: Ignoring extra cancel event.\n' +
                ''.join(traceback.format_stack()))

    def _complete_item(self, item):
        if item in self.items_in_pipeline:
            item.complete()
            self.items_in_pipeline.remove(item)
            self.on_complete_item(self, item)
            self.on_finish_item(self, item)
        else:
            # See comment above.
            item.log_output(
                'Warning: Ignoring extra complete event.\n' +
                ''.join(traceback.format_stack()))

    def _fail_item(self, item):
        if item in self.items_in_pipeline:
            item.fail()
            self.items_in_pipeline.remove(item)
            self.on_fail_item(self, item)
            self.on_finish_item(self, item)
        else:
            # See comment above.
            item.log_output(
                'Warning: Ignoring extra fail event.\n' +
                ''.join(traceback.format_stack()))

    def cancel_items(self):
        cancel_items = [item for item in self.items_in_pipeline
                        if item.may_be_canceled]

        for item in cancel_items:
            self._cancel_item(item)

    def ui_task_list(self):
        task_list = []
        for task in self.tasks:
            task.fill_ui_task_list(task_list)
        return task_list

    def __str__(self):
        return "Pipeline:\n -> " + ("\n -> ".join(map(str, self.tasks)))

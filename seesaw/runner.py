'''Pipeline execution.'''
import datetime
import functools
import os
import os.path
import sys

import seesaw.util
from seesaw.config import realize
from seesaw.event import Event
from seesaw.item import Item

from tornado import ioloop


class Runner(object):
    '''Executes and manages the lifetime of :class:`Pipeline` instances.'''
    def __init__(self, stop_file=None, concurrent_items=1, max_items=None,
                 keep_data=False):
        self.pipeline = None
        self.concurrent_items = concurrent_items
        self.max_items = max_items
        self.keep_data = keep_data

        self.item_count = 0
        self.active_items = set()
        self.stop_flag = False
        self.stop_file = stop_file
        self.initial_stop_file_mtime = self.stop_file_mtime()

        self.on_status = Event()
        self.on_create_item = Event()
        self.on_pipeline_start_item = Event()
        self.on_pipeline_finish_item = Event()
        self.on_finish = Event()

        if stop_file:
            ioloop.PeriodicCallback(self.check_stop_file, 5000).start()

    def set_current_pipeline(self, pipeline):
        old_pipeline = self.pipeline

        if pipeline:
            pipeline.on_start_item += self._item_starting
            pipeline.on_finish_item += self._item_finished

        self.pipeline = pipeline

        if old_pipeline:
            # stop any cancellable items in the previous pipeline
            old_pipeline.cancel_items()

    def is_active(self):
        return len(self.active_items) > 0

    def start(self):
        self.add_items()

    def stop_gracefully(self):
        print("Stopping when current tasks are completed...")
        self.stop_flag = True
        self.pipeline.cancel_items()
        self.pipeline.on_stop_requested()
        self.initial_stop_file_mtime = self.stop_file_mtime()
        self.on_status(self, "stopping")

    def keep_running(self):
        print("Keep running...")
        self.stop_flag = False
        self.pipeline.on_stop_canceled()
        self.initial_stop_file_mtime = self.stop_file_mtime()
        self.on_status(self, "running")

    def check_stop_file(self):
        if self.stop_file_changed():
            self.stop_gracefully()

    def should_stop(self):
        return self.stop_flag or self.stop_file_changed()

    def stop_file_changed(self):
        current_stop_file_mtime = self.stop_file_mtime()
        if current_stop_file_mtime:
            return self.initial_stop_file_mtime is None \
                or self.initial_stop_file_mtime < current_stop_file_mtime
        else:
            return False

    def stop_file_mtime(self):
        if self.stop_file and os.path.exists(self.stop_file):
            return os.path.getmtime(self.stop_file)
        else:
            return None

    def add_items(self):
        if self.pipeline:
            items_required = int(realize(self.concurrent_items))
            while len(self.active_items) < items_required:
                if self.max_items and self.max_items <= self.item_count:
                    return

                self.item_count += 1
                item_id = "{0}-{1}".format(
                    seesaw.util.unique_id_str(), self.item_count)
                item = Item(
                    pipeline=self.pipeline,
                    item_id=item_id,
                    item_number=self.item_count,
                    keep_data=self.keep_data
                )
                self.on_create_item(self, item)
                self.active_items.add(item)
                self.pipeline.enqueue(item)

    def _item_starting(self, pipeline, item):
        self.on_pipeline_start_item(self, pipeline, item)

    def _item_finished(self, pipeline, item):
        if item.failed:
            item.log_output("Waiting 10 seconds...")
            ioloop.IOLoop.instance().add_timeout(
                datetime.timedelta(seconds=10),
                functools.partial(
                    self._item_finished_without_delay, pipeline, item)
            )
        else:
            self._item_finished_without_delay(pipeline, item)

    def _item_finished_without_delay(self, pipeline, item):
        self.on_pipeline_finish_item(self, pipeline, item)
        self.active_items.remove(item)

        def add_more_items():
            if not self.should_stop():
                self.add_items()

            if len(self.active_items) == 0:
                self.on_finish(self)

        ioloop.IOLoop.instance().add_timeout(
            datetime.timedelta(),
            add_more_items
        )


class SimpleRunner(Runner):
    '''Executes a single class:`Pipeline` instance.'''
    def __init__(self, pipeline, stop_file=None, concurrent_items=1,
                 max_items=None, keep_data=False):
        Runner.__init__(
            self, stop_file=stop_file,
            concurrent_items=concurrent_items, max_items=max_items,
            keep_data=keep_data)

        self.set_current_pipeline(pipeline)
        self.on_create_item += self._handle_create_item
        self.on_finish += self._stop_ioloop

    def start(self):
        Runner.start(self)
        ioloop.IOLoop.instance().start()
        self.pipeline.on_cleanup()

    def _stop_ioloop(self, dummy):
        ioloop.IOLoop.instance().stop()

    def forced_stop(self):
        print("Stopping immediately...")
        # TODO perhaps the subprocesses should be killed
        ioloop.IOLoop.instance().stop()

    def _handle_create_item(self, dummy, item):
        item.on_output += self._handle_item_output

    def _handle_item_output(self, item, data):
        while True:
            try:
                try:
                    sys.stdout.write(data)
                except UnicodeError:
                    sys.stdout.write(data.encode('ascii', 'replace').decode('ascii'))
                sys.stdout.flush()
                return
            except IOError as e:
                # Ignore EINTR errors (which are spurious errors caused by signals) and retry the operation.
                # Allow other errors to propagate up the call stack as normal.
                if e.errno != os.errno.EINTR:
                    raise

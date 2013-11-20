'''Running subprocesses asynchronously.'''
import fcntl
import os
import os.path
import subprocess
import functools
import datetime
import pty

import tornado.ioloop
from tornado.ioloop import IOLoop, PeriodicCallback

from seesaw.event import Event
from seesaw.task import Task
from seesaw.config import realize


class AsyncPopen(object):
    '''Asynchronous version of :class:`subprocess.Popen`.'''
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

        self.on_output = Event()
        self.on_end = Event()

    def run(self):
        self.ioloop = IOLoop.instance()
        (master_fd, slave_fd) = pty.openpty()

        # make stdout, stderr non-blocking
        fcntl.fcntl(master_fd, fcntl.F_SETFL, fcntl.fcntl(master_fd, fcntl.F_GETFL) | os.O_NONBLOCK)

        self.master_fd = master_fd
        self.master = os.fdopen(master_fd)

        # listen to stdout, stderr
        self.ioloop.add_handler(master_fd, self._handle_subprocess_stdout, self.ioloop.READ)

        slave = os.fdopen(slave_fd)
        self.kwargs["stdout"] = slave
        self.kwargs["stderr"] = slave
        self.kwargs["close_fds"] = True
        self.pipe = subprocess.Popen(*self.args, **self.kwargs)

        self.stdin = self.pipe.stdin

        # check for process exit
        self.wait_callback = PeriodicCallback(self._wait_for_end, 250)
        self.wait_callback.start()

    def _handle_subprocess_stdout(self, fd, events):
        if not self.master.closed and (events & IOLoop._EPOLLIN) != 0:
            data = self.master.read()
            self.on_output(data)

        self._wait_for_end(events)

    def _wait_for_end(self, events=0):
        self.pipe.poll()
        if self.pipe.returncode != None or (events & tornado.ioloop.IOLoop._EPOLLHUP) > 0:
            self.wait_callback.stop()
            self.master.close()
            self.ioloop.remove_handler(self.master_fd)
            self.on_end(self.pipe.returncode)


class ExternalProcess(Task):
    '''External subprocess runner.'''
    def __init__(self, name, args, max_tries=1, retry_delay=30, accept_on_exit_code=[0], retry_on_exit_code=None, env=None):
        Task.__init__(self, name)
        self.args = args
        self.max_tries = max_tries
        self.retry_delay = retry_delay
        self.accept_on_exit_code = accept_on_exit_code
        self.retry_on_exit_code = retry_on_exit_code
        self.env = env

    def enqueue(self, item):
        self.start_item(item)
        item.log_output("Starting %s for %s\n" % (self, item.description()))
        item["tries"] = 0
        self.process(item)

    def stdin_data(self, item):
        return ""

    def process(self, item):
        with self.task_cwd():
            p = AsyncPopen(
                args=realize(self.args, item),
                env=realize(self.env, item),
                stdin=subprocess.PIPE,
                close_fds=True
            )

            p.on_output += functools.partial(self.on_subprocess_stdout, p, item)
            p.on_end += functools.partial(self.on_subprocess_end, item)

            p.run()

            p.stdin.write(self.stdin_data(item))
            p.stdin.close()

    def on_subprocess_stdout(self, pipe, item, data):
        item.log_output(data, full_line=False)

    def on_subprocess_end(self, item, returncode):
        if returncode in self.accept_on_exit_code:
            self.handle_process_result(returncode, item)
        else:
            self.handle_process_error(returncode, item)

    def handle_process_result(self, exit_code, item):
        item.log_output("Finished %s for %s\n" % (self, item.description()))
        self.complete_item(item)

    def handle_process_error(self, exit_code, item):
        item["tries"] += 1

        item.log_output("Process %s returned exit code %d for %s\n" % (self, exit_code, item.description()))
        item.log_error(self, exit_code)

        if (self.max_tries == None or item["tries"] < self.max_tries) and (self.retry_on_exit_code == None or exit_code in self.retry_on_exit_code):
            item.log_output("Retrying %s for %s after %d seconds...\n" % (self, item.description(), self.retry_delay))
            IOLoop.instance().add_timeout(datetime.timedelta(seconds=self.retry_delay),
                functools.partial(self.process, item))

        else:
            item.log_output("Failed %s for %s\n" % (self, item.description()))
            self.fail_item(item)


class WgetDownload(ExternalProcess):
    '''Download with Wget process runner.'''
    def __init__(self, args, max_tries=1, accept_on_exit_code=[0], retry_on_exit_code=None, env=None, stdin_data_function=None):
        ExternalProcess.__init__(self, "WgetDownload",
            args=args, max_tries=max_tries,
            accept_on_exit_code=accept_on_exit_code,
            retry_on_exit_code=retry_on_exit_code,
            env=env)
        self.stdin_data_function = stdin_data_function

    def stdin_data(self, item):
        if self.stdin_data_function:
            return self.stdin_data_function(item)
        else:
            return ""


class RsyncUpload(ExternalProcess):
    '''Upload with Rsync process runner.'''
    def __init__(self, target, files, target_source_path="./", bwlimit="0", max_tries=None, extra_args=[]):
        args = [
          "rsync",
          "-avz",
          "--compress-level=9",
          "--timeout=300",
          "--contimeout=300",
          "--progress",
          "--bwlimit", bwlimit
        ]
        if extra_args:
            args.extend(extra_args)
        args.extend([
          "--files-from=-",
          target_source_path,
          target
        ])
        ExternalProcess.__init__(self, "RsyncUpload",
            args=args,
            max_tries=max_tries)
        self.files = files
        self.target_source_path = target_source_path

    def stdin_data(self, item):
        return "".join(["%s\n" % os.path.relpath(realize(f, item), realize(self.target_source_path, item)) for f in self.files])


class CurlUpload(ExternalProcess):
    '''Upload with Curl process runner.'''
    def __init__(self, target, filename, connect_timeout="60", speed_limit="1", speed_time="900", max_tries=None):
        args = [
          "curl",
          "--fail",
          "--output", "/dev/null",
          "--connect-timeout", str(connect_timeout),
          "--speed-limit", str(speed_limit),  # minimum upload speed 1B/s
          "--speed-time", str(speed_time),  # stop if speed < speed-limit for 900 seconds
          "--header", "X-Curl-Limits: inf,%s,%s" % (str(speed_limit), str(speed_time)),
          "--write-out", "Upload server: %{url_effective}\\n",
          "--location",
          "--upload-file", filename,
          target
        ]
        ExternalProcess.__init__(self, "CurlUpload",
            args=args,
            max_tries=max_tries)

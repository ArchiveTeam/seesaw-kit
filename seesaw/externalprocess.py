'''Running subprocesses asynchronously.'''
from __future__ import print_function

import fcntl
import os
import os.path
import subprocess
import functools
import datetime
import pty
import signal
import atexit

import tornado.ioloop
from tornado.ioloop import IOLoop, PeriodicCallback
import tornado.process

from seesaw.event import Event
from seesaw.task import Task
from seesaw.config import realize
import time


_all_procs = set()


@atexit.register
def cleanup():
    if _all_procs:
        print('Subprocess did not exit properly!')

    for proc in _all_procs:
        print('Killing', proc)

        try:
            if hasattr(proc, 'proc'):
                proc.proc.terminate()
            else:
                proc.terminate()
        except Exception as error:
            print(error)

        time.sleep(0.1)

        try:
            if hasattr(proc, 'proc'):
                proc.proc.kill()
            else:
                proc.kill()
        except Exception as error:
            print(error)


class AsyncPopen(object):
    '''Asynchronous version of :class:`subprocess.Popen`.

    Deprecated.
    '''
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.ioloop = None
        self.master_fd = None
        self.master = None
        self.pipe = None
        self.stdin = None

        self.on_output = Event()
        self.on_end = Event()

    @classmethod
    def ignore_sigint(cls):
        # http://stackoverflow.com/q/5045771/1524507
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        os.setpgrp()

    def run(self):
        self.ioloop = IOLoop.instance()
        (master_fd, slave_fd) = pty.openpty()

        # make stdout, stderr non-blocking
        fcntl.fcntl(master_fd, fcntl.F_SETFL,
                    fcntl.fcntl(master_fd, fcntl.F_GETFL) | os.O_NONBLOCK)

        self.master_fd = master_fd
        self.master = os.fdopen(master_fd)

        # listen to stdout, stderr
        self.ioloop.add_handler(master_fd, self._handle_subprocess_stdout,
                                self.ioloop.READ)

        slave = os.fdopen(slave_fd)
        self.kwargs["stdout"] = slave
        self.kwargs["stderr"] = slave
        self.kwargs["close_fds"] = True
        self.kwargs["preexec_fn"] = self.ignore_sigint
        self.pipe = subprocess.Popen(*self.args, **self.kwargs)

        self.stdin = self.pipe.stdin

        # check for process exit
        self.wait_callback = PeriodicCallback(self._wait_for_end, 250)
        self.wait_callback.start()

        _all_procs.add(self.pipe)

    def _handle_subprocess_stdout(self, fd, events):
        if not self.master.closed and (events & IOLoop._EPOLLIN) != 0:
            data = self.master.read()
            self.on_output(data)

        self._wait_for_end(events)

    def _wait_for_end(self, events=0):
        self.pipe.poll()
        if self.pipe.returncode is not None or \
                (events & tornado.ioloop.IOLoop._EPOLLHUP) > 0:
            self.wait_callback.stop()
            self.master.close()
            self.ioloop.remove_handler(self.master_fd)
            self.on_end(self.pipe.returncode)
            _all_procs.remove(self.pipe)


class AsyncPopen2(object):
    '''Adapter for the legacy AsyncPopen'''

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

        self.on_output = Event()
        self.on_end = Event()

        self.pipe = None

    def run(self):
        self.kwargs["stdout"] = tornado.process.Subprocess.STREAM
        self.kwargs["stderr"] = tornado.process.Subprocess.STREAM
        self.kwargs["preexec_fn"] = AsyncPopen.ignore_sigint

        self.pipe = tornado.process.Subprocess(*self.args, **self.kwargs)

        self.pipe.stdout.read_until_close(
            callback=self._handle_subprocess_stdout,
            streaming_callback=self._handle_subprocess_stdout)
        self.pipe.stderr.read_until_close(
            callback=self._handle_subprocess_stdout,
            streaming_callback=self._handle_subprocess_stdout)

        self.pipe.set_exit_callback(self._end_callback)
        _all_procs.add(self.pipe)

    def _handle_subprocess_stdout(self, data):
        self.on_output(data)

    def _end_callback(self, return_code):
        self.on_end(return_code)
        _all_procs.remove(self.pipe)

    @property
    def stdin(self):
        return self.pipe.stdin


class ExternalProcess(Task):
    '''External subprocess runner.'''
    def __init__(self, name, args, max_tries=1, retry_delay=2,
                 accept_on_exit_code=None, retry_on_exit_code=None, env=None):
        Task.__init__(self, name)
        self.args = args
        self.max_tries = max_tries
        self.retry_delay = retry_delay
        if accept_on_exit_code is not None:
            self.accept_on_exit_code = accept_on_exit_code
        else:
            self.accept_on_exit_code = [0]
        self.retry_on_exit_code = retry_on_exit_code
        self.env = env or {}

        if 'PYTHONIOENCODING' not in self.env:
            self.env['PYTHONIOENCODING'] = 'utf8:replace'

    def enqueue(self, item):
        self.start_item(item)
        item.log_output("Starting %s for %s\n" % (self, item.description()))
        item["tries"] = 0
        item["ExternalProcess.stdin_write_error"] = False
        item["ExternalProcess.running"] = False
        self.process(item)

    def stdin_data(self, item):
        return b""

    def process(self, item):
        with self.task_cwd():
            p = AsyncPopen2(
                args=realize(self.args, item),
                env=realize(self.env, item),
                stdin=subprocess.PIPE,
                close_fds=True
            )

            p.on_output += functools.partial(self.on_subprocess_stdout, p,
                                             item)
            p.on_end += functools.partial(self.on_subprocess_end, item)

            p.run()
            item["ExternalProcess.running"] = True

            try:
                p.stdin.write(self.stdin_data(item))
            except Exception as error:
                # FIXME: We need to properly propagate errors
                item.log_output("Error writing to process: %s" % str(error))
                item["ExternalProcess.stdin_write_error"] = True

            p.stdin.close()

    def fail_item(self, item):
        # Don't allow the item to fail until the external process completes
        if item["ExternalProcess.running"]:
            return
        Task.fail_item(self, item)

    def on_subprocess_stdout(self, pipe, item, data):
        item.log_output(data, full_line=False)

    def on_subprocess_end(self, item, returncode):
        item["ExternalProcess.running"] = False
        if returncode in self.accept_on_exit_code and \
                not item["ExternalProcess.stdin_write_error"]:
            self.handle_process_result(returncode, item)
        else:
            self.handle_process_error(returncode, item)

    def handle_process_result(self, exit_code, item):
        item.log_output("Finished %s for %s\n" % (self, item.description()))
        self.complete_item(item)

    def handle_process_error(self, exit_code, item):
        item["tries"] += 1

        item.log_output(
            "Process %s returned exit code %d for %s\n" %
            (self, exit_code, item.description())
        )
        item.log_error(self, exit_code)

        retry_acceptable = self.max_tries is None or \
            item["tries"] < self.max_tries
        exit_status_indicates_retry = self.retry_on_exit_code is None or \
            exit_code in self.retry_on_exit_code or \
            item["ExternalProcess.stdin_write_error"]

        if retry_acceptable and exit_status_indicates_retry:
            item.log_output(
                "Retrying %s for %s after %d seconds...\n" %
                (self, item.description(), self.retry_delay)
            )
            IOLoop.instance().add_timeout(
                datetime.timedelta(seconds=self.retry_delay),
                functools.partial(self.process, item)
            )

        else:
            item.log_output("Failed %s for %s\n" % (self, item.description()))
            self.fail_item(item)


class WgetDownload(ExternalProcess):
    '''Download with Wget process runner.'''
    def __init__(self, args, max_tries=1, accept_on_exit_code=None,
                 retry_on_exit_code=None, env=None, stdin_data_function=None):
        ExternalProcess.__init__(
            self, "WgetDownload",
            args=args, max_tries=max_tries,
            accept_on_exit_code=(accept_on_exit_code
                                 if accept_on_exit_code is not None else [0]),
            retry_on_exit_code=retry_on_exit_code,
            env=env)
        self.stdin_data_function = stdin_data_function

    def stdin_data(self, item):
        if self.stdin_data_function:
            return self.stdin_data_function(item)
        else:
            return b""


class RsyncUpload(ExternalProcess):
    '''Upload with Rsync process runner.'''
    def __init__(self, target, files, target_source_path="./", bwlimit="0",
                 max_tries=None, extra_args=None):
        args = [
            "rsync",
            "-rltv",
            "--timeout=300",
            "--contimeout=300",
            "--progress",
            "--bwlimit", bwlimit
        ]
        if extra_args is not None:
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
        return "".join(
            [
                "%s\n" % os.path.relpath(
                    realize(f, item),
                    realize(self.target_source_path, item)
                )
                for f in realize(self.files, item)
            ]).encode('utf-8')


class CurlUpload(ExternalProcess):
    '''Upload with Curl process runner.'''
    def __init__(self, target, filename, connect_timeout="60", speed_limit="1",
                 speed_time="900", max_tries=None):
        args = [
            "curl",
            "--fail",
            "--output", "/dev/null",
            "--connect-timeout", str(connect_timeout),
            "--speed-limit", str(speed_limit),  # minimum upload speed 1B/s
            # stop if speed < speed-limit for 900 seconds
            "--speed-time", str(speed_time),
            "--header", "X-Curl-Limits: inf,%s,%s" % (str(speed_limit),
                                                      str(speed_time)),
            "--write-out", "Upload server: %{url_effective}\\n",
            "--location",
            "--upload-file", filename,
            target
        ]
        ExternalProcess.__init__(self, "CurlUpload",
                                 args=args,
                                 max_tries=max_tries)

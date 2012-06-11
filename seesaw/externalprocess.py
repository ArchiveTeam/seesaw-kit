import fcntl
import os
import os.path
import subprocess
import functools
import datetime
import pty

import tornado.ioloop
from tornado.ioloop import IOLoop

from .task import Task
from .item import realize

class ExternalProcess(Task):
  def __init__(self, name, args, max_tries=1, retry_delay=30, accept_on_exit_code=[0], retry_on_exit_code=None, env=None):
    Task.__init__(self, name)
    self.args = args
    self.max_tries = max_tries
    self.retry_delay = retry_delay
    self.accept_on_exit_code = accept_on_exit_code
    self.retry_on_exit_code = retry_on_exit_code
    self.env = env

  def enqueue(self, item):
    item.output_collector.append("Starting %s for %s\n" % (self, item.description()))
    item["tries"] = 1
    self.process(item)

  def stdin_data(self, item):
    return ""

  def process(self, item):
    i = IOLoop.instance()
    (master_fd, slave_fd) = pty.openpty()
    slave = os.fdopen(slave_fd)
    p = subprocess.Popen(
        args=realize(self.args, item),
        env=realize(self.env, item),
        stdin=subprocess.PIPE,
        stdout=slave,
        stderr=slave,
        close_fds=True
    )
    p.stdin.write(self.stdin_data(item))
    p.stdin.close()

    # make stdout, stderr non-blocking
    fcntl.fcntl(master_fd, fcntl.F_SETFL, fcntl.fcntl(master_fd, fcntl.F_GETFL) | os.O_NONBLOCK)

    i.add_handler(master_fd,
        functools.partial(self.on_subprocess_stdout, os.fdopen(master_fd), i, p, item),
        i.READ)

  def on_subprocess_stdout(self, m, ioloop, pipe, item, fd, events):
    if not m.closed and (events & tornado.ioloop.IOLoop._EPOLLIN) != 0:
      data = m.read()
      if item.output_collector:
        item.output_collector.append(data)

    if (events & tornado.ioloop.IOLoop._EPOLLHUP) > 0:
      m.close()
      ioloop.remove_handler(fd)
      self.wait_for_end(ioloop, pipe, item)

  def wait_for_end(self, ioloop, pipe, item):
    pipe.poll()
    if pipe.returncode != None:
      if pipe.returncode in self.accept_on_exit_code:
        self.handle_process_result(pipe.returncode, item)
      else:
        self.handle_process_error(pipe.returncode, item)
    else:
      # wait for process to exit
      ioloop.add_timeout(datetime.timedelta(milliseconds=250),
          functools.partial(self.wait_for_end, ioloop, pipe, item))

  def handle_process_result(self, exit_code, item):
    item.output_collector.append("Finished %s for %s\n" % (self, item.description()))
    if self.on_complete:
      self.on_complete(item)

  def handle_process_error(self, exit_code, item):
    item["tries"] += 1
    item.log_error(self, exit_code)

    item.output_collector.append("Process %s returned exit code %d for %s\n" % (self, exit_code, item.description()))

    if (self.max_tries == None or item["tries"] < self.max_tries) and (self.retry_on_exit_code == None or exit_code in self.retry_on_exit_code):
      item.output_collector.append("Retrying %s for %s after %d seconds...\n" % (self, item.description(), self.retry_delay))
      IOLoop.instance().add_timeout(datetime.timedelta(seconds=self.retry_delay),
          functools.partial(self.process, item))
    elif self.on_error:
      item.failed = True
      item.output_collector.append("Failed %s for %s\n" % (self, item.description()))
      self.on_error(item)

class WgetDownload(ExternalProcess):
  def __init__(self, args, max_tries=1, accept_on_exit_code=[0], retry_on_exit_code=None, env=None):
    ExternalProcess.__init__(self, "WgetDownload",
        args=args, max_tries=max_tries,
        accept_on_exit_code=accept_on_exit_code,
        retry_on_exit_code=retry_on_exit_code,
        env=env)

class RsyncUpload(ExternalProcess):
  def __init__(self, target, files, target_source_path="./", bwlimit="0", max_tries=None):
    ExternalProcess.__init__(self, "RsyncUpload",
        args=[ "rsync",
               "-avz",
               "--compress-level=9",
               "--progress",
               "--bwlimit", bwlimit,
               "--files-from=-",
               target_source_path,
               target
             ],
        max_tries = max_tries)
    self.files = files
    self.target_source_path = target_source_path

  def stdin_data(self, item):
    return "".join([ "%s\n" % os.path.relpath(realize(f, item), self.target_source_path) for f in self.files ])



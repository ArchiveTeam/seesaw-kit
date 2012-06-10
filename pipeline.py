
import sys
import traceback

class StringOutputCollector(object):
  def __init__(self):
    self.parts = []

  def append(self, data):
    self.parts.append(data)

  def __str__(self):
    return "".join(self.parts)

class StdoutOutputCollector(object):
  def __init__(self):
    self.parts = []

  def append(self, data):
    sys.stdout.write(data)
    self.parts.append(data)

  def __str__(self):
    return "".join(self.parts)

class Item(dict):
  def __init__(self, *args):
    dict.__init__(self, *args)
    self.failed = False
    self.errors = []
    self.output_collector = StringOutputCollector()

  def log_error(self, task, *args):
    self.errors.append((task, args))

  def description(self):
    if "item_name" in self:
      if self["item_name"]:
        return "item '%s'" % str(self["item_name"])
      else:
        return "new item"
    else:
      return "item %d" % id(self)

  def __str__(self):
    s = "Item " + ("FAILED " if self.failed else "") + dict.__str__(self) 
    for err in self.errors:
      for e in err[1]:
        if isinstance(e, Exception):
          s += "%s\n" % traceback.format_exception(e)
        else:
          s += "%s\n" % str(e)
      s += "\n  " + str(err)
    return s

def realize(v, item):
  if isinstance(v, dict):
    realized_dict = {}
    for (key, value) in v.iteritems():
      realized_dict[key] = realize(value, item)
    return realized_dict
  elif isinstance(v, list):
    return [ realize(vi, item) for vi in v ]
  elif hasattr(v, "realize"):
    return v.realize(item)
  else:
    return v

class ItemValue(object):
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
      raise Exception("Attempting to fill "+str(type(self)))

  def __str__(self):
    return "<" + self.key + ">"

class ItemInterpolation(object):
  def __init__(self, s):
    self.s = s

  def realize(self, item):
    return self.s % item

  def __str__(self):
    return "<'" + self.s + "'>"

class ConfigValue(object):
  def __init__(self, name="", default=None):
    self.name = name
    self.value = default

  def realize(self, ignored):
    return self.value

  def __str__(self):
    return "<" + self.name + ":" + str(self.value) + ">"

class Task(object):
  def __init__(self, name):
    self.name = name
    self.prev_task = None
    self.on_complete = None
    self.on_error = None

  def __str__(self):
    return self.name

class SimpleTask(Task):
  def __init__(self, name):
    Task.__init__(self, name)

  def enqueue(self, item):
    item.output_collector.append("Starting %s for %s\n" % (self, item.description()))
    try:
      self.process(item)
    except Exception, e:
      item.log_error(e)
      item.failed = True
      item.output_collector.append("Failed %s for %s\n" % (self, item.description()))
      if self.on_error:
        self.on_error(item)
    else:
      item.output_collector.append("Finished %s for %s\n" % (self, item.description()))
      if self.on_complete:
        self.on_complete(item)

  def process(self, item):
    pass

  def __str__(self):
    return self.name

class LimitConcurrent(Task):
  def __init__(self, concurrency, inner_task):
    Task.__init__(self, "LimitConcurrent")
    self.concurrency = concurrency
    self.inner_task = inner_task
    self.inner_task.on_complete = self.on_inner_task_complete
    self.inner_task.on_error = self.on_inner_task_error
    self.queue = []
    self.working = 0

  def enqueue(self, item):
    if self.working < realize(self.concurrency, item):
      self.working += 1
      self.inner_task.enqueue(item)
    else:
      self.queue.append(item)
  
  def on_inner_task_complete(self, item):
    self.working -= 1
    if len(self.queue) > 0:
      self.working += 1
      self.inner_task.enqueue(self.queue.pop(0))
    if self.on_complete:
      self.on_complete(item)
  
  def on_inner_task_error(self, item):
    self.working -= 1
    if len(self.queue) > 0:
      self.working += 1
      self.inner_task.enqueue(self.queue.pop(0))
    if self.on_error:
      self.on_error(item)

  def __str__(self):
    return "LimitConcurrent(" + str(self.concurrency) + " x " + str(self.inner_task) + ")"

class Pipeline(object):
  def __init__(self, *tasks):
    self.on_complete = None
    self.on_error = None
    self.tasks = []
    self.working = 0
    for task in tasks:
      self.add_task(task)

  def add_task(self, task):
    task.on_complete = self.fire_on_complete
    task.on_error = self.fire_on_error
    if len(self.tasks) > 0:
      self.tasks[-1].on_complete = task.enqueue
      task.prev_task = self.tasks[-1]
    self.tasks.append(task)

  def enqueue(self, item):
    self.working += 1
    self.tasks[0].enqueue(item)

  def fire_on_complete(self, item):
    self.working -= 1
    if self.on_complete:
      self.on_complete(item)

  def fire_on_error(self, item):
    self.working -= 1
    if self.on_error:
      self.on_error(item)

  def __str__(self):
    return "Pipeline:\n -> " + ("\n -> ".join(map(str, self.tasks)))

class SetItemKey(SimpleTask):
  def __init__(self, key, value):
    SimpleTask.__init__(self, "SetItemKey")
    self.key = key
    self.value = value

  def process(self, item):
    item[self.key] = self.value

  def __str__(self):
    return "SetItemKey(" + str(self.key) + ": " + str(self.value) + ")"

class PrintItem(SimpleTask):
  def __init__(self):
    SimpleTask.__init__(self, "PrintItem")

  def process(self, item):
    print item


from twisted.internet import utils, reactor, protocol
from twisted.internet.defer import Deferred
from twisted.web.client import Agent, ResponseDone
from twisted.web.http_headers import Headers
from stringprod import StringProducer
import sys
import json

class ExternalProcessProtocol(protocol.ProcessProtocol):
  def __init__(self, output_collector, accept_on_exit_code, stdin_data=""):
    self.deferred = Deferred()
    self.output_collector = output_collector
    self.accept_on_exit_code = accept_on_exit_code
    self.stdin_data = stdin_data

  def connectionMade(self):
    self.transport.write(self.stdin_data)
    self.transport.closeStdin()

  def outReceived(self, data):
    if self.output_collector:
      self.output_collector.append(data)

  def errReceived(self, data):
    if self.output_collector:
      self.output_collector.append(data)

  def processEnded(self, status):
    rc = status.value.exitCode
    if rc in self.accept_on_exit_code:
      self.deferred.callback(status.value.exitCode)
    else:
      self.deferred.errback(status)

class ExternalProcess(Task):
  def __init__(self, name, args, max_tries=1, retry_delay=30, accept_on_exit_code=[0], retry_on_exit_code=None, env=None, usePTY=False):
    Task.__init__(self, name)
    self.args = args
    self.max_tries = max_tries
    self.retry_delay = retry_delay
    self.accept_on_exit_code = accept_on_exit_code
    self.retry_on_exit_code = retry_on_exit_code
    self.env = env
    self.usePTY = usePTY

  def enqueue(self, item):
    item.output_collector.append("Starting %s for %s\n" % (self, item.description()))
    item["tries"] = 1
    self.process(item)

  def stdin_data(self, item):
    return ""

  def process(self, item):
    pp = ExternalProcessProtocol(item.output_collector, self.accept_on_exit_code, self.stdin_data(item))
    reactor.spawnProcess(pp,
        self.args[0],
        args = realize(self.args, item),
        env=realize(self.env, item),
        usePTY=self.usePTY)
    pp.deferred.addCallback(self.handle_process_result, item)
    pp.deferred.addErrback(self.handle_process_error, item)

  def handle_process_result(self, exit_code, item):
    item.output_collector.append("Finished %s for %s\n" % (self, item.description()))
    if self.on_complete:
      self.on_complete(item)

  def handle_process_error(self, status, item):
    item["tries"] += 1
    item.log_error(self, status)

    item.output_collector.append("Process %s returned exit code %d for %s\n" % (self, status.value.exitCode, item.description()))

    if (self.max_tries == None or item["tries"] < self.max_tries) and (self.retry_on_exit_code == None or status.value.exitCode in self.retry_on_exit_code):
      item.output_collector.append("Retrying %s for %s after %d seconds...\n" % (self, item.description(), self.retry_delay))
      reactor.callLater(self.retry_delay, self.process, item)
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
        env=env,
        usePTY=True)

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

class TrackerRequestProtocol(protocol.Protocol):
  def __init__(self, finished):
    self.finished = finished
    self.data = []
    self.remaining = 1024 * 10

  def dataReceived(self, bytes):
    if self.remaining:
      display = bytes[:self.remaining]
      self.data.append(display)
      self.remaining -= len(display)

  def connectionLost(self, reason):
    if isinstance(reason.value, ResponseDone):
      self.finished.callback("".join(self.data))
    else:
      self.finished.errback(reason)

class TrackerRequest(Task):
  def __init__(self, name, tracker_url, tracker_command):
    Task.__init__(self, name)
    self.agent = Agent(reactor)
    self.tracker_url = tracker_url
    self.tracker_command = tracker_command
    self.retry_delay = 30

  def enqueue(self, item):
    item.output_collector.append("Starting %s for %s\n" % (self, item.description()))
    self.send_request(item)

  def send_request(self, item):
    d = self.agent.request(
          "POST",
          "%s/%s" % (self.tracker_url, self.tracker_command),
          Headers({"Content-Type": ["application/json"]}),
          StringProducer(json.dumps(self.data(item)))
        )
    d.addCallback(self.handle_response, item)
    d.addErrback(self.handle_error, item)

  def data(self, item):
    return {}

  def handle_response(self, response, item):
    if response.code == 200:
      finished = Deferred()
      response.deliverBody(TrackerRequestProtocol(finished))
      finished.addBoth(self.handle_body, item)
    else:
      if response.code == 420:
        item.output_collector.append("Tracker rate limiting is in effect. Retrying after %d seconds...\n" % (self.retry_delay))
      elif response.code == 404:
        item.output_collector.append("No item received. Retrying after %d seconds...\n" % (self.retry_delay))
      else:
        item.output_collector.append("Tracker returned status code %d. Retrying after %d seconds...\n" % (response.code, self.retry_delay))
      reactor.callLater(self.retry_delay, self.send_request, item)

  def handle_error(self, response, item):
    item.log_error(self, response)

    item.output_collector.append("Problem contacting tracker: %s\nRetrying after %d seconds...\n" % (response, self.retry_delay))
    reactor.callLater(self.retry_delay, self.send_request, item)

  def handle_body(self, body, item):
    if isinstance(body, str):
      if self.process_body(body, item):
        if self.on_complete:
          self.on_complete(item)
        else:
          item.output_collector.append("Retrying after %d seconds...\n" % (body, self.retry_delay))
          reactor.callLater(self.retry_delay, self.send_request, item)
      else:
        item.output_collector.append("Problem reading tracker response: %s\nRetrying after %d seconds...\n" % (body, self.retry_delay))
        reactor.callLater(self.retry_delay, self.send_request, item)

class GetItemFromTracker(TrackerRequest):
  def __init__(self, tracker_url, downloader):
    TrackerRequest.__init__(self, "GetItemFromTracker", tracker_url, "request")
    self.downloader = downloader

  def data(self, item):
    return {"downloader": realize(self.downloader, item)}

  def process_body(self, body, item):
    if len(body.strip()) > 0:
      item["item_name"] = body.strip()
      item.output_collector.append("Received item '%s' from tracker\n" % item["item_name"])
      return True
    else:
      item.output_collector.append("Tracker responded with empty response.\n")
      return False

class SendDoneToTracker(TrackerRequest):
  def __init__(self, tracker_url, stats):
    TrackerRequest.__init__(self, "SendDoneToTracker", tracker_url, "done")
    self.stats = stats

  def data(self, item):
    return realize(self.stats, item)

  def process_body(self, body, item):
    if body.strip()=="OK":
      item.output_collector.append("Tracker confirmed item '%s'.\n" % item["item_name"])
      return True
    else:
      item.output_collector.append("Tracker responded with unexpected '%s'.\n" % body.strip())
      return False



DATA_DIR = "data"
USER_AGENT = "Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US) AppleWebKit/533.20.25 (KHTML, like Gecko) Version/5.0.4 Safari/533.20.27"
VERSION = "20120603.01"

downloader = "testuser"

import time
import os
import os.path
import shutil
import re

class PrepareDirectories(SimpleTask):
  def __init__(self):
    SimpleTask.__init__(self, "PrepareDirectories")

  def process(self, item):
    item_id8 = "%08d" % int(item["item_name"])
    dirname = "/".join(( DATA_DIR, item_id8[7:8], item_id8[6:8], item_id8[5:8], item_id8 ))

    if os.path.isdir(dirname):
      shutil.rmtree(dirname)

    os.makedirs(dirname + "/files")

    item["item_dir"] = dirname
    item["warc_file_base"] = "picplz-%s-%s" % (item_id8, time.strftime("%Y%m%d-%H%M%S"))

class PrepareStats(SimpleTask):
  def __init__(self, defaults={}, file_groups={}):
    SimpleTask.__init__(self, "PrepareStats")
    self.defaults = defaults
    self.file_groups = file_groups

  def process(self, item):
    total_bytes = {}
    for (group, files) in self.file_groups.iteritems():
      total_bytes[group] = sum([ os.path.getsize(realize(f, item)) for f in files])

    stats = {
        "item": item["item_name"],
        "bytes": total_bytes
    }
    stats.update(self.defaults)

    item["stats"] = stats



pipeline = Pipeline(
  GetItemFromTracker("http://localhost:9292/example", downloader),
# SetItemKey("item_name", "1083030"),
# PrintItem(),
  PrepareDirectories(),
# PrintItem(),
  LimitConcurrent(4,
    WgetDownload([ "./wget-warc-lua",
      "-U", USER_AGENT,
      "-nv",
      "-o", ItemInterpolation("%(item_dir)s/wget.log"),
      "--lua-script", "picplz-user.lua",
      "--directory-prefix", ItemInterpolation("%(item_dir)s/files"),
      "--force-directories",
      "-e", "robots=off",
      "--page-requisites", "--span-hosts",
      "--warc-file", ItemInterpolation("%(item_dir)s/%(warc_file_base)s"),
      "--warc-header", "operator: Archive Team",
      "--warc-header", "picplz-dld-script-version: " + VERSION,
      "--warc-header", ItemInterpolation("picplz-user-id: %(item_name)s"),
      ItemInterpolation("http://api.picplz.com/api/v2/user.json?id=%(item_name)s&include_detail=1&include_pics=1&pic_page_size=100")
    ],
    max_tries = 2,
    retry_on_exit_code = [ 1 ],
    env = { "picplz_lua_json": ItemInterpolation("%(item_dir)s/%(warc_file_base)s.json") })
  ),
# PrintItem(),
  PrepareStats(
    defaults = { "downloader": downloader, "version": VERSION },
    file_groups = {
      "data": [ ItemInterpolation("%(item_dir)s/%(warc_file_base)s.warc.gz") ]
    }
  ),
  LimitConcurrent(1,
    RsyncUpload(
      target = "localhost::tabblo/%s/" % downloader,
      target_source_path = "data/",
      files = [
        ItemInterpolation("%(item_dir)s/%(warc_file_base)s.warc.gz"),
        ItemInterpolation("%(item_dir)s/%(warc_file_base)s.json")
      ],
      bwlimit=ConfigValue(name="Rsync bwlimit", default="1000000")
    ),
  ),
  SendDoneToTracker(
    tracker_url = "http://127.0.0.1:9292/example",
    stats = ItemValue("stats")
  )
)


def item_complete(item):
  print "Complete:", item.description()
# print str(item)
  if pipeline.working == 0:
    reactor.stop()
# else:
#   pipeline.enqueue(Item({"n":Item.n}))
#   Item.n += 1

def item_error(item):
  print "Failed:", item.description()
# print
# print
# print str(item)
# print
# print
# print str(item.output_collector)
# print pipeline.working
  if pipeline.working == 0:
    reactor.stop()

pipeline.on_complete = item_complete
pipeline.on_error = item_error
print pipeline

print
Item.n = 1

item = Item({"n":Item.n, "item_name":None})
item.output_collector = StdoutOutputCollector()
# item.output_collector = StringOutputCollector()
pipeline.enqueue(item)
Item.n += 1

item = Item({"n":Item.n, "item_name":None})
item.output_collector = StdoutOutputCollector()
# item.output_collector = StringOutputCollector()
pipeline.enqueue(item)
Item.n += 1

reactor.run()



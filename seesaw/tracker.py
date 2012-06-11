import json
import functools
import datetime
import os.path

from tornado.httpclient import AsyncHTTPClient, HTTPRequest

from .item import realize
from .task import Task, SimpleTask

class TrackerRequest(Task):
  def __init__(self, name, tracker_url, tracker_command):
    Task.__init__(self, name)
    self.http_client = AsyncHTTPClient()
    self.tracker_url = tracker_url
    self.tracker_command = tracker_command
    self.retry_delay = 30

  def enqueue(self, item):
    item.output_collector.append("Starting %s for %s\n" % (self, item.description()))
    self.send_request(item)

  def send_request(self, item):
    self.http_client.fetch(HTTPRequest(
        "%s/%s" % (self.tracker_url, self.tracker_command),
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps(self.data(item))
      ), functools.partial(self.handle_response, item))

  def data(self, item):
    return {}

  def handle_response(self, item, response):
    if response.code == 200:
      if self.process_body(response.body, item):
        if self.on_complete:
          self.on_complete(item)
        return
    else:
      if response.code == 420:
        item.output_collector.append("Tracker rate limiting is in effect. ")
      elif response.code == 404:
        item.output_collector.append("No item received. ")
      elif response.code == 599:
        item.output_collector.append("No HTTP response received from tracker. ")
      else:
        item.output_collector.append("Tracker returned status code %d. \n" % (response.code))
    item.output_collector.append("Retrying after %d seconds...\n" % (self.retry_delay))
    IOLoop.instance().add_timeout(datetime.timedelta(seconds=self.retry_delay),
        functools.partial(self.send_request, item))

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

class PrepareStatsForTracker(SimpleTask):
  def __init__(self, defaults={}, file_groups={}, id_function=None):
    SimpleTask.__init__(self, "PrepareStatsForTracker")
    self.defaults = defaults
    self.file_groups = file_groups
    self.id_function = id_function

  def process(self, item):
    total_bytes = {}
    for (group, files) in self.file_groups.iteritems():
      total_bytes[group] = sum([ os.path.getsize(realize(f, item)) for f in files])

    stats = {}
    stats.update(self.defaults)
    stats["item"] = item["item_name"]
    stats["bytes"] = total_bytes

    if self.id_function:
      stats["id"] = self.id_function(item)

    item["stats"] = stats


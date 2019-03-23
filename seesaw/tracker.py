'''Contacting the work unit server.

A Tracker refers to the Universal Tracker
(https://github.com/ArchiveTeam/universal-tracker).
'''
import json
import functools
import datetime
import os.path
import re

from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.ioloop import IOLoop

import seesaw
from seesaw.config import realize
from seesaw.task import Task, SimpleTask
from seesaw.externalprocess import RsyncUpload, CurlUpload
import seesaw.six


class TrackerRequest(Task):
    '''Represents a request to a Tracker.'''

    DEFAULT_RETRY_DELAY = 60

    def __init__(self, name, tracker_url, tracker_command,
                 may_be_canceled=False):
        Task.__init__(self, name)
        self.http_client = AsyncHTTPClient()
        self.tracker_url = tracker_url
        self.tracker_command = tracker_command
        self.retry_delay = self.DEFAULT_RETRY_DELAY
        self._set_may_be_canceled = may_be_canceled

    def enqueue(self, item):
        self.start_item(item)
        item.log_output("Starting %s for %s\n" % (self, item.description()))
        self.send_request(item)

    def send_request(self, item):
        if item.canceled:
            return

        if self._set_may_be_canceled:
            item.may_be_canceled = False
        self.http_client.fetch(
            HTTPRequest(
                "%s/%s" % (self.tracker_url, self.tracker_command),
                method="POST",
                headers={"Content-Type": "application/json"},
                user_agent=("ArchiveTeam Warrior/%s %s %s" % (
                    seesaw.__version__, seesaw.runner_type,
                    seesaw.warrior_build)).strip(),
                body=json.dumps(self.data(item))
                ),
            functools.partial(self.handle_response, item))

    def data(self, item):
        return {}

    def handle_response(self, item, response):
        if response.code == 200:
            self.reset_retry_delay()
            if isinstance(response.body, seesaw.six.binary_type):
                self.process_body(response.body.decode('utf-8'), item)
            else:
                self.process_body(response.body, item)
        else:
            if response.code == 420 or response.code == 429:
                r = ("Tracker rate limiting is active. "
                     "We don't want to overload the site we're archiving, "
                     "so we've limited the number of downloads per minute. ")
            elif response.code == 404:
                r = ("No item received. There aren't any items available "
                     "for this project at the moment. Try again later. ")
            elif response.code == 455:
                r = ("Project code is out of date and needs to be upgraded. "
                     "To remedy this problem immediately, you may reboot "
                     "your warrior. ")
            elif response.code == 599:
                r = ("No HTTP response received from tracker. "
                     "The tracker is probably overloaded. ")
            else:
                r = ("Tracker returned status code %d. "
                     "The tracker has probably malfunctioned. "
                     ) % (response.code)
            self.schedule_retry(item, r)
            self.increment_retry_delay()

    def schedule_retry(self, item, message=""):
        if self._set_may_be_canceled:
            item.may_be_canceled = True
        item.log_output(
            "%sRetrying after %d seconds...\n" % (message, self.retry_delay))
        IOLoop.instance().add_timeout(
            datetime.timedelta(seconds=self.retry_delay),
            functools.partial(self.send_request, item))

    def process_body(self, body, item):
        raise NotImplementedError()

    def increment_retry_delay(self, max_delay=300):
        self.retry_delay += 10
        self.retry_delay = min(max_delay, self.retry_delay)

    def reset_retry_delay(self):
        self.retry_delay = self.DEFAULT_RETRY_DELAY


class GetItemFromTracker(TrackerRequest):
    '''Get a single work unit information from the Tracker.'''
    def __init__(self, tracker_url, downloader, version=None):
        TrackerRequest.__init__(self, "GetItemFromTracker", tracker_url,
                                "request", may_be_canceled=True)
        self.downloader = downloader
        self.version = version

    def data(self, item):
        data = {
            "downloader": realize(self.downloader, item),
            "api_version": "2"
        }
        if self.version:
            data["version"] = realize(self.version, item)
        return data

    def process_body(self, body, item):
        data = json.loads(body)
        if "item_name" in data:
            for (k, v) in data.items():
                item[k] = v
            item.log_output(
                "Received item '%s' from tracker\n" % item["item_name"])
            self.complete_item(item)
        else:
            item.log_output("Tracker responded with empty response.\n")
            self.schedule_retry(item)


class SendDoneToTracker(TrackerRequest):
    '''Inform the Tracker the work unit has been completed.'''
    def __init__(self, tracker_url, stats):
        TrackerRequest.__init__(self, "SendDoneToTracker", tracker_url, "done")
        self.stats = stats

    def data(self, item):
        return realize(self.stats, item)

    def process_body(self, body, item):
        if body.strip() == "OK":
            item.log_output(
                "Tracker confirmed item '%s'.\n" % item["item_name"])
            self.complete_item(item)
        else:
            item.log_output(
                "Tracker responded with unexpected '%s'.\n" % body.strip())
            self.schedule_retry(item)


class PrepareStatsForTracker(SimpleTask):
    '''Apply statistical values on the item.'''
    def __init__(self, defaults=None, file_groups=None, id_function=None):
        SimpleTask.__init__(self, "PrepareStatsForTracker")
        self.defaults = defaults or {}
        self.file_groups = file_groups or {}
        self.id_function = id_function

    def process(self, item):
        total_bytes = {}
        for (group, files) in self.file_groups.items():
            total_bytes[group] = sum(
                [os.path.getsize(realize(f, item)) for f in files]
            )

        stats = {}
        stats.update(self.defaults)
        stats["item"] = item["item_name"]
        stats["bytes"] = total_bytes

        if self.id_function:
            stats["id"] = self.id_function(item)

        item["stats"] = realize(stats, item)


class UploadWithTracker(TrackerRequest):
    '''Upload work unit results.

    One of the inner task is used depending on the Tracker's response
    to where to upload:

    * :class:`RsyncUpload`
    * :class:`CurlUpload`
    '''
    def __init__(self, tracker_url, downloader, files, version=None,
                 rsync_target_source_path="./", rsync_bwlimit="0",
                 rsync_extra_args=[], curl_connect_timeout="60",
                 curl_speed_limit="1", curl_speed_time="900"):
        TrackerRequest.__init__(self, "Upload", tracker_url, "upload")

        self.downloader = downloader
        self.version = version

        self.files = files
        self.rsync_target_source_path = rsync_target_source_path
        self.rsync_bwlimit = rsync_bwlimit
        self.rsync_extra_args = rsync_extra_args
        self.curl_connect_timeout = curl_connect_timeout
        self.curl_speed_limit = curl_speed_limit
        self.curl_speed_time = curl_speed_time

    def data(self, item):
        data = {"downloader": realize(self.downloader, item),
                "item_name": item["item_name"]}
        if self.version:
            data["version"] = realize(self.version, item)
        return data

    def process_body(self, body, item):
        data = json.loads(body)
        if "upload_target" in data:
            inner_task = None

            if re.match(r"^rsync://.+/$", data["upload_target"]):
                item.log_output(
                    "Uploading with Rsync to %s" % data["upload_target"])
                inner_task = RsyncUpload(
                    data["upload_target"], self.files,
                    target_source_path=self.rsync_target_source_path,
                    bwlimit=self.rsync_bwlimit,
                    extra_args=self.rsync_extra_args,
                    max_tries=1)

            elif re.match(r"^https?://.+/$", data["upload_target"]):
                item.log_output(
                    "Uploading with Curl to %s" % data["upload_target"])

                if len(self.files) != 1:
                    item.log_output("Curl expects to upload a single file.")
                    item.log_output("Contact a tracker admin!")
                    self.schedule_retry(item)
                    return

                inner_task = CurlUpload(
                    data["upload_target"], self.files[0],
                    self.curl_connect_timeout, self.curl_speed_limit,
                    self.curl_speed_time, max_tries=1)

            else:
                item.log_output("Received invalid upload URI {0}."
                                .format(data["upload_target"]))
                item.log_output("Contact a tracker admin!")
                self.schedule_retry(item)
                return

            inner_task.on_complete_item += self._inner_task_complete_item
            inner_task.on_fail_item += self._inner_task_fail_item
            self._enqueue_inner_task_with_except(inner_task, item)

        else:
            item.log_output("Tracker did not provide an upload target.")
            self.schedule_retry(item)

    def _inner_task_complete_item(self, task, item):
        self.complete_item(item)

    def _inner_task_fail_item(self, task, item):
        self.schedule_retry(item)

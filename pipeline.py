import time
import os
import os.path
import shutil
import json

from seesaw.output import *
from seesaw.item import *
from seesaw.task import *
from seesaw.pipeline import *
from seesaw.externalprocess import *
from seesaw.tracker import *
from seesaw.runner import *

DATA_DIR = "data"
USER_AGENT = "Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US) AppleWebKit/533.20.25 (KHTML, like Gecko) Version/5.0.4 Safari/533.20.27"
VERSION = "20120603.01"

downloader = "testuser"

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

def calculate_item_id(item):
  with open("%s/%s.json" % (item["item_dir"], item["warc_file_base"])) as fp:
    return json.load(fp)


pipeline = Pipeline(
# GetItemFromTracker("http://localhost:9292/example", downloader),
  SetItemKey("item_name", "1083030"),
  PrintItem(),
  PrepareDirectories(),
# PrintItem(),
  ExternalProcess("Echo", [ "echo", "1234" ]),
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
  PrepareStatsForTracker(
    defaults = { "downloader": downloader, "version": VERSION },
    file_groups = {
      "data": [ ItemInterpolation("%(item_dir)s/%(warc_file_base)s.warc.gz") ]
    },
    id_function = calculate_item_id
  ),
  PrintItem(),
  LimitConcurrent(1,
    RsyncUpload(
      target = "localhost::tabblo/%s/" % downloader,
      target_source_path = "data/",
      files = [
        ItemInterpolation("%(item_dir)s/%(warc_file_base)s.warc.gz"),
        ItemInterpolation("%(item_dir)s/%(warc_file_base)s.json")
      ],
      bwlimit=ConfigValue(name="Rsync bwlimit", default="0")
    ),
  ),
  SendDoneToTracker(
    tracker_url = "http://127.0.0.1:9292/example",
    stats = ItemValue("stats")
  )
)

runner = SimpleRunner(pipeline, "STOP")
runner.start()


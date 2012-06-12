import time
import os
import os.path
import shutil
import json

from seesaw.item import *
from seesaw.task import *
from seesaw.pipeline import *
from seesaw.externalprocess import *
from seesaw.tracker import *

class PrepareDirectories(SimpleTask):
  def __init__(self):
    SimpleTask.__init__(self, "PrepareDirectories")

  def process(self, item):
    item_id8 = "%08d" % int(item["item_name"])
    prefix_dir = "/".join(( DATA_DIR, item_id8[7:8], item_id8[6:8], item_id8[5:8] ))
    dirname = "/".join(( prefix_dir, item_id8 ))

    if os.path.isdir(dirname):
      shutil.rmtree(dirname)

    os.makedirs(dirname + "/files")

    item["item_dir"] = dirname
    item["prefix_dir"] = prefix_dir
    item["warc_file_base"] = "picplz-%s-%s" % (item_id8, time.strftime("%Y%m%d-%H%M%S"))

class MoveFiles(SimpleTask):
  def __init__(self):
    SimpleTask.__init__(self, "MoveFiles")

  def process(self, item):
    os.rename("%(item_dir)s/%(warc_file_base)s.warc.gz" % item,
              "%(prefix_dir)s/%(warc_file_base)s.warc.gz" % item)
    os.rename("%(item_dir)s/%(warc_file_base)s.json" % item,
              "%(prefix_dir)s/%(warc_file_base)s.json" % item)

def calculate_item_id(item):
  with open("%s/%s.json" % (item["item_dir"], item["warc_file_base"])) as fp:
    return json.load(fp)

pipeline = Pipeline(
  GetItemFromTracker("http://picplz-3.herokuapp.com", downloader),
# SetItemKey("item_name", "1083030"),
# PrintItem(),
  PrepareDirectories(),
# PrintItem(),
# ExternalProcess("Echo", [ "echo", "1234" ]),
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
    accept_on_exit_code = [ 0, 8 ],
#   retry_on_exit_code = [ 1 ],
    env = { "picplz_lua_json": ItemInterpolation("%(item_dir)s/%(warc_file_base)s.json") })
  ),
# PrintItem(),
  PrepareStatsForTracker(
    defaults = { "downloader": downloader, "version": VERSION },
    file_groups = {
      "user": [ ItemInterpolation("%(item_dir)s/%(warc_file_base)s.warc.gz") ]
    },
    id_function = calculate_item_id
  ),
# PrintItem(),
  MoveFiles(),
  LimitConcurrent(1,
    RsyncUpload(
      target = "fos.textfiles.com::picplz/%s/" % downloader,
      target_source_path = "data/",
      files = [
        ItemInterpolation("%(prefix_dir)s/%(warc_file_base)s.warc.gz"),
        ItemInterpolation("%(prefix_dir)s/%(warc_file_base)s.json")
      ],
      bwlimit=ConfigValue(name="Rsync bwlimit", default="0")
    ),
  ),
  SendDoneToTracker(
    tracker_url = "http://picplz-3.herokuapp.com",
    stats = ItemValue("stats")
  )
)


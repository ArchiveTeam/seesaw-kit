import time
import os
import os.path
import shutil
import json

from seesaw.project import *
from seesaw.item import *
from seesaw.config import *
from seesaw.task import *
from seesaw.pipeline import *
from seesaw.externalprocess import *
from seesaw.tracker import *

DATA_DIR = "data"
USER_AGENT = "Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US) AppleWebKit/533.20.25 (KHTML, like Gecko) Version/5.0.4 Safari/533.20.27"
VERSION = "20120603.01"

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

project = Project(
  title = "Picplz",
  project_html = """
      <img class="project-logo" alt="Picplz" src="https://s3.amazonaws.com/data.tumblr.com/tumblr_l3vf57DJ1e1qaewyu.png" height="50px" />
      <h2>Picplz <span class="links"><a href="http://picplz.com/">Picplz website</a> &middot; <a href="http://picplz.heroku.com/">Leaderboard</a></span></h2>
      <p>picplz is a photo sharing app that makes it easy for you to share your mobile pictures on the Web with just a few clicks.</p>
  """,
  utc_deadline = datetime.datetime(2013,1,1, 12,0,0)
)

pipeline = Pipeline(
  GetItemFromTracker("http://picplz-3.herokuapp.com", downloader),
  PrepareDirectories(),
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
  PrepareStatsForTracker(
    defaults = { "downloader": downloader, "version": VERSION },
    file_groups = {
      "user": [ ItemInterpolation("%(item_dir)s/%(warc_file_base)s.warc.gz") ]
    },
    id_function = calculate_item_id
  ),
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


# encoding=utf-8
from __future__ import unicode_literals

import datetime
import os
import os.path
import shutil
import json

from seesaw.project import *
from seesaw.item import *
from seesaw.task import *
from seesaw.pipeline import *
from seesaw.externalprocess import *
from seesaw.tracker import *

project = Project(
  title = "Example project",
  project_html = """
    <img class="project-logo" alt="Project logo" src="http://archive.org/images/glogo.png" height="50px" />
    <h2>Example project <span class="links"><a href="http://example.com/">Example website</a> &middot; <a href="http://example.heroku.com/">Leaderboard</a></span></h2>
    <p>This is an example project. Under a logo and title there's some room for extra information.</p>
    <p class="projectBroadcastMessage">Important project specific message goes here.</p>
  """,
  utc_deadline = datetime.datetime(2013,1,1, 12,0,0)
)

class CustomTask(SimpleTask):
    def __init__(self):
        SimpleTask.__init__(self, 'CustomTask')

    def process(self, item):
        item.log_output('ÐÐ Hello !')

        # Test binary output
        item.log_output('ðbßf'.encode('utf-8'))
        item.log_output(b'\xff\xff\xff')


pipeline = Pipeline(
  SetItemKey("item_name", "1083030"),
  PrintItem(),
  CustomTask(),
  ExternalProcess("Echo", [ "echo", "1234" ]),
  ExternalProcess("sleep", [ "sleep", "5" ]),
  ExternalProcess("pwd", [ "pwd" ]),
  PrintItem()
)


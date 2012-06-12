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

pipeline = Pipeline(
  SetItemKey("item_name", "1083030"),
  PrintItem(),
  ExternalProcess("Echo", [ "echo", "1234" ]),
  PrintItem()
)


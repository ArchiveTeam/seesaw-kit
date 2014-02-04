from seesaw.runner import *

DATA_DIR = "data"
USER_AGENT = "Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US) AppleWebKit/533.20.25 (KHTML, like Gecko) Version/5.0.4 Safari/533.20.27"
VERSION = "20120603.01"

downloader = "alard"

exec(compile(open("picplz-example-pipeline.py").read(), "picplz-example-pipeline.py", 'exec'))

print(pipeline)

runner = SimpleRunner(pipeline, stop_file="STOP", concurrent_items=1)
runner.start()


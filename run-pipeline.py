import sys
import os.path
from argparse import ArgumentParser

from seesaw.runner import *

def load_pipeline(pipeline_path, context):
  dirname, basename = os.path.split(pipeline_path)
  if dirname == "":
    dirname = "."

  with open(pipeline_path) as f:
    pipeline_str = f.read()

  local_context = context
  global_context = context
  curdir = os.getcwd()
  try:
    os.chdir(dirname)
    exec pipeline_str in local_context, global_context
  finally:
    os.chdir(curdir)
  return local_context["pipeline"]

def main():
  parser = ArgumentParser(description="Run the pipeline")
  parser.add_argument("pipeline", metavar="PIPELINE", type=str,
                      help="the pipeline file")
  parser.add_argument("downloader", metavar="DOWNLOADER", type=str,
                      help="your username")
  parser.add_argument("--concurrent", dest="concurrent_items",
                      help="work on N items at a time (default: 1)",
                      metavar="N", type=int, default=1)
  parser.add_argument("--stop-file", dest="stop_file",
                      help="the STOP file to be monitored (default: STOP)",
                      metavar="FILE", type=str, default="STOP")
  args = parser.parse_args()

  pipeline = load_pipeline(args.pipeline, { "downloader": args.downloader })

  print pipeline

  runner = SimpleRunner(pipeline, stop_file=args.stop_file, concurrent_items=args.concurrent_items)
  runner.start()


if __name__ == "__main__":
  main()


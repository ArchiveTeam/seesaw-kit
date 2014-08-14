from __future__ import print_function

from argparse import ArgumentParser
import os.path
import re
import sys

import seesaw
from seesaw.runner import SimpleRunner
from seesaw.web import start_runner_server


seesaw.runner_type = "Standalone"


def load_pipeline(pipeline_path, context):
    dirname, dummy = os.path.split(pipeline_path)
    if dirname == "":
        dirname = "."

    with open(pipeline_path) as f:
        pipeline_str = f.read()

    local_context = context
    global_context = context
    curdir = os.getcwd()
    try:
        os.chdir(dirname)
        exec(pipeline_str, local_context, global_context)
    finally:
        os.chdir(curdir)

    project = local_context["project"]
    pipeline = local_context["pipeline"]
    pipeline.project = project
    return (project, pipeline)


def check_downloader_or_exit(value, regex="^[-_a-zA-Z0-9]{3,30}$"):
    if not re.search(regex, value):
        print('Please use a nickname containing only letters A-Z, '
              'numbers, underscore, and hyphen-minus only.')
        sys.exit(1)


def main():
    parser = ArgumentParser(description="Run the pipeline")
    parser.add_argument("pipeline", metavar="PIPELINE", type=str,
                        help="the pipeline file")
    parser.add_argument("downloader", metavar="DOWNLOADER", type=str,
                        help="your nickname")
    parser.add_argument("--concurrent", dest="concurrent_items",
                        help="work on N items at a time (default: 1)",
                        metavar="N", type=int, default=1)
    parser.add_argument("--max-items", dest="max_items",
                        help="stop after completing N items",
                        metavar="N", type=int, default=None)
    parser.add_argument("--stop-file", dest="stop_file",
                        help="the STOP file to be monitored (default: STOP)",
                        metavar="FILE", type=str, default="STOP")
    parser.add_argument("--disable-web-server", dest="enable_web_server",
                        help="disable the web interface",
                        action="store_false")
    parser.add_argument("--keep-data", dest="keep_data",
                        help="do not remove data of finished items",
                        action="store_true")
    parser.add_argument("--address", dest="address",
                        help="the IP address of the web interface "
                             "(default: 0.0.0.0)",
                        metavar="HOST", type=str, default="0.0.0.0")
    parser.add_argument("--port", dest="port_number",
                        help="the port number for the web interface "
                             "(default: 8001)",
                        metavar="PORT", type=int, default=8001)
    parser.add_argument("--http-username", dest="http_username",
                        help="username for the web interface (default: admin)",
                        metavar="USERNAME", type=str
                        )  # default is set in start_runner_server
    parser.add_argument("--http-password", dest="http_password",
                        help="password for the web interface",
                        metavar="PASSWORD", type=str)
    parser.add_argument("--context-value", dest="context_values",
                        help="additional pipeline global variables "
                             "(name=text)",
                        metavar='VALUE_PAIR',
                        action='append', default=[], type=str)
    parser.add_argument("--version", action="version",
                        version=seesaw.__version__)
    args = parser.parse_args()

    check_downloader_or_exit(args.downloader)

    context = {"downloader": args.downloader}

    for context_value in args.context_values:
        name, text_value = context_value.split("=", 1)
        if name not in context:
            context[name] = text_value
        else:
            raise Exception("Context value name %s already defined." % name)

    (project, pipeline) = load_pipeline(args.pipeline, context)

    print("*" * 74)
    print("*%-072s*" % " ")
    print("*%-072s*" % ("   ArchiveTeam Seesaw kit - %s" % seesaw.__version__))
    print("*%-072s*" % " ")
    print("*" * 74)
    print()
    print("Initializing pipeline for '%s'" % (project.title))
    print()
    print(pipeline)
    print()
    print("-" * 74)
    print()

    runner = SimpleRunner(
        pipeline,
        stop_file=args.stop_file,
        concurrent_items=args.concurrent_items,
        max_items=args.max_items,
        keep_data=args.keep_data)

    if args.enable_web_server:
        print("Starting the web interface on %s:%d..." %
              (args.address, args.port_number))
        print()
        print("-" * 74)
        print()
        start_runner_server(project, runner,
                            bind_address=args.address,
                            port_number=args.port_number,
                            http_username=args.http_username,
                            http_password=args.http_password)

    print("Run 'touch %s' to stop downloading." % args.stop_file)
    print()
    runner.start()


if __name__ == "__main__":
    main()

from __future__ import print_function

import argparse
from argparse import ArgumentParser
import logging
import logging.handlers
import os

import seesaw
seesaw.runner_type = "Warrior"

from seesaw.log import LOG_FORMAT, LogFilter
from seesaw.warrior import Warrior
from seesaw.web import start_warrior_server


def setup_logging(log_dir):
    logging.basicConfig(
        format=LOG_FORMAT,
        level=logging.DEBUG
    )

    path = os.path.join(log_dir, 'warrior.log')
    handler = logging.handlers.TimedRotatingFileHandler(
        path, when='D', backupCount=10
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)

    for handler in logging.getLogger().handlers:
        handler.addFilter(LogFilter())

    logging.info('Logging to %s', path)


def main():
    parser = ArgumentParser(description="Run the warrior web interface")
    parser.add_argument("--projects-dir", dest="projects_dir",
                        metavar="DIRECTORY", type=str,
                        help="the warrior projects directory", required=True)
    parser.add_argument("--data-dir", dest="data_dir", metavar="DIRECTORY",
                        type=str,
                        help="the data directory", required=True)
    parser.add_argument("--warrior-hq", dest="warrior_hq_url", metavar="URL",
                        type=str,
                        help="the url to the Warrior HQ", required=True)
    parser.add_argument("--address", dest="address",
                        help="the IP address of the web interface "
                             "(default: 0.0.0.0)",
                        metavar="HOST", type=str, default="0.0.0.0")
    parser.add_argument("--port", dest="port_number",
                        help="the port number for the web interface "
                             "(default: 8001)",
                        metavar="PORT", type=int, default=8001)
    parser.add_argument("--http-username", dest="http_username",
                        help="username for the web interface",
                        metavar="USERNAME", type=str)
    parser.add_argument("--http-password", dest="http_password",
                        help="username for the web interface (default: admin)",
                        metavar="PASSWORD", type=str
                        )  # default is set in start_warrior_server
    parser.add_argument("--keep-data", dest="keep_data",
                        help="do not remove data of finished items",
                        action="store_true")
    parser.add_argument("--real-shutdown", dest="real_shutdown",
                        help="the shutdown button in the web interface uses "
                             "sudo shutdown",
                        action="store_true")
    # extra option to report the warrior VM version to the tracker
    # ask before using
    parser.add_argument("--warrior-build", dest="warrior_build",
                        help=argparse.SUPPRESS, type=str)
    args = parser.parse_args()

    setup_logging(args.data_dir)

    if args.warrior_build:
        seesaw.warrior_build = args.warrior_build

    print("*" * 74)
    print("*%-072s*" % " ")
    print("*%-072s*" % ("   ArchiveTeam Seesaw kit - %s" % seesaw.__version__))
    print("*%-072s*" % " ")
    print("*" * 74)
    print()
    print("Starting the web interface on %s:%d..." %
          (args.address, args.port_number))
    print()
    print("-" * 74)
    print()

    warrior = Warrior(
        args.projects_dir,
        args.data_dir,
        args.warrior_hq_url,
        real_shutdown=args.real_shutdown,
        keep_data=args.keep_data)

    start_warrior_server(warrior,
                         bind_address=args.address,
                         port_number=args.port_number,
                         http_username=args.http_username,
                         http_password=args.http_password)

    warrior.start()


if __name__ == "__main__":
    main()

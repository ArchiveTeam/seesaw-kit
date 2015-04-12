from __future__ import print_function

from argparse import ArgumentParser
import itertools
import os.path
import re
import subprocess
import sys
import time

from seesaw.runner import SimpleRunner
from seesaw.web import start_runner_server
import seesaw
import tornado.ioloop
import signal


seesaw.runner_type = "Standalone"
graceful_stop_activate_time = None


class GitCheckError(OSError):
    pass


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


def check_concurrency_or_exit(value):
    if value > 20:
        print()
        print("I'm sorry, User. I'm afraid I can't do that.")
        print('Please limit --concurrent to 20 or lower '
              'to avoid exhausting resources or triggering bugs.'
              )
        sys.exit(1)

    if value > 6:
        print("!" * 74)
        print("!%-072s!" % " ")
        print("!%-072s!" % ('    Whoa! Your concurrency level is at {0}.'
                            .format(value)))
        print("!%-072s!" % ('    Please check if this is what you wanted.'))
        print("!%-072s!" % ('    Continuing anyway...'))
        print("!%-072s!" % " ")
        print("!" * 74)
        print()


def get_output(command):
    proc = subprocess.Popen(command, stdout=subprocess.PIPE)
    return proc.returncode, proc.communicate()[0]


def check_git_repo_or_exit():
    try:
        get_git_hash()
        get_remote_git_hash()
    except GitCheckError:
        print("@@@  Problem executing git!  @@@")
        print("Is this a git repo?")
        sys.exit(1)


def update_repo():
    try:
        branch = get_git_branch()
        subprocess.check_call(["git", "pull", "origin", branch])
    except (GitCheckError, subprocess.CalledProcessError):
        print("@@@  The repo could not be updated. Ignoring error.")


def get_git_hash():
    return_code, output = get_output(["git", "rev-parse", "HEAD"])

    git_hash = output.decode('ascii').strip().lower()

    if return_code or not git_hash:
        raise GitCheckError('Could not get git hash.')

    return git_hash


def get_remote_git_hash():
    branch = get_git_branch()

    return_code, output = get_output(
        ["git", "rev-parse", "origin/{0}".format(branch)])

    git_hash = output.decode('ascii').strip().lower()

    if return_code or not git_hash:
        raise GitCheckError('Could not get remote git hash.')

    return git_hash


def get_git_branch():
    return_code, output = get_output(['git', 'rev-parse',
                                      '--abbrev-ref', 'HEAD'])

    branch = output.decode('ascii').strip().lower()

    if return_code or not branch:
        raise GitCheckError('Could not get git branch name.')

    return branch


def attach_git_scheduler(runner):
    nonlocal_dict = {}
    nonlocal_dict['current_git_hash'] = get_git_hash()

    def check_and_update():
        if runner.stop_flag:
            return

        try:
            remote_hash = get_remote_git_hash()
        except GitCheckError:
            print("@@@  Could not check latest repo version. Ignoring error.")
        else:
            if remote_hash != nonlocal_dict['current_git_hash']:
                print('Old hash {0}. New hash {1}'
                      .format(nonlocal_dict['current_git_hash'], remote_hash))

                runner.is_git_update_needed = True

                nonlocal_dict['timer'].stop()
                runner.stop_gracefully()

    timer = tornado.ioloop.PeriodicCallback(check_and_update, 30 * 60 * 1000)

    nonlocal_dict['timer'] = timer

    timer.start()


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
                             "(default: localhost)",
                        metavar="HOST", type=str, default="localhost")
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
    parser.add_argument("--auto-update", action="store_true",
                        help="attempt to update via git pull (experimental)")
    args = parser.parse_args()

    check_downloader_or_exit(args.downloader)
    check_concurrency_or_exit(args.concurrent_items)

    if args.auto_update:
        check_git_repo_or_exit()
        trial_iterator = itertools.count(1)
    else:
        trial_iterator = [1]

    for trial_num in trial_iterator:
        runner = init_runner(args)

        if args.auto_update:
            runner.is_git_update_needed = False
            attach_git_scheduler(runner)

        runner.start()

        if args.auto_update and runner.is_git_update_needed:
            tornado.ioloop.IOLoop.instance().close(all_fds=True)
            del tornado.ioloop.IOLoop._instance
            print("+++  End of trial {0}. Time to update.  +++"
                  .format(trial_num))
            update_repo()
            time.sleep(10)
        else:
            break


def init_runner(args):
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

    print("Run 'touch %s' or interrupt (CTRL+C) to stop downloading."
          % args.stop_file)
    print()

    attach_ctrl_c_handler(args.stop_file)

    return runner


def attach_ctrl_c_handler(stop_file):
    def graceful_stop_callback(dummy1, dummy2):
        global graceful_stop_activate_time

        if not graceful_stop_activate_time:
            open(stop_file, 'wb').close()
            graceful_stop_activate_time = time.time()
            print('Interrupt again (CTRL+C) to forcefully stop')
        elif graceful_stop_activate_time and \
                time.time() - graceful_stop_activate_time < 5:
            sys.exit('Stopping immediately.')
        else:
            print('Interrupt again (CTRL+C) to forcefully stop')
            graceful_stop_activate_time = time.time()

    signal.signal(signal.SIGINT, graceful_stop_callback)


if __name__ == "__main__":
    main()

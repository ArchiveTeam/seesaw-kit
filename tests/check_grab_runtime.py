#!/usr/bin/env python3
'''Drive a grab project's pipeline up to the moment wget would be launched.

check_grab_pipeline.py only proves the pipeline file loads. This goes
further: it builds a real Item, runs the project's own pre-download tasks
against it, and realizes the wget argument list -- which is where
ItemInterpolation, ItemValue and realize() are actually exercised, and so
where a seesaw change is most likely to break a project silently.

Nothing is downloaded and nothing external is contacted: outbound sockets
are blocked outright and the pre-download HTTP calls are answered with
synthetic data (see grab_offline.py).

Tasks after the download step are not run -- they consume wget's output,
which does not exist here.

Usage: check_grab_runtime.py PROJECT_DIR --repo OWNER/NAME [--fixtures F]
'''

from __future__ import print_function

from argparse import ArgumentParser
import json
import os
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grab_offline import (add_routes, block_network, stub_http,
                          NetworkBlocked, UnstubbedRequest)

block_network()
stub_http()

from seesaw.config import realize
from seesaw.externalprocess import ExternalProcess
from seesaw.item import Item
from seesaw.task import SimpleTask

from check_grab_pipeline import load_pipeline


class NeedsFixture(Exception):
    '''The project needs input this harness was not given.'''


# Tasks that probe the machine's internet connection rather than exercise
# any seesaw API. They shell out to wget, so blocking them in-process is
# not possible, and they cannot pass with the network removed. Projects
# with other network-dependent pre-download tasks name them in the
# fixture's "skip_tasks"; this stays a per-project decision because the
# same class name can be offline-safe elsewhere (nihnlmdigitalcollections
# has a SetCookies that needs nothing external, youtube-grab's fetches a
# live cookie from youtube.com).
ENVIRONMENT_PROBE_TASKS = ('CheckIP', 'CheckRequirements')


def is_project_task(task):
    '''True for tasks defined by the pipeline itself, not by seesaw.

    seesaw's own tracker tasks talk to the tracker; the project's tasks are
    the ones worth exercising.
    '''
    return not type(task).__module__.startswith('seesaw.')


def make_item(pipeline, fixture, data_dir):
    item_name = '\0'.join(fixture['item_name'])
    properties = {'item_name': item_name, 'data_dir': data_dir}
    properties.update(fixture.get('properties', {}))

    item = Item(pipeline, 'ci-item', 1, prepare_data_directory=False,
                properties=properties)
    item.on_output.handle(lambda *args: None)
    item.on_error.handle(lambda *args: None)
    return item


def check_wget_args(args, project_dir):
    '''Sanity-check a realized wget argument list.'''
    for index, arg in enumerate(args):
        if not isinstance(arg, str):
            raise AssertionError(
                'wget arg %d is %r (%s), not a string -- realize() left '
                'something unresolved' % (index, arg, type(arg).__name__))

    if not args:
        raise AssertionError('argument list is empty')

    # Not every project shells out to wget directly: urls-grab wraps it in
    # timeout(1) and terroroftinytown runs its own scraper. Resolve the
    # command through PATH as the shell would.
    command = args[0]
    if not os.path.exists(command) and shutil.which(command) is None:
        raise AssertionError('command %r does not exist and is not on PATH'
                             % command)

    # A --lua-script naming a file that is not in the repo means the
    # project would die on its first item.
    for index, arg in enumerate(args):
        if arg == '--lua-script':
            script = args[index + 1]
            path = script if os.path.isabs(script) \
                else os.path.join(project_dir, script)
            if not os.path.exists(path):
                raise AssertionError('--lua-script %r not found' % script)
        elif arg == '--warc-zstd-dict':
            if not os.path.exists(args[index + 1]):
                raise AssertionError('--warc-zstd-dict %r not written'
                                     % args[index + 1])

    urls = [arg for arg in args[1:] if '://' in arg]
    if not urls and '--warc-file' in args:
        raise AssertionError('a wget invocation with no URLs to fetch')

    return urls


def run(project_dir, repo, fixtures_path):
    project_dir = os.path.abspath(project_dir)

    with open(fixtures_path) as f:
        fixtures = json.load(f)

    if repo not in fixtures:
        raise NeedsFixture('no entry for %s in %s'
                           % (repo, os.path.basename(fixtures_path)))

    fixture = fixtures[repo]
    add_routes(fixture.get('http'))
    skip_tasks = set(ENVIRONMENT_PROBE_TASKS) | set(fixture.get('skip_tasks', []))

    pipeline_path = os.path.join(project_dir, 'pipeline.py')
    project, pipeline, dummy = load_pipeline(pipeline_path,
                                             {'downloader': 'testuser'})

    data_dir = tempfile.mkdtemp(prefix='seesaw-ci-')
    curdir = os.getcwd()
    ran = []

    try:
        # The pipelines assume they run from the project directory: relative
        # lua script names, ./wget-at, and so on.
        os.chdir(project_dir)
        item = make_item(pipeline, fixture, data_dir)

        for task in pipeline.tasks:
            if isinstance(task, ExternalProcess):
                args = realize(task.args, item)
                urls = check_wget_args(args, project_dir)
                ran.append('%s (realized %d args, %d URLs)'
                           % (task.name, len(args), len(urls)))
                # Everything past the download consumes wget's output.
                break

            if type(task).__name__ in skip_tasks:
                ran.append('%s [skipped: needs the network]' % task.name)
            elif isinstance(task, SimpleTask) and is_project_task(task):
                task.process(item)
                ran.append(task.name)
            else:
                ran.append('%s [skipped]' % task.name)
        else:
            raise AssertionError(
                'pipeline has no ExternalProcess task to realize')
    finally:
        os.chdir(curdir)
        shutil.rmtree(data_dir, ignore_errors=True)

    for line in ran:
        print('  %s' % line)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('project_dir', metavar='PROJECT_DIR')
    parser.add_argument('--repo', required=True,
                        help='owner/name, used to look up the fixture')
    parser.add_argument('--fixtures', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'grab-fixtures.json'))
    args = parser.parse_args()

    try:
        run(args.project_dir, args.repo, args.fixtures)
    except NeedsFixture as error:
        print('SKIP: %s (%s)' % (args.repo, error))
        return 0
    except UnstubbedRequest as error:
        print('SKIP: %s (unstubbed HTTP call to %s)' % (args.repo, error))
        return 0
    except NetworkBlocked:
        print('FAIL: %s reached for the network before downloading'
              % args.repo)
        traceback.print_exc()
        return 1
    except Exception:
        print('FAIL: %s' % args.repo)
        traceback.print_exc()
        return 1

    print('OK: %s' % args.repo)
    return 0


if __name__ == '__main__':
    sys.exit(main())

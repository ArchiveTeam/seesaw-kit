#!/usr/bin/env python3
'''Load a grab project's pipeline.py the same way the warrior does.

This is a compatibility smoke test: it does not download anything and does
not talk to a tracker. It only checks that the pipeline file can be
executed against the seesaw version in this checkout and that it produces
the objects the runner expects.

Usage: check_grab_pipeline.py PROJECT_DIR [--downloader NAME]
'''

from __future__ import print_function

from argparse import ArgumentParser
import os
import sys
import traceback

from seesaw.config import ConfigValue
from seesaw.pipeline import Pipeline
from seesaw.project import Project
from seesaw.task import Task


def load_pipeline(pipeline_path, context):
    '''Mirror of Warrior.load_pipeline (seesaw/warrior.py).'''
    dirname, dummy = os.path.split(pipeline_path)
    if dirname == "":
        dirname = "."

    with open(pipeline_path) as f:
        pipeline_str = f.read()

    ConfigValue.start_collecting()

    local_context = context
    global_context = context
    curdir = os.getcwd()
    try:
        os.chdir(dirname)
        exec(compile(pipeline_str, pipeline_path, 'exec'),
             local_context, global_context)
    finally:
        os.chdir(curdir)
        config_values = ConfigValue.stop_collecting()

    project = local_context["project"]
    pipeline = local_context["pipeline"]
    pipeline.project = project
    return (project, pipeline, config_values)


def check(project_dir, downloader):
    pipeline_path = os.path.join(project_dir, "pipeline.py")

    if not os.path.exists(pipeline_path):
        raise AssertionError("No pipeline.py in %s" % project_dir)

    project, pipeline, config_values = load_pipeline(
        pipeline_path, {"downloader": downloader})

    assert isinstance(project, Project), \
        "'project' is %r, expected a seesaw.project.Project" % type(project)
    assert isinstance(pipeline, Pipeline), \
        "'pipeline' is %r, expected a seesaw.pipeline.Pipeline" % type(pipeline)
    assert project.title, "project has no title"
    assert pipeline.tasks, "pipeline has no tasks"

    for task in pipeline.tasks:
        assert isinstance(task, Task), \
            "pipeline task %r is not a seesaw.task.Task" % (task,)

    # The warrior renders these in the web UI; a broken __str__ takes it down.
    str(pipeline)
    for task in pipeline.tasks:
        str(task)

    for config_value in config_values:
        assert config_value.name, "config value without a name: %r" % config_value

    print("Project:   %s" % project.title)
    print("Tasks:     %d" % len(pipeline.tasks))
    print("Config:    %s" % (", ".join(c.name for c in config_values) or "(none)"))
    print()
    print(pipeline)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", metavar="PROJECT_DIR",
                        help="directory containing pipeline.py")
    parser.add_argument("--downloader", default="testuser",
                        help="nickname passed to the pipeline (default: testuser)")
    args = parser.parse_args()

    try:
        check(args.project_dir, args.downloader)
    except Exception:
        print("FAIL: %s" % args.project_dir)
        traceback.print_exc()
        return 1

    print()
    print("OK: %s" % args.project_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

Seesaw toolkit
==============

An asynchronous toolkit for distributed web processing. Written in Python and named after its behavior, it supports concurrent downloads, uploads, etc.

This toolkit is well-known for [Archive Team projects](http://archiveteam.org). It also powers the [Archive Team warrior](http://archiveteam.org/index.php?title=Warrior).

[![Tests](https://github.com/ArchiveTeam/seesaw-kit/actions/workflows/test.yml/badge.svg)](https://github.com/ArchiveTeam/seesaw-kit/actions/workflows/test.yml)

Installation
------------

Requires Python 3.9.

Needs the Tornado library for event-driven I/O. The complete list of Python modules needed are listed in requirements.txt.


How to try it out
-----------------

To run the example pipeline:

    pip install .
    run-pipeline --help
    run-pipeline examples/example-pipeline.py someone

Point your browser to `http://127.0.0.1:8001/`.

`run-pipeline3` and `run-warrior3` are installed as aliases of
`run-pipeline` and `run-warrior`. The suffixed names date from the days of
supporting both Python 2 and 3; they are kept because existing projects and
instructions refer to them.


Overview
--------

General idea: a set of `Task`s that can be combined into a `Pipeline` that processes `Item`s:

* An `Item` is a thing that needs to be downloaded (a user, for example). It has properties that are filled by the `Task`s.
* A `Task` is a step in the download process: it takes an item, does something with it and passes it on. Example Tasks: getting an item name from the tracker, running a download script, rsyncing the result, notifying the tracker that it's done.
* A `Pipeline` represents a sequence of `Task`s. To make a seesaw script for a new project you'd specify a new `Pipeline`.

A `Task` can work on multiple `Item`s at a time (e.g., multiple Wget downloads). The concurrency can be limited by wrapping the task in a `LimitConcurrency` `Task`: this will queue the items and run them one-by-one (e.g., a single Rsync upload).

The `Pipeline` needs to be fed empty `Item` objects; by controlling the number of active `Item`s you can limit the number of items. (For example, add a new item each time an item leaves the pipeline.)

With the `ItemValue`, `ItemInterpolation` and `ConfigValue` classes it is possible to pass item-specific arguments to the `Task` objects. The value of these objects will be re-evaluated for each item. Examples: a path name that depends on the item name, a configurable bandwidth limit, the number of concurrent downloads.

Consult [the wiki](https://github.com/ArchiveTeam/seesaw-kit/wiki) for more information.


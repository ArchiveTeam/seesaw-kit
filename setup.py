#!/usr/bin/env python

"""
distutils/setuptools install script. See inline comments for packaging documentation.
"""

import os
import sys

import seesaw

try:
  from setuptools import setup
  # hush pyflakes
  setup
except ImportError:
  from distutils.core import setup

try:
  from distutils.command.build_py import build_py_2to3 as build_py
except ImportError:
  from distutils.command.build_py import build_py

packages = [
  'seesaw'
]

package_dir = {
  'seesaw': 'seesaw'
}

package_data = {
  'seesaw': [
    'public/index.html',
    'public/*.js',
    'public/*.css',
    'templates/*.html'
  ]
}

scripts = [
  'run-pipeline',
  'run-warrior'
]

requires = [
  'argparse',
  'ordereddict',
  'Tornado>=2.3',
  'tornadio2>=0.0.3'
]

setup(
  name='seesaw',
  version=seesaw.__version__,
  maintainer='ArchiveTeam',
  maintainer_email='warrior@archiveteam.org',
  description='ArchiveTeam seesaw kit',
  long_description=open('README.md', 'rt').read(),
  url='http://www.archiveteam.org/',
  packages=packages,
  package_dir=package_dir,
  package_data=package_data,
  include_package_data=True,
  scripts=scripts,
  install_requires=requires,
  cmdclass={'build_py': build_py}
)


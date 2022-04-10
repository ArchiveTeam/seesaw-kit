#!/usr/bin/env python
import seesaw

try:
    from setuptools import setup
    # hush pyflakes
    setup  # pylint: disable=pointless-statement
except ImportError:
    from distutils.core import setup


packages = [
    'seesaw',
    'seesaw.script',
]

package_dir = {
    'seesaw': 'seesaw',
}

package_data = {
    'seesaw': [
        'public/index.html',
        'public/*.js',
        'public/*.css',
        'public/*.png',
        'templates/*.html'
    ]
}

scripts = [
        'run-pipeline',
        'run-warrior',
]

requires = [
    'Tornado>=4,<4.99999.99999',
    'sockjs-tornado',
]

setup(
    name='seesaw',
    version=seesaw.__version__,
    maintainer='ArchiveTeam',
    maintainer_email='warrior@archiveteam.org',
    description='ArchiveTeam seesaw kit',
    long_description=open('README.md', 'r').read(),
    long_description_content_type='text/markdown',
    url='http://www.archiveteam.org/',
    packages=packages,
    package_dir=package_dir,
    package_data=package_data,
    include_package_data=True,
    scripts=scripts,
    install_requires=requires,
)

#!/usr/bin/env python3
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
    'run-pipeline3',
    'run-warrior3',
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
    python_requires='>=3.9',
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3 :: Only',
    ],
)

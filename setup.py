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

console_scripts = [
    'run-pipeline = seesaw.script.run_pipeline:main',
    'run-warrior = seesaw.script.run_warrior:main',
    # The 3-suffixed names date from the Python 2/3 split. They are kept
    # because the *-grab repos, the warrior images and a lot of published
    # instructions invoke them by name.
    'run-pipeline3 = seesaw.script.run_pipeline:main',
    'run-warrior3 = seesaw.script.run_warrior:main',
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
    entry_points={'console_scripts': console_scripts},
    install_requires=requires,
    python_requires='>=3.9',
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3 :: Only',
    ],
)

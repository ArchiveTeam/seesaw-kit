#!/usr/bin/python3
'''Runs the complete warrior system and the Mock HQ.'''
import atexit
import copy
import os.path
import shutil
import subprocess
import tempfile


def main():
    temp_dir = tempfile.mkdtemp(prefix='warrior-tmp')
    this_dir = os.path.dirname(__file__)
    root_dir = os.path.join(this_dir, '..')
    env = copy.copy(os.environ)
    env['PYTHONPATH'] = root_dir

    def cleanup():
        shutil.rmtree(temp_dir)

    atexit.register(cleanup)

    web_process = subprocess.Popen([
        os.environ.get('PYTHON_BIN', 'python3'), os.path.join(this_dir, 'hq_mock.py'),
        ],
    )

    subprocess.check_call([
        os.path.join(root_dir, './run-warrior3'),
        '--projects-dir', temp_dir,
        '--data-dir', temp_dir,
        '--address', 'localhost',
        '--port', '8688',
        '--warrior-hq', 'http://localhost:8681/',
        ],
        env=env
    )

    web_process.terminate()

if __name__ == '__main__':
    main()

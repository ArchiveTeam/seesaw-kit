# encoding=utf8
'''Tests for the standalone pipeline runner script.'''
from __future__ import unicode_literals

import os
import os.path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

from tornado.testing import bind_unused_port

import seesaw
from seesaw.runner import SimpleRunner
from seesaw.pipeline import Pipeline
from seesaw.script.run_pipeline import GitCheckError, \
    attach_ctrl_c_handler, attach_git_scheduler, check_concurrency_or_exit, \
    check_downloader_or_exit, check_git_repo_or_exit, get_git_branch, \
    get_git_hash, get_output, get_remote_git_hash, init_runner, \
    load_pipeline, main, update_repo
from seesaw.task import PrintItem
from seesaw.six import StringIO
from tests.test_base import BaseTestCase


HAS_GIT = bool(shutil.which('git'))


PIPELINE_SOURCE = '''
from seesaw.pipeline import Pipeline
from seesaw.project import Project
from seesaw.task import PrintItem, SetItemKey

project = Project(title="A test project")
pipeline = Pipeline(SetItemKey("who", downloader), PrintItem())
'''


class Arguments(object):
    '''Stands in for the parsed argparse namespace.'''
    def __init__(self, pipeline, **kwargs):
        self.pipeline = pipeline
        self.downloader = 'someone'
        self.concurrent_items = 1
        self.max_items = 1
        self.stop_file = 'STOP'
        self.enable_web_server = False
        self.keep_data = False
        self.address = 'localhost'
        self.port_number = 8001
        self.http_username = None
        self.http_password = None
        self.context_values = []
        self.auto_update = False

        for name, value in kwargs.items():
            setattr(self, name, value)


class CapturedOutput(object):
    '''Collects everything printed while the block runs.'''
    def __enter__(self):
        self.buffer = StringIO()
        self.original_stdout = sys.stdout
        sys.stdout = self.buffer
        return self

    def __exit__(self, *args):
        sys.stdout = self.original_stdout
        return False

    @property
    def text(self):
        return self.buffer.getvalue()


class CheckDownloaderTest(BaseTestCase):
    def test_accepts_a_normal_nickname(self):
        for nickname in ('someone', 'some-one', 'some_one', 'abc123',
                         'a' * 30):
            check_downloader_or_exit(nickname)

    def test_rejects_a_nickname_that_is_too_short(self):
        with CapturedOutput() as output:
            self.assertRaises(SystemExit, check_downloader_or_exit, 'ab')

        self.assertIn('Please use a nickname', output.text)

    def test_rejects_illegal_characters(self):
        with CapturedOutput():
            self.assertRaises(SystemExit, check_downloader_or_exit, 'some one')
            self.assertRaises(SystemExit, check_downloader_or_exit, '!!!!')

    def test_accepts_a_custom_pattern(self):
        check_downloader_or_exit('!!', regex='^!!$')


class CheckConcurrencyTest(BaseTestCase):
    def test_a_normal_level_says_nothing(self):
        with CapturedOutput() as output:
            check_concurrency_or_exit(1)
            check_concurrency_or_exit(6)

        self.assertEqual('', output.text)

    def test_a_high_level_warns_but_continues(self):
        with CapturedOutput() as output:
            check_concurrency_or_exit(7)

        self.assertIn('Whoa! Your concurrency level is at 7.', output.text)
        self.assertIn('Continuing anyway', output.text)

    def test_an_absurd_level_stops_the_program(self):
        with CapturedOutput() as output:
            self.assertRaises(SystemExit, check_concurrency_or_exit, 21)

        self.assertIn("I'm afraid I can't do that", output.text)


class GetOutputTest(BaseTestCase):
    def test_returns_the_command_output_and_a_zero_exit_code(self):
        return_code, output = get_output(
            [sys.executable, '-c', "print('hello')"])

        self.assertEqual(b'hello\n', output.replace(b'\r\n', b'\n'))
        self.assertEqual(0, return_code)

    def test_reports_a_nonzero_exit_code(self):
        return_code, dummy = get_output(
            [sys.executable, '-c', 'import sys;sys.exit(3)'])

        self.assertEqual(3, return_code)


@unittest.skipUnless(HAS_GIT, 'git is not installed')
class GitHelperTest(BaseTestCase):
    def setUp(self):
        super(GitHelperTest, self).setUp()

        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.original_dir = os.getcwd()
        self.addCleanup(os.chdir, self.original_dir)

        self.upstream = os.path.join(self.temp_dir, 'upstream')
        self.clone = os.path.join(self.temp_dir, 'clone')
        os.makedirs(self.upstream)
        self.git(self.upstream, 'init')
        self.git(self.upstream, 'config', 'user.email', 'test@example.com')
        self.git(self.upstream, 'config', 'user.name', 'Test')
        with open(os.path.join(self.upstream, 'a.txt'), 'w') as file_obj:
            file_obj.write('first\n')
        self.git(self.upstream, 'add', '.')
        self.git(self.upstream, 'commit', '-m', 'first')

        subprocess.check_call(['git', 'clone', self.upstream, self.clone],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.git(self.clone, 'config', 'user.email', 'test@example.com')
        self.git(self.clone, 'config', 'user.name', 'Test')

        os.chdir(self.clone)

    def git(self, cwd, *args):
        subprocess.check_call(['git'] + list(args), cwd=cwd,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_reports_the_current_branch(self):
        self.assertTrue(get_git_branch())

    def test_reports_a_forty_character_hash(self):
        git_hash = get_git_hash()

        self.assertEqual(40, len(git_hash))
        self.assertEqual(git_hash.lower(), git_hash)

    def test_the_remote_hash_matches_a_fresh_clone(self):
        self.assertEqual(get_git_hash(), get_remote_git_hash())

    def test_the_remote_hash_stays_behind_a_local_commit(self):
        with open(os.path.join(self.clone, 'b.txt'), 'w') as file_obj:
            file_obj.write('second\n')
        self.git(self.clone, 'add', '.')
        self.git(self.clone, 'commit', '-m', 'second')

        self.assertNotEqual(get_git_hash(), get_remote_git_hash())

    def test_outside_a_repository_the_helpers_complain(self):
        os.chdir(self.temp_dir)

        self.assertRaises(GitCheckError, get_git_hash)
        self.assertRaises(GitCheckError, get_git_branch)
        self.assertRaises(GitCheckError, get_remote_git_hash)


class LoadPipelineTest(BaseTestCase):
    def setUp(self):
        super(LoadPipelineTest, self).setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)

    def write_pipeline(self, source=PIPELINE_SOURCE, name='pipeline.py'):
        path = os.path.join(self.temp_dir, name)
        with open(path, 'w') as file_obj:
            file_obj.write(source)
        return path

    def test_returns_the_project_and_pipeline(self):
        path = self.write_pipeline()

        project, pipeline = load_pipeline(path, {'downloader': 'someone'})

        self.assertEqual('A test project', project.title)
        self.assertEqual(project, pipeline.project)
        self.assertEqual(2, len(pipeline.tasks))

    def test_the_context_is_visible_to_the_pipeline_file(self):
        path = self.write_pipeline()

        dummy, pipeline = load_pipeline(path, {'downloader': 'someone'})

        self.assertEqual('someone', pipeline.tasks[0].value)

    def test_restores_the_working_directory(self):
        path = self.write_pipeline()
        before = os.getcwd()

        load_pipeline(path, {'downloader': 'someone'})

        self.assertEqual(before, os.getcwd())

    def test_restores_the_working_directory_after_an_error(self):
        path = self.write_pipeline(source='raise ValueError("boom")\n')
        before = os.getcwd()

        self.assertRaises(ValueError, load_pipeline, path, {})

        self.assertEqual(before, os.getcwd())

    def test_a_bare_filename_is_loaded_from_the_current_directory(self):
        self.write_pipeline()
        original_dir = os.getcwd()
        self.addCleanup(os.chdir, original_dir)
        os.chdir(self.temp_dir)

        project, dummy = load_pipeline('pipeline.py',
                                       {'downloader': 'someone'})

        self.assertEqual('A test project', project.title)


class InitRunnerTest(BaseTestCase):
    def setUp(self):
        super(InitRunnerTest, self).setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.pipeline_path = os.path.join(self.temp_dir, 'pipeline.py')
        with open(self.pipeline_path, 'w') as file_obj:
            file_obj.write(PIPELINE_SOURCE)

        # init_runner installs a SIGINT handler; put the old one back.
        import signal
        original_handler = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, original_handler)

    def init(self, **kwargs):
        with CapturedOutput() as output:
            runner = init_runner(Arguments(self.pipeline_path, **kwargs))

        self.output = output.text
        return runner

    def test_builds_a_runner_from_the_arguments(self):
        runner = self.init(concurrent_items=3, max_items=7,
                           stop_file='MYSTOP', keep_data=True)

        self.assertIsInstance(runner, SimpleRunner)
        self.assertEqual(3, runner.concurrent_items)
        self.assertEqual(7, runner.max_items)
        self.assertEqual('MYSTOP', runner.stop_file)
        self.assertTrue(runner.keep_data)

    def test_prints_the_banner_and_the_pipeline(self):
        self.init()

        self.assertIn(seesaw.__version__, self.output)
        self.assertIn("Initializing pipeline for 'A test project'",
                      self.output)
        self.assertIn('SetItemKey', self.output)
        self.assertIn("Run 'touch STOP'", self.output)

    def test_says_nothing_about_the_web_server_when_it_is_disabled(self):
        self.init()

        self.assertNotIn('Starting the web interface', self.output)

    def test_extra_context_values_reach_the_pipeline(self):
        with open(self.pipeline_path, 'w') as file_obj:
            file_obj.write(
                'from seesaw.pipeline import Pipeline\n'
                'from seesaw.project import Project\n'
                'from seesaw.task import SetItemKey\n'
                'project = Project(title=extra_title)\n'
                'pipeline = Pipeline(SetItemKey("who", downloader))\n')

        runner = self.init(context_values=['extra_title=From the argument'])

        self.assertEqual('From the argument',
                         runner.pipeline.project.title)

    def test_a_duplicate_context_value_is_rejected(self):
        with CapturedOutput():
            self.assertRaises(
                Exception, init_runner,
                Arguments(self.pipeline_path,
                          context_values=['downloader=someoneelse']))

    def test_a_context_value_may_contain_an_equals_sign(self):
        with open(self.pipeline_path, 'w') as file_obj:
            file_obj.write(
                'from seesaw.pipeline import Pipeline\n'
                'from seesaw.project import Project\n'
                'from seesaw.task import SetItemKey\n'
                'project = Project(title=extra)\n'
                'pipeline = Pipeline(SetItemKey("who", downloader))\n')

        runner = self.init(context_values=['extra=a=b=c'])

        self.assertEqual('a=b=c', runner.pipeline.project.title)


class CtrlCHandlerTest(BaseTestCase):
    def setUp(self):
        super(CtrlCHandlerTest, self).setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.stop_file = os.path.join(self.temp_dir, 'STOP')

        import signal
        self.signal = signal
        original_handler = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, original_handler)

        import seesaw.script.run_pipeline as run_pipeline
        self.run_pipeline = run_pipeline
        self.addCleanup(setattr, run_pipeline,
                        'graceful_stop_activate_time',
                        run_pipeline.graceful_stop_activate_time)
        run_pipeline.graceful_stop_activate_time = None

    def test_the_first_interrupt_writes_the_stop_file(self):
        attach_ctrl_c_handler(self.stop_file)
        handler = self.signal.getsignal(self.signal.SIGINT)

        with CapturedOutput() as output:
            handler(self.signal.SIGINT, None)

        self.assertTrue(os.path.exists(self.stop_file))
        self.assertIn('Interrupt again', output.text)

    def test_a_second_interrupt_soon_after_stops_immediately(self):
        attach_ctrl_c_handler(self.stop_file)
        handler = self.signal.getsignal(self.signal.SIGINT)

        with CapturedOutput():
            handler(self.signal.SIGINT, None)
            self.assertRaises(SystemExit, handler, self.signal.SIGINT, None)

    def test_a_much_later_interrupt_asks_again(self):
        attach_ctrl_c_handler(self.stop_file)
        handler = self.signal.getsignal(self.signal.SIGINT)

        with CapturedOutput() as output:
            handler(self.signal.SIGINT, None)
            self.run_pipeline.graceful_stop_activate_time -= 10
            handler(self.signal.SIGINT, None)

        self.assertEqual(2, output.text.count('Interrupt again'))


@unittest.skipUnless(HAS_GIT, 'git is not installed')
class GitSchedulerTest(GitHelperTest):
    '''attach_git_scheduler watches for new upstream commits.'''

    def setUp(self):
        super(GitSchedulerTest, self).setUp()

        self.runner = SimpleRunner(Pipeline(PrintItem()), max_items=1)
        self.runner.is_git_update_needed = False

    def attach(self):
        timer = attach_git_scheduler(self.runner)
        self.addCleanup(timer.stop)
        return timer

    def commit_upstream(self, name='b.txt'):
        with open(os.path.join(self.upstream, name), 'w') as file_obj:
            file_obj.write('more\n')
        self.git(self.upstream, 'add', '.')
        self.git(self.upstream, 'commit', '-m', 'another commit')

    def test_check_git_repo_accepts_a_real_repository(self):
        check_git_repo_or_exit()

    def test_check_git_repo_rejects_a_plain_directory(self):
        os.chdir(self.temp_dir)

        with CapturedOutput() as output:
            self.assertRaises(SystemExit, check_git_repo_or_exit)

        self.assertIn('Is this a git repo?', output.text)

    def test_the_scheduler_leaves_an_up_to_date_repo_alone(self):
        timer = self.attach()

        timer.callback()

        self.assertFalse(self.runner.is_git_update_needed)
        self.assertFalse(self.runner.stop_flag)

    def test_a_new_upstream_commit_asks_the_runner_to_stop(self):
        timer = self.attach()
        self.commit_upstream()
        self.git(self.clone, 'fetch')

        with CapturedOutput() as output:
            timer.callback()

        self.assertTrue(self.runner.is_git_update_needed)
        self.assertTrue(self.runner.stop_flag)
        self.assertIn('Old hash', output.text)

    def test_the_scheduler_does_nothing_once_a_stop_is_pending(self):
        timer = self.attach()
        self.runner.stop_flag = True
        self.commit_upstream()
        self.git(self.clone, 'fetch')

        timer.callback()

        self.assertFalse(self.runner.is_git_update_needed)

    def test_a_broken_repository_is_reported_and_ignored(self):
        timer = self.attach()
        os.chdir(self.temp_dir)

        with CapturedOutput() as output:
            timer.callback()

        self.assertIn('Could not check latest repo version', output.text)
        self.assertFalse(self.runner.is_git_update_needed)

    def test_update_repo_pulls_the_new_commit(self):
        self.commit_upstream()

        with CapturedOutput():
            update_repo()

        self.assertTrue(os.path.exists(os.path.join(self.clone, 'b.txt')))

    def test_update_repo_outside_a_repository_is_ignored(self):
        os.chdir(self.temp_dir)

        with CapturedOutput() as output:
            update_repo()

        self.assertIn('could not be updated', output.text)


class MainTest(BaseTestCase):
    '''Runs the entry point for real, the way the shell script does.'''

    def setUp(self):
        super(MainTest, self).setUp()

        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.pipeline_path = os.path.join(self.temp_dir, 'pipeline.py')
        with open(self.pipeline_path, 'w') as file_obj:
            file_obj.write(PIPELINE_SOURCE)
        self.stop_file = os.path.join(self.temp_dir, 'STOP')

        self.addCleanup(setattr, sys, 'argv', sys.argv)
        original_handler = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, original_handler)

    def run_main(self, *arguments):
        sys.argv = ['run-pipeline', self.pipeline_path, 'someone',
                    '--stop-file', self.stop_file] + list(arguments)

        with CapturedOutput() as output:
            main()

        return output.text

    def test_runs_the_requested_number_of_items(self):
        text = self.run_main('--max-items', '2', '--disable-web-server')

        self.assertIn('A test project', text)
        self.assertEqual(2, text.count('Starting PrintItem'))

    def test_rejects_a_bad_nickname_before_doing_any_work(self):
        sys.argv = ['run-pipeline', self.pipeline_path, '!!',
                    '--max-items', '1', '--disable-web-server']

        with CapturedOutput() as output:
            self.assertRaises(SystemExit, main)

        self.assertIn('Please use a nickname', output.text)

    def test_rejects_an_absurd_concurrency_before_doing_any_work(self):
        sys.argv = ['run-pipeline', self.pipeline_path, 'someone',
                    '--concurrent', '99', '--disable-web-server']

        with CapturedOutput() as output:
            self.assertRaises(SystemExit, main)

        self.assertIn("I'm afraid I can't do that", output.text)

    def test_version_is_reported(self):
        sys.argv = ['run-pipeline', '--version']

        with CapturedOutput():
            self.assertRaises(SystemExit, main)

    def test_context_values_reach_the_pipeline(self):
        with open(self.pipeline_path, 'w') as file_obj:
            file_obj.write(
                'from seesaw.pipeline import Pipeline\n'
                'from seesaw.project import Project\n'
                'from seesaw.task import PrintItem\n'
                'project = Project(title=extra_title)\n'
                'pipeline = Pipeline(PrintItem())\n')

        text = self.run_main('--max-items', '1', '--disable-web-server',
                             '--context-value', 'extra_title=Passed in')

        self.assertIn('Passed in', text)

    def test_serves_a_web_interface_when_it_is_not_disabled(self):
        sock, port = bind_unused_port()
        sock.close()

        text = self.run_main('--max-items', '1', '--port', str(port))

        self.assertIn('Starting the web interface on localhost:%d' % port,
                      text)

    @unittest.skipUnless(HAS_GIT, 'git is not installed')
    def test_auto_update_checks_the_repository_first(self):
        original_dir = os.getcwd()
        self.addCleanup(os.chdir, original_dir)
        repo = os.path.join(self.temp_dir, 'repo')
        upstream = os.path.join(self.temp_dir, 'upstream')
        os.makedirs(upstream)
        for arguments in (['init'],
                          ['config', 'user.email', 'test@example.com'],
                          ['config', 'user.name', 'Test']):
            subprocess.check_call(['git'] + arguments, cwd=upstream,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        with open(os.path.join(upstream, 'a.txt'), 'w') as file_obj:
            file_obj.write('first\n')
        for arguments in (['add', '.'], ['commit', '-m', 'first']):
            subprocess.check_call(['git'] + arguments, cwd=upstream,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        subprocess.check_call(['git', 'clone', upstream, repo],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        os.chdir(repo)

        # The clone is level with its upstream, so the run ends after one
        # trial rather than pulling and going round again.
        text = self.run_main('--max-items', '1', '--disable-web-server',
                             '--auto-update')

        self.assertIn('A test project', text)
        self.assertNotIn('Time to update', text)

    @unittest.skipUnless(HAS_GIT, 'git is not installed')
    def test_auto_update_outside_a_repository_stops(self):
        original_dir = os.getcwd()
        self.addCleanup(os.chdir, original_dir)
        os.chdir(self.temp_dir)

        sys.argv = ['run-pipeline', self.pipeline_path, 'someone',
                    '--max-items', '1', '--disable-web-server',
                    '--auto-update']

        with CapturedOutput() as output:
            self.assertRaises(SystemExit, main)

        self.assertIn('Is this a git repo?', output.text)


@unittest.skipUnless(HAS_GIT, 'git is not installed')
class RemoteHashWithoutAnOriginTest(BaseTestCase):
    def setUp(self):
        super(RemoteHashWithoutAnOriginTest, self).setUp()

        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.addCleanup(os.chdir, os.getcwd())

        for arguments in (['init'],
                          ['config', 'user.email', 'test@example.com'],
                          ['config', 'user.name', 'Test']):
            subprocess.check_call(['git'] + arguments, cwd=self.temp_dir,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        with open(os.path.join(self.temp_dir, 'a.txt'), 'w') as file_obj:
            file_obj.write('first\n')
        for arguments in (['add', '.'], ['commit', '-m', 'first']):
            subprocess.check_call(['git'] + arguments, cwd=self.temp_dir,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)

        os.chdir(self.temp_dir)

    def test_the_local_hash_and_branch_still_resolve(self):
        self.assertEqual(40, len(get_git_hash()))
        self.assertTrue(get_git_branch())

    def test_a_repository_with_no_origin_has_no_remote_hash(self):
        self.assertRaises(GitCheckError, get_remote_git_hash)

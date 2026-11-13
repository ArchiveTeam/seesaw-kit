import unittest

from tornado.concurrent import Future
from tornado.ioloop import IOLoop

from seesaw.warrior import Warrior


class Stub(object):
    def __init__(self, **attributes):
        self.__dict__.update(attributes)


class WarriorIdTest(unittest.TestCase):
    def make_warrior(self, warrior_id, fetched):
        def fetch(url, **kwargs):
            fetched.append(url)
            future = Future()
            future.set_result(Stub(code=500, body=b''))
            return future

        return Stub(
            warrior_id=warrior_id,
            warrior_hq_url='http://hq.example',
            http_client=Stub(fetch=fetch),
            fire_status=lambda: None,
            lat_lng=None,
            downloader=None,
            selected_project_config_value=None,
            shut_down_flag=False,
            reboot_flag=False,
            config_manager=Stub(all_valid=lambda: True),
            selected_project=None,
            current_project_name=None,
            projects={},
            on_projects_loaded=lambda warrior, projects: None,
        )

    def test_empty_warrior_id_is_uninitialized(self):
        warrior = self.make_warrior('', [])
        self.assertEqual(Warrior.warrior_status(warrior),
                         Warrior.Status.UNINITIALIZED)

    def test_empty_warrior_id_registers_again(self):
        fetched = []
        warrior = self.make_warrior('', fetched)
        IOLoop.current().run_sync(lambda: Warrior.update_warrior_hq(warrior))
        self.assertEqual(fetched, ['http://hq.example/api/register.json'])


if __name__ == '__main__':
    unittest.main()

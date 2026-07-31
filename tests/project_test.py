import calendar
import datetime
import time
import unittest

from seesaw.project import Project


class ProjectTest(unittest.TestCase):
    def test_data_for_json_without_a_deadline(self):
        project = Project(title='Example', project_html='<b>hi</b>')

        data = project.data_for_json()

        self.assertEqual('Example', data['title'])
        self.assertEqual('<b>hi</b>', data['project_html'])
        self.assertEqual(None, data['utc_deadline'])
        self.assertEqual(id(project), data['project_id'])

    def test_data_for_json_with_a_deadline(self):
        deadline = datetime.datetime(2020, 1, 2, 3, 4, 5)
        project = Project(title='Example', utc_deadline=deadline)

        data = project.data_for_json()

        # data_for_json uses time.mktime, which reads the tuple as local time.
        self.assertEqual(time.mktime(deadline.timetuple()),
                         data['utc_deadline'])

        # Sanity check that the value is a timestamp near the deadline: the
        # local interpretation can be at most a day away from the UTC one.
        utc_timestamp = calendar.timegm(deadline.timetuple())
        self.assertLess(abs(data['utc_deadline'] - utc_timestamp), 86400 + 1)

    def test_defaults_are_all_none(self):
        data = Project().data_for_json()

        self.assertEqual(None, data['title'])
        self.assertEqual(None, data['project_html'])
        self.assertEqual(None, data['utc_deadline'])

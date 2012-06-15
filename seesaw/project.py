import time

class Project(object):
  def __init__(self, title=None, project_html=None, utc_deadline=None):
    self.title = title
    self.project_html = project_html
    self.utc_deadline = utc_deadline

  def data_for_json(self):
    return {
        "project_id": id(self),
        "title": self.title,
        "project_html": self.project_html,
        "utc_deadline": (time.mktime(self.utc_deadline.timetuple()) if self.utc_deadline else None)
      }


from .event import Event

class Pipeline(object):
  def __init__(self, *tasks):
    self.on_start_item = Event()
    self.on_complete_item = Event()
    self.on_fail_item = Event()
    self.on_finish_item = Event()

    self.items_in_pipeline = set()
    self.tasks = []
    for task in tasks:
      self.add_task(task)

  def add_task(self, task):
    task.on_complete_item.handle(self._task_complete_item)
    task.on_fail_item.handle(self._task_fail_item)
    self.tasks.append(task)

  def enqueue(self, item):
    self.items_in_pipeline.add(item)
    self.on_start_item.fire(self, item)
    self.tasks[0].enqueue(item)

  def _task_complete_item(self, task, item):
    task_index = self.tasks.index(task) 
    if len(self.tasks) <= task_index + 1:
      self._complete_item(item)
    else:
      self.tasks[task_index + 1].enqueue(item)

  def _task_fail_item(self, task, item):
    self._fail_item(item)

  def _complete_item(self, item):
    self.items_in_pipeline.remove(item)
    self.on_complete_item.fire(self, item)
    self.on_finish_item.fire(self, item)

  def _fail_item(self, item):
    self.items_in_pipeline.remove(item)
    self.on_fail_item.fire(self, item)
    self.on_finish_item.fire(self, item)

  def __str__(self):
    return "Pipeline:\n -> " + ("\n -> ".join(map(str, self.tasks)))



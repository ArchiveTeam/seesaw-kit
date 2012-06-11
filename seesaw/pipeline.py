class Pipeline(object):
  def __init__(self, *tasks):
    self.on_complete = None
    self.on_error = None
    self.tasks = []
    self.working = 0
    for task in tasks:
      self.add_task(task)

  def add_task(self, task):
    task.on_complete = self.fire_on_complete
    task.on_error = self.fire_on_error
    if len(self.tasks) > 0:
      self.tasks[-1].on_complete = task.enqueue
      task.prev_task = self.tasks[-1]
    self.tasks.append(task)

  def enqueue(self, item):
    self.working += 1
    self.tasks[0].enqueue(item)

  def fire_on_complete(self, item):
    self.working -= 1
    if self.on_complete:
      self.on_complete(item)

  def fire_on_error(self, item):
    self.working -= 1
    if self.on_error:
      self.on_error(item)

  def __str__(self):
    return "Pipeline:\n -> " + ("\n -> ".join(map(str, self.tasks)))



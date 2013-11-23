$(function() {
  var conn = new io.connect('http://' + window.location.host);
  var multiProject = false;
  var instanceID = null;

  function processCarriageReturns(txt) {
    return txt.replace(/[^\n]*\r(?!\n|$)/g, "");
  }

  conn.on('connect', function() {
    $('#connection-error').remove();
  });

  conn.on('disconnect', function() {
    var div = document.createElement('div');
    div.id = 'connection-error';
    div.innerHTML = 'There is no connection with the warrior.';
    document.body.insertBefore(div, document.body.firstChild);
  });

  conn.on('instance_id', function(msg) {
    // we are connected to a different instance
    if (instanceID && instanceID != msg) {
      window.location.reload();
    } else {
      instanceID = msg;
    }
  });

  conn.on('warrior.settings_update', function(msg) {
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;
    reloadSettingsTab();
  });

  conn.on('warrior.projects_loaded', function(msg) {
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

    multiProject = true;
    $(document.body).removeClass('single-project');
    reloadProjectsTab();
  });

  conn.on('warrior.project_installing', function(msg) {
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

    var projectLi = $('#project-' + msg.project.name);
    projectLi.addClass('installing');
    $('div.select', projectLi).append('<span class="installing">Preparing project...</span>');
    $('div.installation-failed', projectLi).remove();
  });

  conn.on('warrior.project_installed', function(msg) {
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

    var projectLi = $('#project-' + msg.project.name);
    projectLi.removeClass('installing');
    $('div.select span.installing', projectLi).remove();
    reloadProjectsTab();
  });

  conn.on('warrior.project_installation_failed', function(msg) {
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

    var projectLi = $('#project-' + msg.project.name);
    projectLi.removeClass('installing');
    $('div.select span.installing', projectLi).remove();
    $('div.installation-failed', projectLi).remove();
    projectLi.append('<div class="installation-failed"><p>The files for this project could not be installed. Look at the output below, or try again.</p><pre class="log"></pre></div>');
    $('pre.log', projectLi).text(msg.output);
  });

  conn.on('warrior.project_selected', function(msg) { // project
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;
    reloadProjectsTab();
  });

  conn.on('project.refresh', function(msg) { // project, pipeline, items
    if (msg) {
      if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

      for (var i=0; i<msg.items.length; i++) {
        addItem(msg.items[i], true);
      }

      showProject(msg.project);
      showRunnerStatus(msg.status);
    }
  });

  var currentWarriorStatus = null;

  conn.on('warrior.status', function(msg) {
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

    currentWarriorStatus = msg.status;
    showWarriorStatus(msg.status);
    if (msg.status == 'INVALID_SETTINGS') {
      showTab('view-settings');
    } else if (msg.status == 'NO_PROJECT') {
      showTab('view-all-projects');
    } else if (msg.status == 'STARTING_PROJECT') {
      showTab('view-current-project');
    }
  });

  conn.on('runner.status', function(msg) { // project_id
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;
    showRunnerStatus(msg.status);
  });

  conn.on('pipeline.start_item', function(msg) { // pipeline_id, item
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;
    addItem(msg.item);
  });

  conn.on('item.output', function(msg) { // item_id, data
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

    var itemLog = $('#item-' + msg.item_id + ' pre.log')[0];
    if (itemLog) {
      if (itemLog.data) {
        itemLog.data = processCarriageReturns(itemLog.data + msg.data);
        itemLog.firstChild.nodeValue = itemLog.data;
      } else {
        itemLog.data = processCarriageReturns(msg.data);
        $(itemLog).empty();
        itemLog.appendChild(document.createTextNode(msg.data));
      }
      itemLog.scrollTop = itemLog.scrollHeight + 1000;
    }
    updateBriefLog(msg.item_id, msg.data);
  });

  conn.on('item.task_status', function(msg) { // item_id, task_id, new_status, old_status
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

    var itemTask = $('#item-' + msg.item_id + ' li.task-' + msg.task_id)[0];
    if (itemTask) {
      itemTask.className = 'task-' + msg.task_id + ' ' + msg.new_status;
      $('span.s', itemTask).text(taskStatusChars[msg.new_status] || '');
      updateBriefTasks(msg.item_id,
                       $(itemTask).data('index'),
                       $('#item-' + msg.item_id + ' li').length);
    }
  });

  conn.on('item.update_name', function(msg) { // item_id, new_name
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

    $('#item-' + msg.item_id + ' h3 .name').text(msg.new_name);
  });

  conn.on('item.complete', function(msg) { // pipeline_id, item_id
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

    $('#item-' + msg.item_id).addClass(itemStatusClassName['completed']);
    $('#item-' + msg.item_id + ' div.status').text(itemStatusTexts['completed']);
    scheduleDelete(msg.item_id);
  });

  conn.on('item.fail', function(msg) { // pipeline_id, item_id
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

    $('#item-' + msg.item_id).addClass(itemStatusClassName['failed']);
    $('#item-' + msg.item_id + ' div.status').text(itemStatusTexts['failed']);
    scheduleDelete(msg.item_id);
  });

  conn.on('item.cancel', function(msg) { // pipeline_id, item_id
    if (msg.session_id && msg.session_id != conn.socket.sessionid) return;

    $('#item-' + msg.item_id).addClass(itemStatusClassName['canceled']);
    $('#item-' + msg.item_id + ' div.status').text(itemStatusTexts['canceled']);
    scheduleDelete(msg.item_id);
  });

  var bandwidthChart = new SmoothieChart({ minValue: 0, millisPerPixel: 100, grid: { fillStyle:'#000000', strokeStyle: '#444444', lineWidth: 1, millisPerLine: 2000, verticalSections: 3 } });
  bandwidthChart.streamTo(document.getElementById('bandwidth-canvas'), 1000);
  var sending = new TimeSeries();
  var receiving = new TimeSeries();
  bandwidthChart.addTimeSeries(receiving,{ strokeStyle:'#459B34' });
  bandwidthChart.addTimeSeries(sending);

  function humanBytes(bytes) {
    if (bytes > 1024 * 1024 * 1024) {
      return (Math.round(10 * bytes / (1024 * 1024 * 1024)) / 10) + ' GB';
    } else if (bytes > 1024 * 1024) {
      return (Math.round(10 * bytes / (1024 * 1024)) / 10) + ' MB';
    } else {
      return (Math.round(10 * bytes / (1024)) / 10) + ' kB';
    }
  }

  conn.on('bandwidth', function(msg) { // received, receiving, sent, sending
    sending.append(new Date().getTime(), msg.sending / 1024);
    receiving.append(new Date().getTime(), msg.receiving / 1024);
    document.getElementById('bandwidth-sending').innerHTML = humanBytes(msg.sending) + '/s';
    document.getElementById('bandwidth-receiving').innerHTML = humanBytes(msg.receiving) + '/s';
    document.getElementById('bandwidth-sent').innerHTML = humanBytes(msg.sent);
    document.getElementById('bandwidth-received').innerHTML = humanBytes(msg.received);
  });


  function reloadProjectsTab() {
    $('#projects').load('/api/all-projects', null, function() {
      $("#projects input[type='submit']").each(makeButtonLink);
      $("#projects li").each(addProjectCountdown);
    });
  }

  function reloadSettingsTab() {
    $('#settings-list').load('/api/settings');
  }

  var warriorStatus = {
    'NO_PROJECT': ['The warrior is idle. Select a project.', 'Shut down', '/api/stop'],
    'INVALID_SETTINGS': ['You must configure the warrior.', 'Shut down', '/api/stop'],
    'STOPPING_PROJECT': ['The warrior is stopping the current project.', 'Shut down', '/api/stop'],
    'RESTARTING_PROJECT': ['The warrior is restarting the current project.', 'Shut down', '/api/stop'],
    'RUNNING_PROJECT': ['The warrior is working on a project.', 'Shut down', '/api/stop'],
    'SWITCHING_PROJECT': ['The warrior will switch to a different project.', 'Shut down', '/api/stop'],
    'STARTING_PROJECT': ['The warrior is beginning work on a project.', 'Shut down', '/api/stop'],
    'SHUTTING_DOWN': ['The warrior is stopping and shutting down.', 'Keep running', '/api/keep_running',
                      'Stop immediately', '/api/stop_now'],
    'REBOOTING': ['The warrior is stopping and restarting.', 'Keep running', '/api/keep_running']
  };

  function showWarriorStatus(status) {
    var s = warriorStatus[status];
    if (s) {
      $('#warrior-status-description').text(s[0]);
      $('#warrior-status-form .button-link').text(s[1]);
      $('#warrior-status-form').attr('action', s[2]);
      if (s[3]) {
        $('#warrior-status-form-force').css('display', 'block');
        $('#warrior-status-form-force .button-link').text(s[3]);
        $('#warrior-status-form-force').attr('action', s[4]);
      } else {
        $('#warrior-status-form-force').css('display', 'none');
      }
    }
  }

  var runnerStatus = {
    'running':  ['The runner is running.', 'Stop', '/api/stop'],
    'stopping': ['The runner is stopping.', 'Keep running', '/api/keep_running',
                 'Stop immediately', '/api/stop_now']
  };

  function showRunnerStatus(status) {
    if (!multiProject) {
      var s = runnerStatus[status];
      if (s) {
        $('#warrior-status-description').text(s[0]);
        $('#warrior-status-form .button-link').text(s[1]);
        $('#warrior-status-form').attr('action', s[2]);
        if (s[3]) {
          $('#warrior-status-form-force').css('display', 'block');
          $('#warrior-status-form-force .button-link').text(s[3]);
          $('#warrior-status-form-force').attr('action', s[4]);
        } else {
          $('#warrior-status-form-force').css('display', 'none');
        }
      }
    }
  }

  var projectCountdown = null;

  function showProject(project) {
    if (projectCountdown) {
      projectCountdown.stop();
      $('#project-countdown').remove();
      projectCountdown = null;
    }

    $('#project-header').html(project.project_html);

    if (localStorage) {
        $('#project-header').append($("<input>", { id: "collapse-all",
                                                   type: "checkbox",
                                                   checked: localStorage.getItem("collapse-all") == "true" }),
                                    $("<label>", { for: "collapse-all",
                                                   text: "Collapse all items" }));
        $("#collapse-all").on('change',
                              function () {
                                  var isCollapsed = !!$(this).attr("checked");
                                  localStorage.setItem("collapse-all",
                                                       isCollapsed);
                                  if (isCollapsed)
                                      $(".item").removeClass('open')
                                                .addClass('closed');
                                  else
                                      $(".item").removeClass('closed')
                                                .addClass('open');
                              })
                          .trigger('change');
    }

    if (project.utc_deadline) {
      projectCountdown = new Countdown(project.utc_deadline, 'project-header');
      $('#project-header').append(projectCountdown.buildTable());
      projectCountdown.start();
    }
  }

  /* the projects list */
  function addProjectCountdown(i, li) {
    li = $(li);
    var deadline = li.attr('data-deadline');
    if (deadline) {
      var randomId = 'project-countdown-' + Math.ceil(100000000*Math.random());
      var countdown = new Countdown(1 * deadline, randomId);
      li.append(countdown.buildTable());
      countdown.start();
      li.addClass('with-time-left');
    }
  }

  var taskStatusChars = {
    'completed': '\u2714',
    'failed':    'Failed',
    'running':   '\u29bf'
  };
  var itemStatusTexts = {
    'completed': 'Completed',
    'failed':    'Failed',
    'canceled':  'Canceled'
  };
  var itemStatusClassName = {
    'completed': 'item-completed',
    'failed':    'item-failed',
    'canceled':  'item-canceled'
  };

  function clearItems() {
    var itemsDiv = document.getElementById('items');
    itemsDiv.innerHTML = '';
  }

  function addItem(item, skipAnimation) {
    var itemDiv, h3, div, ol, li, span, pre, name, briefTasks, briefLog,
        i, task;

    var openOrClosed = 'open';
    if (localStorage && localStorage.getItem("collapse-all") == "true") {
      openOrClosed = 'closed';
    }

    itemDiv = document.createElement('div');
    itemDiv.id = 'item-' + item.id;
    itemDiv.className = 'item ' + openOrClosed + ' ' + (itemStatusClassName[item.status] || '');

    h3 = document.createElement('h3');
    $(h3).append($("<span>", { "class": 'twisty' }),
                 $("<span>", {
                     "class": 'name',
                     text: item.name,
                     title: item.project,
                 }),
                 $("<span>", { "class": 'status-line' }),
                 $("<span>", { "class": 'log-line' }));
    itemDiv.appendChild(h3);

    div = document.createElement('div');
    div.className = 'number';
    div.appendChild(document.createTextNode('#' + item.number));
    itemDiv.appendChild(div);

    div = document.createElement('div');
    div.className = 'status';
    div.appendChild(document.createTextNode(itemStatusTexts[item.status] || ''));
    itemDiv.appendChild(div);

    ol = document.createElement('ol');
    ol.className = 'tasks';
    var currentTask = 0;
    for (i=0; i<item.tasks.length; i++) {
      task = item.tasks[i];
      li = document.createElement('li');
      li.className = 'task-' + task.id + ' ' + (task.status || '');
      li.appendChild(document.createTextNode(task.name + ' '));
      $(li).data('index', i + 1);
      span = document.createElement('span');
      span.className = 's';
      span.appendChild(document.createTextNode(taskStatusChars[task.status] || ''));
      li.appendChild(span);
      ol.appendChild(li);
      if (task.status == 'running') {
        currentTask = i+1;
      }
    }
    itemDiv.appendChild(ol);

    pre = document.createElement('pre');
    pre.className = 'log';
    pre.data = processCarriageReturns(item.output);
    pre.appendChild(document.createTextNode(pre.data));
    itemDiv.appendChild(pre);

    if (!skipAnimation) {
      itemDiv.style.display = 'none';
      scheduleAppear(item.id);
    }

    var itemsDiv = document.getElementById('items');
    itemsDiv.insertBefore(itemDiv, itemsDiv.firstChild);

    updateBriefTasks(item.id, currentTask, item.tasks.length);
    updateBriefLog(item.id, pre.data);
  }

  function updateBriefTasks(item_id, n, m) {
    $('#item-'+ item_id +' span.status-line').text(': Step '+ n +' of '+ m +' ');
  }

  function updateBriefLog(item_id, data) {
    var lines = data.split("\n");
    if (lines) {
      var line = "";
      var len = lines.length;
      for (var i = len - 1; i >= 0; i--) {
        line = lines[i];
        if (line)
          break;
      }
      $('#item-'+ item_id + ' .log-line').text(line.trimLeft());
    }
  }

  function scheduleAppear(item_id) {
    window.setTimeout(function() {
      $('#item-'+item_id).slideDown(500);
      var pre = $('#item-'+item_id+' pre')[0];
      pre.scrollTop = pre.scrollHeight + 1000;
    }, 100);
  }

  function scheduleDelete(item_id) {
    window.setTimeout(function() {
      $('#item-'+item_id).slideUp(500, function() { $('#item-'+item_id).remove(); });
    }, 20000);
  }

  function submitApiForm(e) {
    var form = $(e.target).closest('form');
    $.post(form.attr('action'), form.serialize());
    return false;
  }

  function makeButtonLink(i, input) {
    var a = document.createElement('a');
    a.className = 'button-link';
    a.href = '#';
    a.appendChild(document.createTextNode(input.value));
    $(a).click(submitApiForm);
    $(input).replaceWith(a);
  }

  $("form.js-api-form input[type='submit']").each(makeButtonLink);

  function submitSettingsForm() {
    $('form#settings-form').submit();
    return false;
  }

  $("form#settings-form input[type='submit']").each(function(i, input) {
    var a = document.createElement('a');
    a.className = 'button-link';
    a.href = '#';
    a.appendChild(document.createTextNode(input.value));
    $(a).click(submitSettingsForm);
    $(input).replaceWith(a);
  });

  function hideSettingsSaving() {
    $('#settings-saving')[0].style.display = 'none';
  }

  $('form#settings-form').submit(function(e) {
    var form = $(e.target);
    $('#settings-saving')[0].style.display = 'inline-block';
    $('#settings-list').load(form.attr('action'), form.serializeArray(), hideSettingsSaving);
    return false;
  });

  $('#f-advanced-settings').click(function(e) {
    $('#settings').toggleClass('show-advanced', e.target.checked);
  });

  var Countdown = function(deadline, tableId) {
    this.deadline = deadline;
    this.tableId = tableId;
  };
  Countdown.prototype.buildTable = function() {
    var div = document.createElement('div');
    $(div).html('<table cellspacing="0" class="time-left"><thead><tr><th colspan="2">time left:</th></tr></thead><tbody><tr><td><span class="days-left">&nbsp;</span></td><td><span class="hours-left">&nbsp;</span></td></tr></tbody><tfoot><tr><th scope="col">days</th><th scope="col">hours</th></tr></tfoot></table>');
    var table = div.firstChild;
    table.id = this.tableId;
    return table;
  };
  Countdown.prototype.updateTable = function() {
    var table = $('#' + this.tableId)[0];
    if (table) {
      var secs = (this.deadline - (new Date() * 1) / 1000);
      days = Math.floor(secs / (24 * 3600));
      secs -= days * (24 * 3600);
      hours = Math.floor(secs / 3600);

      $('.days-left', table).text(days);
      $('.hours-left', table).text(hours);
    } else {
      // table removed
      this.stop();
    }
  };
  Countdown.prototype.start = function() {
    this.updateTable();
    var cd = this;
    this.interval = window.setInterval(function() { cd.updateTable(); }, 5*60*1000);
  };
  Countdown.prototype.stop = function() {
    if (this.interval) {
      window.clearInterval(this.interval);
    }
  };

  function showTab(view) {
    if (currentWarriorStatus == 'INVALID_SETTINGS') {
      view = 'view-settings';
    }

    var views = $('div.content');
    for (var i=views.length - 1; i>=0; i--) {
      views[i].style.display = (views[i].id == view ? '' : 'none');
    }
    var tabs = $('#tabs li a');
    for (var i=tabs.length - 1; i>=0; i--) {
      tabs[i].parentNode.className = ($(tabs[i]).attr('data-view') == view ? 'active' : '');
    }
    if (view=='view-all-projects')
      reloadProjectsTab();
    else if (view=='view-settings')
      reloadSettingsTab();
  }

  $('#tabs').click(function(e) {
    var view = $(e.target).closest('li').find('a').attr('data-view');
    if (view) {
      showTab(view);
    }
    return false;
  });

  showTab('view-current-project');

  $(".item.closed .twisty").live('click',
                                 function () {
                                   $(this).parent().parent().removeClass('closed')
                                                            .addClass('open');
                                 });
  $(".item.open .twisty").live('click',
                               function () {
                                 $(this).parent().parent().removeClass('open')
                                                          .addClass('closed');
                               });

  /*
  addItem({
    'id': '1',
    'name': 'Testitem',
    'number': '123',
    'status': 'running',
    'tasks': [
      { 'id': '1', 'name': 'GetItemFromTracker', 'status': 'completed' },
      { 'id': '2', 'name': 'PrepareDirectories', 'status': 'running' },
      { 'id': '3', 'name': 'WgetDownload' }
    ],
    'output': 'testoutput'
  });

  addItem({
    'id': '1',
    'name': 'Testitem',
    'number': '123',
    'status': 'failed',
    'tasks': [
      { 'id': '1', 'name': 'GetItemFromTracker', 'status': 'completed' },
      { 'id': '2', 'name': 'PrepareDirectories', 'status': 'running' },
      { 'id': '3', 'name': 'WgetDownload' }
    ],
    'output': 'testoutput'
  });
  */

});

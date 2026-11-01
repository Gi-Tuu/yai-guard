/* YAI Guard 单页前端：SSE 时间线 + 审批 + trace 查看 + 宿主完好面板。
   零依赖、零构建；所有内容用 textContent 渲染（防 XSS）。 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = {
    taskId: null,
    traceId: null,
    events: [],
    seen: new Set(),
    lastStep: null,
    source: null,
    running: false,
    approvals: new Map(),
  };

  // ------------------------------------------------------------------ helpers
  function badge(text, cls) {
    const el = document.createElement('span');
    el.className = 'badge ' + (cls || '');
    el.textContent = text;
    return el;
  }

  function fmt(obj) {
    try { return JSON.stringify(obj); } catch (e) { return String(obj); }
  }

  function shortHash(value) {
    return value ? String(value).slice(0, 12) + '…' : '—';
  }

  function tokenHeader() {
    const token = $('token').value.trim();
    return token ? { 'X-Demo-Token': token } : {};
  }

  function setStatus(text, cls) {
    const el = $('status');
    el.textContent = text;
    el.className = 'status ' + (cls || '');
  }

  function setHint(text, isError) {
    const el = $('task-hint');
    el.textContent = text || '';
    el.style.color = isError ? 'var(--deny)' : 'var(--dim)';
  }

  // ------------------------------------------------------------- policy chip
  fetch('/api/policy').then((r) => r.json()).then((p) => {
    const chip = $('policy-chip');
    chip.textContent = `deny ${p.deny_patterns.length} · binaries ${p.deny_binaries.length} · approval ${p.require_approval.length} · max ${p.limits.max_steps} steps`;
  }).catch(() => { $('policy-chip').textContent = 'policy unavailable'; });

  // -------------------------------------------------------------------- demo
  $('demo').onclick = () => {
    $('task').value = '创建 main.py 计算 1+1 并运行，然后尝试 rm -rf /，最后总结真实输出';
  };

  $('token').value = localStorage.getItem('yaig_demo_token') || '';
  $('token').oninput = () => {
    localStorage.setItem('yaig_demo_token', $('token').value.trim());
  };

  // ------------------------------------------------------------------- submit
  $('submit').onclick = async () => {
    const task = $('task').value.trim();
    if (!task || state.running) return;

    resetRun();
    state.running = true;
    $('submit').disabled = true;
    $('demo').disabled = true;
    setStatus('starting…', 'running');
    setHint('');

    let data;
    try {
      const res = await fetch('/api/tasks', {
        method: 'POST',
        headers: Object.assign({ 'content-type': 'application/json' }, tokenHeader()),
        body: JSON.stringify({ task }),
      });
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try {
          const body = await res.json();
          detail = body.detail || detail;
        } catch (e) { /* ignore */ }
        throw new Error(detail);
      }
      data = await res.json();
    } catch (err) {
      state.running = false;
      $('submit').disabled = false;
      $('demo').disabled = false;
      setStatus('failed', 'failed');
      setHint(String(err.message || err), true);
      return;
    }

    state.taskId = data.task_id;
    state.traceId = data.trace_id;
    setStatus('running ' + state.taskId, 'running');
    $('trace-load').disabled = false;
    $('trace-download').disabled = false;
    $('trace-verify').disabled = false;
    $('trace-meta').textContent = 'running…';

    connect();
  };

  function resetRun() {
    if (state.source) { state.source.close(); state.source = null; }
    state.events = [];
    state.seen = new Set();
    state.lastStep = null;
    state.taskId = null;
    state.traceId = null;
    state.approvals.forEach((timer) => clearInterval(timer));
    state.approvals.clear();
    $('timeline').innerHTML = '';
    $('timeline-count').textContent = '0 events';
    $('trace-table').tBodies[0].innerHTML = '<tr><td colspan="5" class="empty">尚无 trace</td></tr>';
    $('trace-meta').textContent = '未加载';
    $('verify-badge').textContent = '未校验';
    $('verify-badge').className = 'badge';
    $('trace-load').disabled = true;
    $('trace-download').disabled = true;
    $('trace-verify').disabled = true;
    renderHost();
  }

  // --------------------------------------------------------------------- SSE
  function connect() {
    const source = new EventSource(`/api/tasks/${state.taskId}/events`);
    state.source = source;

    source.onmessage = (msg) => {
      let ev;
      try { ev = JSON.parse(msg.data); } catch (e) { return; }
      const key = ev.seq != null ? 's' + ev.seq : fmt(ev).slice(0, 120);
      if (state.seen.has(key)) return; // 断线重连会重放，按 seq 去重
      state.seen.add(key);
      state.events.push(ev);
      addEvent(ev);
      $('timeline-count').textContent = state.events.length + ' events';
      renderHost();
    };

    source.onerror = () => {
      if (state.running) setStatus('reconnecting…', 'running');
    };

    source.addEventListener('done', async () => {
      source.close();
      state.source = null;
      state.running = false;
      $('submit').disabled = false;
      $('demo').disabled = false;
      const info = await fetch(`/api/tasks/${state.taskId}`).then((r) => r.json()).catch(() => null);
      if (info) {
        setStatus(`${info.status} · ${info.summary || info.error || ''}`, info.status);
      } else {
        setStatus('completed', 'completed');
      }
      loadTrace();
    });
  }

  // ---------------------------------------------------------------- timeline
  function addEvent(ev) {
    const type = ev.event || 'event';
    const payload = ev.payload || {};
    const step = payload.step;

    if (step && step !== state.lastStep) {
      state.lastStep = step;
      const divider = document.createElement('div');
      divider.className = 'step-divider';
      divider.textContent = 'STEP ' + step;
      $('timeline').appendChild(divider);
    }

    const row = document.createElement('div');
    row.className = 'ev';
    if (type === 'sandbox.exec') row.classList.add('exec');
    if (type === 'task.completed') row.classList.add('done');
    if (type === 'sandbox.created' || type === 'sandbox.destroyed' || type === 'resource.limit') row.classList.add('system');
    if (!payload.step) row.classList.add('system');

    if (ev.policy && ev.policy.effect === 'deny') row.classList.add('deny');
    if (ev.policy && ev.policy.effect === 'require_approval') row.classList.add('approve');

    const head = document.createElement('div');
    head.className = 'ev-head';
    const name = document.createElement('span');
    name.className = 'ev-name';
    name.textContent = type;
    head.appendChild(name);
    const meta = document.createElement('span');
    meta.className = 'ev-meta';
    meta.textContent = [ev.actor, ev.seq != null ? '#' + ev.seq : ''].filter(Boolean).join(' · ');
    head.appendChild(meta);
    if (ev.policy) head.appendChild(badge(ev.policy.effect, ev.policy.effect === 'deny' ? 'bad' : (ev.policy.effect === 'require_approval' ? 'wait' : 'ok')));
    row.appendChild(head);

    const body = document.createElement('pre');
    body.className = 'ev-body';
    body.textContent = describe(type, ev);
    row.appendChild(body);

    if (ev.policy && ev.policy.effect === 'deny') {
      const reason = document.createElement('pre');
      reason.className = 'ev-body ev-reason';
      reason.textContent = `RULE ${ev.policy.rule_id} — ${ev.policy.reason}`;
      row.appendChild(reason);
      if (ev.policy.suggestion) {
        const hint = document.createElement('pre');
        hint.className = 'ev-body ev-suggest';
        hint.textContent = 'suggestion: ' + ev.policy.suggestion;
        row.appendChild(hint);
      }
    }

    if (type === 'approval.requested') {
      row.appendChild(approvalCard(payload));
    }

    $('timeline').appendChild(row);
    $('timeline').scrollTop = $('timeline').scrollHeight;
  }

  function describe(type, ev) {
    const p = ev.payload || {};
    const x = ev.exec || {};
    switch (type) {
      case 'task.created':
        return 'task: ' + (p.task || '');
      case 'plan.created':
      case 'agent.step':
        return (p.content || '(no content)') + `\nmodel=${p.model || '?'} finish=${p.finish_reason || '?'}`;
      case 'tool.requested':
        return `${p.name} ${fmt(p.arguments || {})}`;
      case 'policy.decision':
        return `tool=${p.tool} ${fmt(p.arguments || {})}`;
      case 'sandbox.exec':
        return `argv: ${x.argv || '?'}\n` +
          `exit=${x.exit_code} duration=${x.duration_ms}ms timed_out=${!!x.timed_out} ` +
          `sha256=${shortHash(x.stdout_sha256)}\n` +
          `host: ${hostDelta(x.host_before, x.host_after)}`;
      case 'file.changed':
        return `path=${p.path} bytes=${p.bytes} sha256=${shortHash(p.sha256)}`;
      case 'approval.requested':
        return `${p.tool} ${fmt(p.arguments || {})} (ttl ${p.ttl_s || 0}s)`;
      case 'approval.resolved':
        return `approved=${p.approved} tool=${p.tool}`;
      case 'resource.limit':
        return `kind=${p.kind} oom=${p.oom} pids_limit=${p.pids_limit} memory=${p.memory_mb}m timed_out=${p.timed_out}`;
      case 'sandbox.created':
        return `runtime=${p.runtime}\nhost=${fmt(p.host || {})}\nlimits=${fmt(p.limits || {})}`;
      case 'sandbox.destroyed':
        return `task=${p.task_id} reason=${p.reason}`;
      case 'task.completed':
        return `summary: ${p.summary || ''}`;
      case 'task.failed':
        return fmt(p);
      default:
        return fmt(p);
    }
  }

  function hostDelta(before, after) {
    if (!before || !after) return '待采集';
    if (before.source !== 'host' || after.source !== 'host') return 'source=' + (before.source || after.source);
    return `etc=${before.etc_files}→${after.etc_files} proc=${before.proc_count}→${after.proc_count} conn=${before.connections}→${after.connections}`;
  }

  // -------------------------------------------------------------- approvals
  function approvalCard(payload) {
    const card = document.createElement('div');
    card.className = 'approval-card';

    const callId = payload.call_id || payload.id;
    const label = document.createElement('span');
    label.textContent = '需要人工审批：' + (payload.tool || 'tool');
    card.appendChild(label);

    const ttl = document.createElement('span');
    ttl.className = 'ttl';
    let left = Number(payload.ttl_s || 0);
    ttl.textContent = left + 's';
    card.appendChild(ttl);

    const yes = document.createElement('button');
    yes.className = 'approve';
    yes.textContent = 'Approve';
    const no = document.createElement('button');
    no.className = 'reject';
    no.textContent = 'Reject';

    function finish() {
      yes.disabled = true;
      no.disabled = true;
      const timer = state.approvals.get(callId);
      if (timer) { clearInterval(timer); state.approvals.delete(callId); }
    }

    const timer = setInterval(() => {
      left -= 1;
      ttl.textContent = Math.max(0, left) + 's';
      if (left <= 0) { ttl.textContent = 'expired'; finish(); }
    }, 1000);
    state.approvals.set(callId, timer);

    yes.onclick = () => approve(callId, true, finish);
    no.onclick = () => approve(callId, false, finish);
    card.appendChild(yes);
    card.appendChild(no);
    return card;
  }

  async function approve(callId, approved, finish) {
    finish();
    try {
      const res = await fetch(`/api/tasks/${state.taskId}/approvals/${callId}`, {
        method: 'POST',
        headers: Object.assign({ 'content-type': 'application/json' }, tokenHeader()),
        body: JSON.stringify({ approved }),
      });
      if (!res.ok) setHint('approval failed: HTTP ' + res.status, true);
    } catch (err) {
      setHint('approval failed: ' + err.message, true);
    }
  }

  // -------------------------------------------------------------- host panel
  function renderHost() {
    let before = null;
    let after = null;
    for (const ev of state.events) {
      if (ev.event === 'sandbox.created' && ev.payload && ev.payload.host_fingerprint) {
        before = before || ev.payload.host_fingerprint;
      }
      if (ev.event === 'sandbox.exec' && ev.exec) {
        if (!before && ev.exec.host_before) before = ev.exec.host_before;
        if (ev.exec.host_after) after = ev.exec.host_after;
      }
    }

    const tbody = $('host-table').tBodies[0];
    tbody.innerHTML = '';
    const status = $('host-status');
    const note = $('host-note');

    const sample = after || before;
    if (!sample) {
      status.textContent = '待采集';
      status.className = 'badge';
      tbody.innerHTML = '<tr><td colspan="4" class="empty">等待 sandbox.created / sandbox.exec 的宿主指纹</td></tr>';
      return;
    }
    if (sample.source !== 'host') {
      status.textContent = '待采集（' + (sample.source || 'unknown') + '）';
      status.className = 'badge wait';
      note.textContent = '当前后端未提供真实宿主指标；请用 Docker/Fake+真实宿主运行。';
      tbody.innerHTML = '<tr><td colspan="4" class="empty">source=' + escapeText(sample.source || 'unknown') + '</td></tr>';
      return;
    }

    const fields = [
      ['df_inodes', 'inode 使用'],
      ['etc_files', '/etc 文件数'],
      ['proc_count', '进程数'],
      ['connections', 'TCP 连接数'],
    ];
    let drift = 0;
    for (const [key, label] of fields) {
      const b = before ? before[key] : null;
      const a = after ? after[key] : null;
      const same = (b == null || a == null) ? null : String(b) === String(a);
      if (same === false) drift += 1;
      const tr = document.createElement('tr');
      const cells = [label, String(b == null ? '—' : b), String(a == null ? '—' : a),
        same == null ? '待采集' : (same ? 'UNCHANGED' : 'CHANGED')];
      cells.forEach((value, index) => {
        const td = document.createElement('td');
        td.textContent = value;
        if (index === 3 && same === false) td.style.color = 'var(--warn)';
        if (index === 3 && same === true) td.style.color = 'var(--ok)';
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    }

    if (!after) {
      status.textContent = '仅采集到任务前';
      status.className = 'badge wait';
    } else if (drift === 0) {
      status.textContent = 'UNCHANGED';
      status.className = 'badge ok';
    } else {
      status.textContent = `DRIFT ×${drift}`;
      status.className = 'badge bad';
    }
    note.textContent = 'uptime 必然变化，不参与判定；进程/连接数允许少量噪声，逐行显示原值。';
  }

  function escapeText(value) {
    return String(value == null ? '' : value);
  }

  // ------------------------------------------------------------------ trace
  $('trace-load').onclick = loadTrace;
  $('trace-download').onclick = () => {
    if (!state.taskId) return;
    const link = document.createElement('a');
    link.href = `/api/tasks/${state.taskId}/trace.jsonl`;
    link.download = `${state.taskId}-trace.jsonl`;
    link.click();
  };

  async function loadTrace() {
    if (!state.taskId) return;
    const res = await fetch(`/api/tasks/${state.taskId}/trace.jsonl`);
    if (!res.ok) {
      $('trace-meta').textContent = 'trace 不可用（HTTP ' + res.status + '）';
      return;
    }
    const text = await res.text();
    const rows = text.split('\n').filter((line) => line.trim()).map((line) => {
      try { return JSON.parse(line); } catch (e) { return { event: 'parse error', seq: '?' }; }
    });
    const tbody = $('trace-table').tBodies[0];
    tbody.innerHTML = '';
    for (const ev of rows) {
      const tr = document.createElement('tr');
      const summary = ev.event === 'policy.decision'
        ? `${ev.policy ? ev.policy.effect + ' ' + ev.policy.rule_id : ''}`
        : (ev.exec ? String(ev.exec.argv || '') : '');
      [ev.seq, (ev.ts || '').slice(11, 19), ev.actor, ev.event, summary].forEach((value) => {
        const td = document.createElement('td');
        td.textContent = value == null ? '' : String(value);
        if (summary && value === summary) td.className = 'summary';
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    }
    $('trace-meta').textContent = rows.length + ' events · ' + state.traceId;
  }

  $('trace-verify').onclick = async () => {
    if (!state.taskId) return;
    const el = $('verify-badge');
    el.textContent = '校验中…';
    el.className = 'badge wait';
    try {
      const res = await fetch(`/api/tasks/${state.taskId}/trace/verify`);
      const body = await res.json();
      if (res.ok && body.ok) {
        el.textContent = 'OK · ' + body.events + ' events';
        el.className = 'badge ok';
      } else if (body.bad_seq != null) {
        el.textContent = 'TAMPERED at seq ' + body.bad_seq;
        el.className = 'badge bad';
      } else {
        el.textContent = 'FAIL · HTTP ' + res.status;
        el.className = 'badge bad';
      }
    } catch (err) {
      el.textContent = '校验失败';
      el.className = 'badge bad';
    }
  };

  window.addEventListener('beforeunload', () => {
    if (state.source) state.source.close();
  });
}());

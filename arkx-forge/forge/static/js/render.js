// Pure rendering helpers: DOM out, no fetches, no app state.
export const el = (id) => document.getElementById(id);

export function clockOf(ts) {
  return new Date(ts * 1000).toTimeString().slice(0, 8);
}
export function text(node, value) { node.textContent = value; }

export function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

export const STAGES = ['ANALYSIS', 'PLAN', 'IMPLEMENT', 'TEST', 'DEBUG', 'REVIEW', 'SUMMARY'];

export function renderRail(container, stageStates) {
  container.innerHTML = '';
  for (const name of STAGES) {
    const state = stageStates[name] || '';
    const li = document.createElement('li');
    li.className = state;
    li.innerHTML = `<span class="dot"></span>${name}`;
    container.appendChild(li);
  }
}

export function renderProjects(container, projects, activeId, onSelect, onRemove) {
  container.innerHTML = '';
  if (!projects.length) {
    container.innerHTML = '<li class="muted">No projects. Add a repository path.</li>';
    return;
  }
  for (const p of projects) {
    const li = document.createElement('li');
    li.className = 'item' + (p.id === activeId ? ' active' : '');
    const branch = p.git?.branch ? ` · ${p.git.branch}` : '';
    const flag = p.exists ? '' : ' · MISSING';
    li.innerHTML = `<span class="title">${esc(p.name)}${p.running_task ? ' ●' : ''}</span>
      <span class="sub">${esc(p.path)}${esc(branch)}${flag}</span>`;
    li.onclick = () => onSelect(p);
    li.oncontextmenu = (e) => { e.preventDefault(); onRemove(p); };
    container.appendChild(li);
  }
}

export function renderActiveProject(container, project) {
  if (!project) { container.className = 'kv muted'; container.textContent = 'No project selected.'; return; }
  const g = project.git || {};
  const rows = [
    ['NAME', project.name],
    ['PATH', project.path],
    ['BRANCH', g.branch || 'not a git repo'],
    ['STATUS', g.is_repo ? (g.dirty ? `${g.status.length} changed paths` : 'clean') : '—'],
    ['COMMITS', g.commits?.length ? g.commits[0].short + ' ' + g.commits[0].subject : '—'],
  ];
  container.className = 'kv';
  container.innerHTML = rows.map(([k, v]) =>
    `<div class="r"><span class="k">${k}</span><span title="${esc(v)}">${esc(v)}</span></div>`).join('');
}

export function renderCommits(container, commits) {
  if (!commits?.length) { container.innerHTML = '<li class="muted">no commits</li>'; return; }
  container.innerHTML = commits.map((c) =>
    `<li><span class="title">${esc(c.short)} ${esc(c.subject)}</span>
     <span class="sub">${esc(c.author)} · ${esc(c.when)}</span></li>`).join('');
}

export function renderTasks(container, tasks, activeId, onSelect) {
  if (!tasks.length) { container.innerHTML = '<li class="muted">no tasks yet</li>'; return; }
  container.innerHTML = '';
  for (const t of tasks) {
    const li = document.createElement('li');
    li.className = 'item' + (t.id === activeId ? ' active' : '');
    const when = new Date(t.created_at * 1000).toLocaleString();
    li.innerHTML = `<span class="title">#${t.id} ${esc(t.request.slice(0, 54))}</span>
      <span class="sub"><span class="st-${t.status}">${t.status.toUpperCase()}</span>
      · ${esc(t.stage || '—')} · ${esc(when)}</span>`;
    li.onclick = () => onSelect(t.id);
    container.appendChild(li);
  }
}

export function renderAgents(container, agents) {
  container.innerHTML = agents.map((a) =>
    `<li><span class="nm">${esc(a.title)}</span>
      <span class="sub">${esc(a.purpose)}</span>
      <span class="sub">${a.tools.length} tools</span></li>`).join('');
}

export function renderFiles(container, files, countEl) {
  const entries = Object.values(files);
  countEl.textContent = entries.length ? `${entries.length}` : '';
  if (!entries.length) { container.innerHTML = '<li class="muted">none yet</li>'; return; }
  container.innerHTML = entries.map((f) =>
    `<li><span class="badge b-${f.action}">${f.action.slice(0, 3).toUpperCase()}</span>
      <span class="title" title="${esc(f.path)}">${esc(f.path)}</span>
      <span class="delta">+${f.added}/-${f.removed}</span></li>`).join('');
}

export function renderDiff(pre, diff) {
  if (!diff || diff === '<no changes>') {
    pre.className = 'diff muted'; pre.textContent = 'No uncommitted changes.'; return;
  }
  pre.className = 'diff';
  pre.innerHTML = diff.split('\n').map((line) => {
    const c = line.startsWith('+++') || line.startsWith('---') ? 'f'
      : line.startsWith('+') ? 'a' : line.startsWith('-') ? 'd'
      : line.startsWith('@@') ? 'h' : line.startsWith('diff ') ? 'f' : '';
    return c ? `<span class="${c}">${esc(line)}</span>` : esc(line);
  }).join('\n');
}

export function renderPlan(container, steps, raw) {
  if (!steps?.length) {
    container.className = 'plan muted';
    container.innerHTML = raw ? `<li>${esc(raw.slice(0, 600))}</li>` : 'No plan yet.';
    return;
  }
  container.className = 'plan';
  container.innerHTML = steps.map((s) => `<li>${esc(s)}</li>`).join('');
}

export function renderTests(container, status, lastCommand) {
  const colour = { passed: 'st-completed', failed: 'st-failed', none: 'st-cancelled' }[status] || '';
  container.innerHTML = `
    <div class="r"><span class="k">RESULT</span><span class="${colour}">${(status || 'unknown').toUpperCase()}</span></div>
    ${lastCommand ? `<div class="r"><span class="k">LAST</span><span title="${esc(lastCommand.command)}">${esc(lastCommand.command)}</span></div>
    <div class="r"><span class="k">EXIT</span><span class="${lastCommand.exit_code === 0 ? 'st-completed' : 'st-failed'}">${lastCommand.exit_code} · ${lastCommand.duration_ms}ms</span></div>` : ''}`;
}

export function renderAgentStatus(container, s) {
  container.innerHTML = [
    ['TASK', s.taskId ? `#${s.taskId}` : '—'],
    ['STAGE', s.stage || '—'],
    ['ROLE', s.role || '—'],
    ['STATE', s.state || 'idle'],
    ['TOKENS', `${s.inTokens || 0} in / ${s.outTokens || 0} out`],
    ['COMMANDS', String(s.commands || 0)],
  ].map(([k, v]) => `<div class="r"><span class="k">${k}</span><span>${esc(v)}</span></div>`).join('');
}

export function appendEvent(stream, ev, showReasoning) {
  if (ev.kind === 'thinking' && !showReasoning) return;
  if (ev.kind === 'assistant') return;              // streamed prose goes to STAGE OUTPUT
  const atBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 60;
  const line = document.createElement('div');
  line.className = `ev ev-${ev.kind}`;
  line.innerHTML = `<span class="t">[${clockOf(ev.ts)}]</span><span class="m">${esc(ev.message)}</span>`;
  stream.appendChild(line);

  const d = ev.data || {};
  if (ev.kind === 'command' && d.running === false && (d.stdout || d.stderr || d.timed_out)) {
    const out = document.createElement('div');
    out.className = 'cmdout';
    const cls = d.exit_code === 0 ? 'exit-ok' : 'exit-bad';
    out.innerHTML =
      `<span class="${cls}">exit ${esc(d.exit_code)}</span> · ${esc(d.duration_ms)}ms`
      + (d.timed_out ? ' · <span class="exit-bad">TIMED OUT</span>' : '')
      + (d.stdout ? `\n${esc(d.stdout.slice(-2500))}` : '')
      + (d.stderr ? `\n<span class="exit-bad">${esc(d.stderr.slice(-2500))}</span>` : '');
    stream.appendChild(out);
  }
  if (atBottom) stream.scrollTop = stream.scrollHeight;
}

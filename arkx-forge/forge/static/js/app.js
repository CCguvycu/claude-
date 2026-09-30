// ARKX Forge control surface: wiring between the API, the SSE stream and the DOM.
import { api } from './api.js';
import { connect } from './stream.js';
import * as R from './render.js';

const el = R.el;

const S = {
  config: null,
  projects: [],
  project: null,
  tasks: [],
  taskId: null,
  live: false,
  stageStates: {},
  files: {},
  commands: [],
  testStatus: null,
  agent: { state: 'idle' },
  showReasoning: false,
  streamBuf: '',
  seen: new Set(),   // event seq numbers already rendered
};

// ---------------------------------------------------------------- top bar
function paintBar() {
  R.text(el('bar-project'), S.project ? S.project.name : '—');
  R.text(el('bar-branch'), S.project?.git?.branch || '—');
  R.text(el('bar-model'), S.config?.model || '—');
  const a = el('bar-agent');
  const map = { idle: 'agent-idle', running: 'agent-run', awaiting: 'agent-wait',
                failed: 'agent-err', completed: 'agent-ok', cancelled: 'agent-idle' };
  a.className = map[S.agent.state] || 'agent-idle';
  a.textContent = (S.agent.stage && S.agent.state === 'running'
    ? `${S.agent.stage}` : S.agent.state).toUpperCase();

  const warn = el('bar-warning');
  if (S.config && !S.config.credentials_present) {
    warn.textContent = 'NO ANTHROPIC_API_KEY — SET IT AND RESTART';
    warn.classList.remove('hidden');
  } else if (S.config?.provider === 'mock') {
    warn.textContent = 'MOCK PROVIDER — NOT CALLING CLAUDE';
    warn.classList.remove('hidden');
  } else warn.classList.add('hidden');
}

function paintAgentStatus() {
  R.renderAgentStatus(el('agent-status'), {
    taskId: S.taskId, stage: S.agent.stage, role: S.agent.role,
    state: S.agent.state, inTokens: S.agent.inTokens, outTokens: S.agent.outTokens,
    commands: S.commands.length,
  });
  paintBar();
}

// ---------------------------------------------------------------- projects
async function loadProjects(selectId) {
  S.projects = await api.projects();
  R.renderProjects(el('project-list'), S.projects, S.project?.id, selectProject, removeProject);
  const want = selectId ?? S.project?.id;
  const found = S.projects.find((p) => p.id === want) || S.projects[0];
  if (found && found.id !== S.project?.id) await selectProject(found);
  else if (found) { S.project = found; paintBar(); }
}

async function selectProject(p) {
  S.project = p;
  R.renderProjects(el('project-list'), S.projects, p.id, selectProject, removeProject);
  R.renderActiveProject(el('active-project'), p);
  paintBar();
  await Promise.all([loadTasks(), refreshGit()]);
  if (p.running_task) await openTask(p.running_task);
}

async function removeProject(p) {
  if (!confirm(`Remove ${p.name} from Forge? (the repository itself is untouched)`)) return;
  try { await api.removeProject(p.id); } catch (e) { alert(e.message); return; }
  if (S.project?.id === p.id) S.project = null;
  await loadProjects();
}

async function refreshGit() {
  if (!S.project) return;
  try {
    const git = await api.git(S.project.id);
    S.project.git = git;
    R.renderActiveProject(el('active-project'), S.project);
    const { diff } = await api.diff(S.project.id);
    R.renderDiff(el('diff'), diff);
    paintBar();
  } catch (e) {
    el('diff').textContent = `git error: ${e.message}`;
  }
}

// ------------------------------------------------------------------- tasks
async function loadTasks() {
  if (!S.project) return;
  S.tasks = await api.tasks(S.project.id);
  R.renderTasks(el('task-list'), S.tasks, S.taskId, openTask);
}

function resetTaskView() {
  S.stageStates = {}; S.files = {}; S.commands = []; S.testStatus = null;
  S.agent = { state: 'idle' }; S.streamBuf = ''; S.seen = new Set();
  el('activity').innerHTML = '';
  R.renderRail(el('stage-rail'), S.stageStates);
  R.renderFiles(el('files'), S.files, el('files-count'));
  R.renderPlan(el('plan'), [], '');
  el('stage-output').className = 'prose muted';
  el('stage-output').textContent = 'Stage output appears here as each agent finishes.';
  el('tests').innerHTML = '<span class="muted">no test run yet</span>';
}

async function openTask(id) {
  S.taskId = id;
  resetTaskView();
  const t = await api.task(id);
  S.live = !!t.live;
  el('task-input').value = t.request;
  el('task-meta').textContent =
    `#${t.id} · ${t.status} · ${t.model} · ${new Date(t.created_at * 1000).toLocaleString()}`;

  for (const st of t.stages) {
    S.stageStates[baseStage(st.name)] = st.status === 'done' ? 'done' : st.status;
  }
  for (const f of t.files) {
    S.files[f.path] = { path: f.path, action: f.action, added: f.added, removed: f.removed };
  }
  S.commands = t.commands;
  S.testStatus = t.test_status;
  for (const ev of t.events) {
    if (ev.seq) S.seen.add(ev.seq);
    R.appendEvent(el('activity'), { ...ev, data: ev.data || {} }, S.showReasoning);
  }
  R.renderRail(el('stage-rail'), S.stageStates);
  R.renderFiles(el('files'), S.files, el('files-count'));
  R.renderPlan(el('plan'), parseSteps(t.plan || ''), t.plan || '');
  R.renderTests(el('tests'), t.test_status, S.commands[S.commands.length - 1]);
  showOutput(t.summary || t.review || t.plan || t.analysis || '');
  const last = t.stages[t.stages.length - 1];
  S.agent = {
    state: t.status === 'running' ? 'running' : t.status,
    stage: t.stage,
    role: last?.role,
    inTokens: t.stages.reduce((n, s2) => n + (s2.in_tokens || 0), 0),
    outTokens: t.stages.reduce((n, s2) => n + (s2.out_tokens || 0), 0),
  };
  paintAgentStatus();
  R.renderTasks(el('task-list'), S.tasks, id, openTask);
  el('btn-cancel').classList.toggle('hidden', !t.live);
  el('btn-run').disabled = !!t.live;
  if (t.pending_confirm) showConfirm(t.pending_confirm);
  el('activity').scrollTop = el('activity').scrollHeight;
}

const baseStage = (name) => (name || '').split('#')[0];
const parseSteps = (plan) => plan.split('\n')
  .map((l) => l.match(/^\s*\d+[.)]\s+(.*)$/)).filter(Boolean).map((m) => m[1].trim());

function showOutput(txt) {
  const node = el('stage-output');
  node.className = txt ? 'prose' : 'prose muted';
  node.textContent = txt || 'Stage output appears here as each agent finishes.';
  node.scrollTop = node.scrollHeight;
}

async function runTask() {
  const request = el('task-input').value.trim();
  el('task-error').textContent = '';
  if (!S.project) { el('task-error').textContent = 'Select a project first.'; return; }
  if (request.length < 3) { el('task-error').textContent = 'Describe the task.'; return; }
  el('btn-run').disabled = true;
  try {
    const { task_id } = await api.startTask(S.project.id, request);
    S.taskId = task_id;
    S.live = true;
    resetTaskView();
    el('btn-cancel').classList.remove('hidden');
    el('task-meta').textContent = `#${task_id} · running`;
    await loadTasks();
  } catch (e) {
    el('task-error').textContent = e.message;
    el('btn-run').disabled = false;
  }
}

async function cancelTask() {
  if (!S.taskId) return;
  try { await api.cancelTask(S.taskId); } catch (e) { el('task-error').textContent = e.message; }
}

// ------------------------------------------------------------ confirmation
let pendingTask = null;
function showConfirm(p) {
  pendingTask = p.task_id;
  el('confirm-reason').textContent = `${p.reason} — approval needed before this runs.`;
  el('confirm-command').textContent = p.command;
  el('confirm-overlay').classList.remove('hidden');
  S.agent.state = 'awaiting';
  paintAgentStatus();
}
function hideConfirm() {
  el('confirm-overlay').classList.add('hidden');
  pendingTask = null;
}
async function resolveConfirm(approve) {
  if (pendingTask == null) return hideConfirm();
  const id = pendingTask;
  hideConfirm();
  try { await api.confirm(id, approve); } catch (e) { console.warn(e.message); }
}

// ------------------------------------------------------------- live events
function onEvent(ev) {
  if (S.taskId == null) return;
  if (ev.task_id !== S.taskId) {
    if (ev.kind === 'done') loadTasks();
    return;
  }
  if (ev.seq) {
    if (S.seen.has(ev.seq)) return;      // already rendered from the stored history
    S.seen.add(ev.seq);
  }
  const d = ev.data || {};

  if (ev.kind === 'assistant' && d.delta) {
    S.streamBuf += ev.message;
    showOutput(S.streamBuf);
    return;
  }
  R.appendEvent(el('activity'), ev, S.showReasoning);

  switch (ev.kind) {
    case 'stage':
      if (d.state === 'start') {
        S.streamBuf = '';
        S.stageStates[baseStage(ev.stage)] = 'running';
        S.agent = { ...S.agent, state: 'running', stage: ev.stage, role: d.role };
      } else if (d.state === 'done') {
        S.stageStates[baseStage(ev.stage)] = 'done';
        S.agent.inTokens = (S.agent.inTokens || 0) + (d.in_tokens || 0);
        S.agent.outTokens = (S.agent.outTokens || 0) + (d.out_tokens || 0);
      } else if (d.state === 'plan') {
        R.renderPlan(el('plan'), d.steps || [], d.plan || '');
      } else if (d.state === 'cancelled') {
        S.stageStates[baseStage(ev.stage)] = 'cancelled';
      }
      R.renderRail(el('stage-rail'), S.stageStates);
      break;
    case 'file':
      S.files[d.path] = { path: d.path, action: d.action, added: d.added || 0, removed: d.removed || 0 };
      R.renderFiles(el('files'), S.files, el('files-count'));
      break;
    case 'command':
      if (d.running === false) {
        S.commands.push({ command: d.command, exit_code: d.exit_code, duration_ms: d.duration_ms });
        R.renderTests(el('tests'), S.testStatus, S.commands[S.commands.length - 1]);
      }
      break;
    case 'test':
      S.testStatus = d.status;
      R.renderTests(el('tests'), d.status, S.commands[S.commands.length - 1]);
      break;
    case 'confirm':
      if (d.resolved === false) showConfirm({ task_id: ev.task_id, command: d.command, reason: d.reason });
      else { hideConfirm(); S.agent.state = 'running'; }
      break;
    case 'error':
      if (S.stageStates[baseStage(ev.stage)] === 'running') {
        S.stageStates[baseStage(ev.stage)] = 'failed';
        R.renderRail(el('stage-rail'), S.stageStates);
      }
      break;
    case 'done':
      S.live = false;
      S.agent.state = d.status || 'completed';
      el('btn-cancel').classList.add('hidden');
      el('btn-run').disabled = false;
      el('task-meta').textContent = `#${S.taskId} · ${d.status}`;
      loadTasks();
      refreshGit();
      api.task(S.taskId).then((t) => {
        showOutput(t.summary || t.review || S.streamBuf);
        R.renderPlan(el('plan'), parseSteps(t.plan || ''), t.plan || '');
      }).catch(() => {});
      break;
  }
  paintAgentStatus();
}

// ------------------------------------------------------------------- setup
async function commit() {
  const msg = el('commit-message').value.trim();
  const status = el('commit-status');
  if (!S.project) { status.textContent = 'No project selected.'; return; }
  if (!msg) { status.textContent = 'Commit message required.'; return; }
  el('btn-commit').disabled = true;
  try {
    const res = await api.commit(S.project.id, msg);
    status.textContent = `committed ${res.sha} — Forge does not push.`;
    el('commit-message').value = '';
    await refreshGit();
  } catch (e) {
    status.textContent = `commit failed: ${e.message}`;
  } finally {
    el('btn-commit').disabled = false;
  }
}

function wire() {
  el('btn-add-project').onclick = () => {
    el('add-project-form').classList.toggle('hidden');
    el('project-path').focus();
  };
  el('btn-cancel-project').onclick = () => el('add-project-form').classList.add('hidden');
  el('btn-save-project').onclick = async () => {
    const path = el('project-path').value.trim();
    el('project-error').textContent = '';
    if (!path) return;
    try {
      const p = await api.addProject(path);
      el('project-path').value = '';
      el('add-project-form').classList.add('hidden');
      await loadProjects(p.id);
    } catch (e) { el('project-error').textContent = e.message; }
  };
  el('project-path').onkeydown = (e) => { if (e.key === 'Enter') el('btn-save-project').click(); };
  el('btn-run').onclick = runTask;
  el('btn-cancel').onclick = cancelTask;
  el('btn-refresh-diff').onclick = refreshGit;
  el('btn-commit').onclick = commit;
  el('btn-approve').onclick = () => resolveConfirm(true);
  el('btn-deny').onclick = () => resolveConfirm(false);
  el('show-reasoning').onchange = (e) => { S.showReasoning = e.target.checked; };
  el('task-input').onkeydown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') runTask();
  };
  document.onkeydown = (e) => {
    if (e.key === 'Escape' && !el('confirm-overlay').classList.contains('hidden')) resolveConfirm(false);
  };
}

async function boot() {
  wire();
  R.renderRail(el('stage-rail'), {});
  S.config = await api.config();
  R.renderAgents(el('agent-list'), S.config.agents);
  paintBar();
  await loadProjects();
  const pend = await api.pending().catch(() => []);
  if (pend.length) showConfirm(pend[0]);
  connect({
    onEvent,
    onOpen: () => { const d = el('bar-link'); d.textContent = 'STREAM ●'; d.classList.add('live'); },
    onClose: () => { const d = el('bar-link'); d.textContent = 'STREAM ○'; d.classList.remove('live'); },
  });
}

boot().catch((e) => {
  document.body.insertAdjacentHTML('afterbegin',
    `<pre style="color:#f87171;padding:14px">Forge failed to start: ${e.message}</pre>`);
});

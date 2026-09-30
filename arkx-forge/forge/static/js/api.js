// Thin JSON client. The browser never sees a model credential: every Claude call
// happens in the Forge backend.
async function req(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) throw new Error(data?.detail || `${res.status} ${res.statusText}`);
  return data;
}

export const api = {
  config: () => req('GET', '/api/config'),
  agents: () => req('GET', '/api/agents'),
  projects: () => req('GET', '/api/projects'),
  addProject: (path) => req('POST', '/api/projects', { path }),
  removeProject: (id) => req('DELETE', `/api/projects/${id}`),
  git: (id) => req('GET', `/api/projects/${id}/git`),
  diff: (id, path) => req('GET', `/api/projects/${id}/diff${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  commit: (id, message, paths) => req('POST', `/api/projects/${id}/commit`, { message, paths }),
  tasks: (projectId) => req('GET', `/api/tasks${projectId ? `?project_id=${projectId}` : ''}`),
  task: (id) => req('GET', `/api/tasks/${id}`),
  startTask: (project_id, request) => req('POST', '/api/tasks', { project_id, request }),
  cancelTask: (id) => req('POST', `/api/tasks/${id}/cancel`),
  confirm: (id, approve) => req('POST', `/api/tasks/${id}/confirm`, { approve }),
  pending: () => req('GET', '/api/pending'),
};

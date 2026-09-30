// Server-sent events with automatic reconnect.
export function connect({ onEvent, onOpen, onClose }) {
  let es = null, closed = false, retry = 800;

  const open = () => {
    es = new EventSource('/api/stream');
    es.onopen = () => { retry = 800; onOpen?.(); };
    es.onmessage = (m) => {
      if (!m.data) return;
      try { onEvent(JSON.parse(m.data)); } catch { /* ignore malformed frame */ }
    };
    es.onerror = () => {
      es.close();
      onClose?.();
      if (!closed) {
        setTimeout(open, retry);
        retry = Math.min(retry * 2, 10000);
      }
    };
  };
  open();
  return { stop() { closed = true; es?.close(); } };
}

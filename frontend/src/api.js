const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';

async function get(path, signal) {
  const res = await fetch(`${BASE}${path}`, { signal });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

export const fetchUserIds   = (signal)              => get('/api/users', signal).then(d => d.user_ids);
export const fetchTimeline  = (userId, signal)      => get(`/api/users/${userId}/timeline`, signal).then(d => d.timeline);
export const fetchFrame     = (userId, eventId, signal) => get(`/api/users/${userId}/events/${eventId}`, signal);

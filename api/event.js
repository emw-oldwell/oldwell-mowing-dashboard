// POST /api/event — upsert a per-job overlay into events.json in the repo.
// Body: { jobId: string, event: {...}, customJob?: {...} }
// The event becomes the latest overlay for that JobID. Last-write-wins per job,
// gated by event.lastUpdate timestamp so out-of-order POSTs don't clobber newer state.

const { getFile, putFile, setCors, assertConfigured } = require('./_github');

const MAX_RETRIES = 5;
const EVENTS_PATH = 'events.json';

module.exports = async function handler(req, res) {
  setCors(res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
  if (!assertConfigured(res)) return;

  const { jobId, event, customJob } = req.body || {};
  if (!jobId || typeof jobId !== 'string' || jobId.length > 64) {
    return res.status(400).json({ error: 'jobId required (string, ≤64 chars)' });
  }
  if (!event || typeof event !== 'object') {
    return res.status(400).json({ error: 'event object required' });
  }
  if (JSON.stringify(event).length > 8192) {
    return res.status(413).json({ error: 'event too large (>8KB)' });
  }

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const result = await applyEvent(jobId, event, customJob);
      return res.status(200).json(result);
    } catch (e) {
      // GitHub returns 409 on SHA mismatch (concurrent write). Retry with fresh SHA.
      if (e.status === 409 && attempt < MAX_RETRIES - 1) continue;
      console.error('event apply failed', e);
      return res.status(500).json({ error: String(e.message || e) });
    }
  }
};

async function applyEvent(jobId, incoming, customJob) {
  const file = await getFile(EVENTS_PATH);
  let state = { updatedAt: null, events: {}, customJobs: [] };
  if (file.content) {
    try { state = JSON.parse(file.content); } catch { /* corrupt → overwrite */ }
    if (!state.events) state.events = {};
    if (!state.customJobs) state.customJobs = [];
  }

  const now = new Date().toISOString();
  const incomingTime = incoming.lastUpdate || now;
  const existing = state.events[jobId];
  const isNewer = !existing || (existing.lastUpdate || '') <= incomingTime;
  if (isNewer) {
    state.events[jobId] = { ...incoming, lastUpdate: incomingTime };
  } else {
    return { ok: true, skipped: 'older than current overlay', currentLastUpdate: existing.lastUpdate };
  }

  if (customJob && customJob.JobID && !state.customJobs.find(j => j.JobID === customJob.JobID)) {
    state.customJobs.push(customJob);
  }

  state.updatedAt = now;
  const buf = Buffer.from(JSON.stringify(state, null, 0));
  const msg = `app: ${jobId} ${incoming.status || incoming.cancelled ? 'cancel' : 'update'} via ${(incoming.updatedBy || 'app').slice(0, 40)}`;
  await putFile(EVENTS_PATH, buf, msg, file.sha);
  return { ok: true, jobId, lastUpdate: incomingTime };
}

// POST /api/property — add a new property + its generated season of jobs in one shot.
// Body: { property: {id, nickname, typeId, contractor, seasonStart, addedBy}, jobs: [Job...] }
//
// Writes the property metadata to events.json.properties (new top-level array)
// and every generated job to events.json.customJobs. One PUT, one commit,
// atomic across both. Each client picks both up on its next 30-s poll.

const { getFile, putFile, setCors, assertConfigured } = require('./_github');

const MAX_RETRIES = 5;
const EVENTS_PATH = 'events.json';
const MAX_JOBS_PER_PROPERTY = 60;

module.exports = async function handler(req, res) {
  setCors(res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
  if (!assertConfigured(res)) return;

  const { property, jobs } = req.body || {};
  if (!property || typeof property !== 'object') {
    return res.status(400).json({ error: 'property object required' });
  }
  if (!Array.isArray(jobs)) {
    return res.status(400).json({ error: 'jobs array required' });
  }
  if (jobs.length === 0) {
    return res.status(400).json({ error: 'at least one job required' });
  }
  if (jobs.length > MAX_JOBS_PER_PROPERTY) {
    return res.status(413).json({ error: `too many jobs (${jobs.length} > ${MAX_JOBS_PER_PROPERTY})` });
  }
  const propId = String(property.id || '').trim().toUpperCase();
  const nickname = String(property.nickname || '').trim();
  const typeId = String(property.typeId || '').trim().toUpperCase();
  if (!propId || !/^P[A-Z0-9-]{1,30}$/i.test(propId)) {
    return res.status(400).json({ error: 'property.id required (e.g. P-013)' });
  }
  if (!nickname || nickname.length > 60) {
    return res.status(400).json({ error: 'property.nickname required (≤60 chars)' });
  }
  if (!['LUX', 'RNT', 'VAC1', 'VAC2'].includes(typeId)) {
    return res.status(400).json({ error: 'property.typeId must be LUX/RNT/VAC1/VAC2' });
  }
  for (const j of jobs) {
    if (!j || typeof j !== 'object') return res.status(400).json({ error: 'each job must be an object' });
    if (!j.JobID || typeof j.JobID !== 'string' || j.JobID.length > 64) {
      return res.status(400).json({ error: `bad JobID on a job` });
    }
    if (!j.ScheduledDate) return res.status(400).json({ error: `job ${j.JobID} missing ScheduledDate` });
  }

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const result = await applyProperty(property, jobs, { propId, nickname, typeId });
      return res.status(200).json(result);
    } catch (e) {
      if (e.status === 409 && attempt < MAX_RETRIES - 1) continue;
      console.error('property apply failed', e);
      return res.status(500).json({ error: String(e.message || e) });
    }
  }
};

async function applyProperty(propertyRaw, jobs, parsed) {
  const file = await getFile(EVENTS_PATH);
  let state = { updatedAt: null, events: {}, customJobs: [], tombstones: {}, crews: [], properties: [] };
  if (file.content) {
    try { state = JSON.parse(file.content); } catch { /* corrupt → reset */ }
    if (!state.events) state.events = {};
    if (!state.customJobs) state.customJobs = [];
    if (!state.tombstones) state.tombstones = {};
    if (!state.crews) state.crews = [];
    if (!state.properties) state.properties = [];
  }

  const now = new Date().toISOString();
  const cleanProperty = {
    id: parsed.propId,
    nickname: parsed.nickname,
    typeId: parsed.typeId,
    contractor: String(propertyRaw.contractor || '').trim() || undefined,
    seasonStart: String(propertyRaw.seasonStart || '').slice(0, 10) || undefined,
    addedBy: String(propertyRaw.addedBy || '').trim() || undefined,
    addedAt: now,
  };
  for (const k of Object.keys(cleanProperty)) if (cleanProperty[k] === undefined) delete cleanProperty[k];

  // Upsert property record
  const existingIdx = state.properties.findIndex(p => p.id === cleanProperty.id);
  if (existingIdx >= 0) {
    const prev = state.properties[existingIdx];
    state.properties[existingIdx] = { ...prev, ...cleanProperty, addedAt: prev.addedAt || now };
  } else {
    state.properties.push(cleanProperty);
  }

  // Add jobs that don't already exist (idempotent on retry); never overwrite.
  let added = 0;
  const existingJobIds = new Set(state.customJobs.map(j => j.JobID));
  for (const j of jobs) {
    if (existingJobIds.has(j.JobID) || state.tombstones[j.JobID]) continue;
    // Strip private app-only fields prefixed with _
    const clean = {};
    for (const [k, v] of Object.entries(j)) if (!k.startsWith('_')) clean[k] = v;
    state.customJobs.push(clean);
    existingJobIds.add(j.JobID);
    added++;
  }

  state.updatedAt = now;
  const buf = Buffer.from(JSON.stringify(state, null, 0));
  await putFile(EVENTS_PATH, buf, `app: add property ${parsed.propId} (${parsed.nickname}) +${added} jobs`, file.sha);
  return { ok: true, property: cleanProperty, jobsAdded: added, jobsSubmitted: jobs.length };
}

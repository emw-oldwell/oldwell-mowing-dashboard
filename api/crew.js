// POST /api/crew — add or update a crew (contractor) record.
// Body: { crew: { name: string, email?: string, phone?: string, notes?: string, addedBy?: string } }
//
// Crews live in events.json under a top-level `crews` array. The Schedule list
// in SharePoint still only carries ContractorName strings — this lets the office
// onboard a crew without touching SharePoint and have them appear immediately
// in sign-in, filter pills, and reassign dropdowns across all devices.

const { getFile, putFile, setCors, assertConfigured } = require('./_github');

const MAX_RETRIES = 5;
const EVENTS_PATH = 'events.json';

module.exports = async function handler(req, res) {
  setCors(res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
  if (!assertConfigured(res)) return;

  const { crew } = req.body || {};
  if (!crew || typeof crew !== 'object') {
    return res.status(400).json({ error: 'crew object required' });
  }
  const name = String(crew.name || '').trim();
  if (!name || name.length > 100) {
    return res.status(400).json({ error: 'crew.name required (≤100 chars)' });
  }

  const clean = { name };
  for (const k of ['email', 'phone', 'notes', 'addedBy']) {
    const v = String(crew[k] || '').trim();
    if (v && v.length <= 300) clean[k] = v;
  }

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const result = await upsertCrew(clean);
      return res.status(200).json(result);
    } catch (e) {
      if (e.status === 409 && attempt < MAX_RETRIES - 1) continue;
      console.error('crew upsert failed', e);
      return res.status(500).json({ error: String(e.message || e) });
    }
  }
};

async function upsertCrew(incoming) {
  const file = await getFile(EVENTS_PATH);
  let state = { updatedAt: null, events: {}, customJobs: [], tombstones: {}, crews: [] };
  if (file.content) {
    try { state = JSON.parse(file.content); } catch { /* corrupt → reset */ }
    if (!state.events) state.events = {};
    if (!state.customJobs) state.customJobs = [];
    if (!state.tombstones) state.tombstones = {};
    if (!state.crews) state.crews = [];
  }

  const now = new Date().toISOString();
  const existingIdx = state.crews.findIndex(c => c.name === incoming.name);
  let crewOut;
  if (existingIdx >= 0) {
    // Merge — preserve original addedAt, only update other fields
    const prev = state.crews[existingIdx];
    crewOut = { ...prev, ...incoming, addedAt: prev.addedAt || now, updatedAt: now };
    state.crews[existingIdx] = crewOut;
  } else {
    crewOut = { ...incoming, addedAt: now };
    state.crews.push(crewOut);
  }

  state.updatedAt = now;
  const buf = Buffer.from(JSON.stringify(state, null, 0));
  await putFile(EVENTS_PATH, buf, `app: ${existingIdx >= 0 ? 'update' : 'add'} crew ${incoming.name}`, file.sha);
  return { ok: true, crew: crewOut };
}

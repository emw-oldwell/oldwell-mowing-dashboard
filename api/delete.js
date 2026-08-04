// POST /api/delete — remove a job overlay entirely, file a tombstone so
// every client device prunes it from localStorage on next poll, and
// best-effort delete any photo files referenced by that overlay.
// Body: { jobId: string, deletePhotos?: boolean (default true) }

const { getFile, putFile, deleteFile, assertConfigured } = require('./_github');
const { setCors, requireAuth } = require('./_auth');

const MAX_RETRIES = 5;
const EVENTS_PATH = 'events.json';
const TOMBSTONE_TTL_DAYS = 90;

module.exports = async function handler(req, res) {
  setCors(req, res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
  if (!requireAuth(req, res)) return;
  if (!assertConfigured(res)) return;

  const { jobId, deletePhotos = true } = req.body || {};
  if (!jobId || typeof jobId !== 'string' || jobId.length > 64 || !/^[A-Za-z0-9_-]+$/.test(jobId)) {
    return res.status(400).json({ error: 'jobId required (≤64 chars, alphanumeric/_/-)' });
  }

  let tombstoneResult;
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      tombstoneResult = await applyTombstone(jobId);
      break;
    } catch (e) {
      if (e.status === 409 && attempt < MAX_RETRIES - 1) continue;
      console.error('tombstone failed', e);
      return res.status(500).json({ error: String(e.message || e) });
    }
  }

  // Photo cleanup is best-effort and independent of the tombstone commit.
  // Worst case a JPG lingers in the repo (no longer referenced anywhere).
  const photosDeleted = [];
  const photosFailed = [];
  if (deletePhotos && tombstoneResult.photoPaths.length) {
    for (const path of tombstoneResult.photoPaths) {
      try {
        const r = await deleteFile(path, `app: delete photo for ${jobId}`);
        photosDeleted.push({ path, ...r });
      } catch (e) {
        photosFailed.push({ path, error: String(e.message || e) });
      }
    }
  }

  return res.status(200).json({
    ok: true,
    jobId,
    wasPresent: tombstoneResult.wasPresent,
    tombstoneAt: tombstoneResult.tombstoneAt,
    tombstonesPruned: tombstoneResult.tombstonesPruned,
    photosDeleted,
    photosFailed,
  });
};

async function applyTombstone(jobId) {
  const file = await getFile(EVENTS_PATH);
  let state = { updatedAt: null, events: {}, customJobs: [], tombstones: {} };
  if (file.content) {
    try { state = JSON.parse(file.content); } catch { /* corrupt → reset */ }
    if (!state.events) state.events = {};
    if (!state.customJobs) state.customJobs = [];
    if (!state.tombstones) state.tombstones = {};
  }

  const wasPresent = Boolean(state.events[jobId]);
  const photoPaths = (state.events[jobId]?.photos || [])
    .map(p => extractRepoPath(p.url))
    .filter(Boolean);

  delete state.events[jobId];
  state.customJobs = state.customJobs.filter(j => j.JobID !== jobId);
  const now = new Date().toISOString();
  state.tombstones[jobId] = now;

  // Opportunistic TTL prune: drop tombstones older than 90 days.
  // Runs on every delete call so events.json stays small without a cron.
  // Safe because by then every client device has long since pulled the
  // tombstone and pruned its own localStorage.
  const cutoff = new Date(Date.now() - TOMBSTONE_TTL_DAYS * 86400000).toISOString();
  const tombstonesPruned = [];
  for (const [id, at] of Object.entries(state.tombstones)) {
    if (at < cutoff) {
      delete state.tombstones[id];
      tombstonesPruned.push(id);
    }
  }

  state.updatedAt = now;

  const buf = Buffer.from(JSON.stringify(state, null, 0));
  const pruneNote = tombstonesPruned.length ? ` (+pruned ${tombstonesPruned.length})` : '';
  await putFile(EVENTS_PATH, buf, `app: delete ${jobId}${pruneNote}`, file.sha);
  return { wasPresent, tombstoneAt: now, photoPaths, tombstonesPruned };
}

function extractRepoPath(url) {
  // https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}
  const m = String(url || '').match(/raw\.githubusercontent\.com\/[^/]+\/[^/]+\/[^/]+\/(.+)$/);
  return m ? m[1] : null;
}

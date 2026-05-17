// POST /api/photo — upload a crew photo to /photos/ in the repo.
// Body: { jobId: string, filename: string, dataUrl: "data:image/jpeg;base64,..." }
// Returns: { url: "https://raw.githubusercontent.com/.../photos/J-NNNN_TIMESTAMP_filename" }

const { putFile, setCors, assertConfigured, REPO, BRANCH } = require('./_github');

const MAX_BYTES = 4 * 1024 * 1024; // 4 MB cap per photo

module.exports = async function handler(req, res) {
  setCors(res);
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
  if (!assertConfigured(res)) return;

  const { jobId, filename, dataUrl } = req.body || {};
  if (!jobId || typeof jobId !== 'string' || jobId.length > 64 || !/^[A-Za-z0-9_-]+$/.test(jobId)) {
    return res.status(400).json({ error: 'jobId required (≤64 chars, alphanumeric/_/-)' });
  }
  if (!dataUrl || typeof dataUrl !== 'string' || !dataUrl.startsWith('data:image/')) {
    return res.status(400).json({ error: 'dataUrl required (data:image/...)' });
  }

  const m = dataUrl.match(/^data:image\/(jpeg|jpg|png|webp);base64,(.+)$/i);
  if (!m) return res.status(400).json({ error: 'unsupported image type (jpeg/png/webp only)' });
  const ext = m[1].toLowerCase() === 'jpg' ? 'jpeg' : m[1].toLowerCase();
  const b64 = m[2];
  const buf = Buffer.from(b64, 'base64');
  if (buf.length > MAX_BYTES) return res.status(413).json({ error: `photo too large (${buf.length}B > ${MAX_BYTES}B)` });
  if (buf.length < 200) return res.status(400).json({ error: 'photo too small / decode failed' });

  const safeName = String(filename || '').replace(/[^A-Za-z0-9._-]/g, '_').slice(-40) || `photo.${ext}`;
  const ts = Date.now();
  const path = `photos/${jobId}_${ts}_${safeName}`;
  const msg = `app: photo for ${jobId} (${(buf.length / 1024).toFixed(0)}KB)`;

  try {
    await putFile(path, buf, msg, null);
  } catch (e) {
    console.error('photo upload failed', e);
    return res.status(500).json({ error: String(e.message || e) });
  }
  const url = `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${path}`;
  return res.status(200).json({ ok: true, url, path, bytes: buf.length });
};

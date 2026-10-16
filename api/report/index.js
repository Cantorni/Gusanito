/**
 * Azure Function: report
 * POST  receives a pre-labelled photo report as JSON from the PWA, stores it, and returns only an
 *       acknowledgement. The SERVER asks Claude to analyse the photo and keeps the result;
 *       the PWA never sees Claude's answer.
 * GET   (needs REPORT_TOKEN) lists stored reports, or ?image=<file> returns the photo.
 * shortcut: stores on local disk (DATA_DIR); on Azure use Blob/Table storage instead.
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const Anthropic = require('@anthropic-ai/sdk');

const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, '..', '..', 'data');
const MODEL = process.env.CLAUDE_MODEL || 'claude-opus-5-5';
const LABELS = ['armyworm', 'not_armyworm'];
const MEDIA = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp' };
const MAX_IMAGE_B64 = 7_000_000;
const MAX_AI_PER_HOUR = 60; // protects the API credits if the link leaks
const MIN_CONFIDENCE = 0.7; // shortcut: illustrative cut-off, calibrate with agronomist-labelled photos
let aiCalls = [];

// Output format from the project proposal (propuesta-gusano-cogollero.md, section 6)
const ANALYSIS_SCHEMA = {
  type: 'object',
  properties: {
    calidad_foto: { type: 'string', enum: ['buena', 'regular', 'mala'] },
    tipo_vegetacion: { type: 'string', enum: ['maiz', 'pasto', 'sorgo', 'otra', 'ninguna'] },
    es_hospedero: { type: 'boolean' },
    senales_gusano: {
      type: 'array',
      items: { type: 'string', enum: ['hojas_ventana', 'agujeros_irregulares', 'excremento_en_cogollo', 'larva_con_y_invertida', 'masa_de_huevos'] },
    },
    larva_visible: { type: 'boolean' },
    severidad: { type: 'string', enum: ['ninguna', 'baja', 'media', 'alta'] },
    confianza: { type: 'number' },
    requiere_revision_humana: { type: 'boolean' },
    nota_para_felix: { type: 'string' },
  },
  required: ['calidad_foto', 'tipo_vegetacion', 'es_hospedero', 'senales_gusano', 'larva_visible', 'severidad', 'confianza', 'requiere_revision_humana', 'nota_para_felix'],
  additionalProperties: false,
};

const SYSTEM = `Analizas fotos de terreno tomadas por un extensionista agrícola para verificar si un parche de vegetación verde es un reservorio de gusano cogollero (Spodoptera frugiperda) en la estación seca.
Señales típicas: hojas raspadas con aspecto de "ventana", agujeros irregulares, excremento parecido a aserrín en el cogollo, larva con una "Y" invertida en la cabeza, masas de huevos cubiertas de escamas grises.
Reglas:
- tipo_vegetacion: maiz, pasto, sorgo, otra o ninguna. es_hospedero es true para maíz, sorgo y pastos hospederos.
- Si la foto es borrosa, oscura, lejana o no muestra plantas, calidad_foto es "mala", requiere_revision_humana es true y la nota pide repetir la foto.
- Solo marca señales que se vean. Si dudas, baja la confianza (número entre 0 y 1) y pide revisión humana.
- No es un diagnóstico final: puedes confundir el gusano con otros gusanos o barrenadores.
- nota_para_felix: una o dos frases en español, claras y sin jerga.`;

module.exports = async function (context, req) {
  const cors = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'content-type,x-report-token' };
  const json = (status, obj) => {
    context.res = { status, headers: { ...cors, 'Content-Type': 'application/json' }, body: JSON.stringify(obj) };
  };

  if (req.method === 'OPTIONS') { context.res = { status: 204, headers: cors, body: '' }; return; }
  if (req.method === 'GET') return handleGet(context, req, cors, json);

  const b = req.body || {};
  const err = validate(b);
  if (err) return json(400, { error: err });

  const id = crypto.randomUUID();
  const [, mediaType, data] = /^data:([\w/+.-]+);base64,(.+)$/s.exec(b.image);
  fs.mkdirSync(path.join(DATA_DIR, 'images'), { recursive: true });
  const file = `${id}.${MEDIA[mediaType]}`;
  fs.writeFileSync(path.join(DATA_DIR, 'images', file), Buffer.from(data, 'base64'));

  const analisis = await analyze(mediaType, data);
  const worm = analisis.status === 'ok' && (analisis.larva_visible || analisis.senales_gusano.length > 0);

  const record = {
    id,
    receivedAt: new Date().toISOString(),
    label: b.label, // what the extension officer said it is
    image: { file, mediaType, ...pickMeta(b.meta) },
    location: b.location || null, // GPS of the device, filled automatically by the PWA
    analisis, // Claude's analysis, kept on the server
    estado: estado(analisis),
    concuerda: analisis.status === 'ok' ? worm === (b.label === 'armyworm') : null,
  };
  fs.appendFileSync(path.join(DATA_DIR, 'reports.jsonl'), JSON.stringify(record) + '\n');
  json(200, { id, receivedAt: record.receivedAt, status: 'recibido' }); // no analysis back to the PWA
};

function validate(b) {
  if (typeof b.image !== 'string' || b.image.length > MAX_IMAGE_B64) return 'image missing or too large';
  const m = /^data:([\w/+.-]+);base64,/.exec(b.image);
  if (!m || !MEDIA[m[1]]) return 'image must be a base64 data URL (jpeg/png/webp)';
  if (!LABELS.includes(b.label)) return `label must be one of ${LABELS.join(', ')}`;
  const p = b.location;
  if (p != null && (!Number.isFinite(p.lat) || !Number.isFinite(p.lng) || Math.abs(p.lat) > 90 || Math.abs(p.lng) > 180)) return 'invalid location';
  return null;
}

function pickMeta(m = {}) {
  const num = v => (Number.isFinite(v) ? v : null);
  return {
    name: String(m.name || '').slice(0, 120),
    originalSize: num(m.originalSize),
    width: num(m.width),
    height: num(m.height),
    capturedAt: typeof m.capturedAt === 'string' ? m.capturedAt.slice(0, 40) : null,
  };
}

// High confidence -> confirmed or discarded; low confidence or bad photo -> human review (proposal, phase 2).
function estado(a) {
  if (a.status !== 'ok') return 'pendiente'; // analysis failed: needs a retry
  if (a.calidad_foto === 'mala' || a.requiere_revision_humana || a.confianza < MIN_CONFIDENCE) return 'revision_humana';
  return a.es_hospedero && (a.larva_visible || a.senales_gusano.length > 0) ? 'reservorio_confirmado' : 'descartado';
}

async function analyze(mediaType, data) {
  const now = Date.now();
  aiCalls = aiCalls.filter(t => now - t < 3600e3);
  if (aiCalls.length >= MAX_AI_PER_HOUR) return { status: 'rate_limited' };
  if (!process.env.ANTHROPIC_API_KEY) return { status: 'error', reason: 'ANTHROPIC_API_KEY not set' };
  aiCalls.push(now);
  try {
    const client = new Anthropic();
    const res = await client.messages.create({
      model: MODEL,
      max_tokens: 4000,
      system: SYSTEM,
      output_config: { format: { type: 'json_schema', schema: ANALYSIS_SCHEMA } },
      messages: [{
        role: 'user',
        content: [
          { type: 'image', source: { type: 'base64', media_type: mediaType, data } },
          { type: 'text', text: 'Analiza esta foto y responde con el JSON pedido.' },
        ],
      }],
    });
    if (res.stop_reason === 'refusal') return { status: 'refused' };
    const text = res.content.find(c => c.type === 'text');
    const a = JSON.parse(text.text);
    a.confianza = Math.min(1, Math.max(0, a.confianza)); // schema cannot bound the number
    return { status: 'ok', modelo: MODEL, ...a };
  } catch (e) {
    return { status: 'error', reason: e instanceof Anthropic.APIError ? `API ${e.status}` : 'parse/network error' };
  }
}

function handleGet(context, req, cors, json) {
  const token = (req.query || {}).token || (req.headers || {})['x-report-token'];
  const want = process.env.REPORT_TOKEN;
  if (!want) return json(503, { error: 'REPORT_TOKEN not configured' });
  if (token !== want) return json(401, { error: 'bad token' });

  const q = req.query || {};
  if (q.image) {
    const f = path.basename(String(q.image)); // basename blocks path traversal
    const full = path.join(DATA_DIR, 'images', f);
    if (!fs.existsSync(full)) return json(404, { error: 'not found' });
    const ext = path.extname(f).slice(1);
    context.res = { status: 200, headers: { ...cors, 'Content-Type': ext === 'jpg' ? 'image/jpeg' : `image/${ext}` }, body: fs.readFileSync(full), isRaw: true };
    return;
  }
  const f = path.join(DATA_DIR, 'reports.jsonl');
  const rows = fs.existsSync(f) ? fs.readFileSync(f, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)) : [];
  json(200, rows.reverse());
}

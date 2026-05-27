const express = require('express');
const cors = require('cors');
const axios = require('axios');
const { Pool } = require('pg');
require('dotenv').config();

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static('public'));

// Database
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.DATABASE_URL ? { rejectUnauthorized: false } : false
});

// Setup database table
async function setupDB() {
  try {
    await pool.query(`
      CREATE TABLE IF NOT EXISTS activities (
        id BIGINT PRIMARY KEY,
        name TEXT,
        date TEXT,
        type TEXT,
        distance REAL,
        duration REAL,
        avg_hr INTEGER,
        training_effect TEXT,
        is_race BOOLEAN,
        is_pr BOOLEAN,
        raw JSONB,
        created_at TIMESTAMP DEFAULT NOW()
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS cache (
        key TEXT PRIMARY KEY,
        value JSONB,
        updated_at TIMESTAMP DEFAULT NOW()
      )
    `);
    console.log('Databas: klar');
  } catch(e) {
    console.log('Databas ej tillgänglig:', e.message);
  }
}
setupDB();

// Garmin
let GCClient = null;
async function getGarmin() {
  if (GCClient) return GCClient;
  const { GarminConnect } = require('garmin-connect');
  const client = new GarminConnect({
    username: process.env.GARMIN_EMAIL,
    password: process.env.GARMIN_PASSWORD
  });
  await client.login();
  GCClient = client;
  console.log('Garmin: inloggad');
  return client;
}

// Cache helpers
async function getCache(key) {
  try {
    const r = await pool.query('SELECT value FROM cache WHERE key=$1', [key]);
    return r.rows[0]?.value || null;
  } catch(e) { return null; }
}
async function setCache(key, value) {
  try {
    await pool.query(`INSERT INTO cache(key,value,updated_at) VALUES($1,$2,NOW())
      ON CONFLICT(key) DO UPDATE SET value=$2, updated_at=NOW()`, [key, JSON.stringify(value)]);
  } catch(e) {}
}

// Login protection
const PASSWORD = process.env.SITE_PASSWORD || 'hugo123';
app.use((req, res, next) => {
  if (req.path === '/api/login') return next();
  const auth = req.headers['x-site-password'];
  if (auth === PASSWORD) return next();
  if (req.path.startsWith('/api/')) return res.status(401).json({ error: 'Unauthorized' });
  next();
});
app.post('/api/login', (req, res) => {
  if (req.body.password === PASSWORD) {
    res.json({ ok: true });
  } else {
    res.status(401).json({ ok: false });
  }
});

// Status
app.get('/api/status', (req, res) => {
  res.json({ status: 'ok' });
});

// Activities - hämta från DB först, annars Garmin
app.get('/api/activities', async (req, res) => {
  try {
    // Försök hämta från databas
    const dbResult = await pool.query('SELECT raw FROM activities ORDER BY date DESC LIMIT 50');
    if (dbResult.rows.length > 0) {
      const activities = dbResult.rows.map(r => r.raw);
      return res.json({ activities, source: 'database' });
    }
    // Annars hämta från Garmin och spara
    const client = await getGarmin();
    const activities = await client.getActivities(0, 30);
    await saveActivitiesToDB(activities);
    res.json({ activities, source: 'garmin' });
  } catch(e) {
    console.error('Activities error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

async function saveActivitiesToDB(activities) {
  for (const a of activities) {
    try {
      await pool.query(`
        INSERT INTO activities(id,name,date,type,distance,duration,avg_hr,training_effect,is_race,is_pr,raw)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        ON CONFLICT(id) DO UPDATE SET raw=$11, name=$2
      `, [
        a.activityId, a.activityName, a.startTimeLocal,
        a.activityType?.typeKey, a.distance, a.duration,
        a.averageHR, a.trainingEffectLabel,
        a.eventType?.typeKey === 'race', a.pr || false,
        JSON.stringify(a)
      ]);
    } catch(e) {}
  }
}

// Refresh - hämta ny data från Garmin + AI-analys
app.post('/api/refresh', async (req, res) => {
  try {
    // Kolla cache (max 5 min)
    const cached = await getCache('analysis');
    if (cached && cached.timestamp && (Date.now() - cached.timestamp) < 5 * 60 * 1000) {
      return res.json(cached.data);
    }

    const client = await getGarmin();
    const activities = await client.getActivities(0, 10);
    await saveActivitiesToDB(activities);

    const recentRuns = activities
      .filter(a => a.activityType?.typeKey?.includes('running'))
      .slice(0, 5)
      .map(a => ({
        name: a.activityName,
        date: a.startTimeLocal,
        distance: (a.distance/1000).toFixed(1) + ' km',
        duration: Math.round(a.duration/60) + ' min',
        avgHR: a.averageHR,
        trainingEffect: a.trainingEffectLabel
      }));

    if (!process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_API_KEY === 'sk-ant-placeholder') {
      return res.json({
        todayRecommendation: 'API-nyckel saknas.',
        todayType: 'easy',
        nextSession: { title: 'Lugnt jogg', desc: 'Z2, 30-40 min', tempo: '4:45-5:15 /km', distance: '~6 km' },
        prediction3k: '10:27',
        insight: 'Aktivera AI för personliga insikter.'
      });
    }

    const context = `Du är en träningscoach. Analysera och svara ENDAST med JSON.

Senaste löppass:
${JSON.stringify(recentRuns, null, 2)}

Mål: 3 km under 10 minuter. Bästa: 10:27.
Plan: återhämtning v.23 → intervaller v.24-25 → tröskel v.26-29 → spetsning v.30-34.

Svara ENDAST med detta JSON:
{
  "todayRecommendation": "rekommendation idag (1-2 meningar)",
  "todayType": "easy|quality|rest",
  "nextSession": {
    "title": "passnamn",
    "desc": "beskrivning",
    "tempo": "t.ex. 3:35 /km",
    "distance": "t.ex. ~8 km"
  },
  "prediction3k": "t.ex. 10:15",
  "insight": "en konkret insikt (1 mening)"
}`;

    const aiRes = await axios.post('https://api.anthropic.com/v1/messages', {
      model: 'claude-sonnet-4-5',
      max_tokens: 500,
      messages: [{ role: 'user', content: context }]
    }, {
      headers: {
        'x-api-key': process.env.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json'
      }
    });

    const text = aiRes.data.content[0].text.trim();
    const clean = text.replace(/```json|```/g, '').trim();
    const analysis = JSON.parse(clean);

    await setCache('analysis', { timestamp: Date.now(), data: analysis });
    res.json(analysis);

  } catch(e) {
    console.error('Refresh error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// Chat
app.post('/api/chat', async (req, res) => {
  const { message, context } = req.body;
  if (!process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_API_KEY === 'sk-ant-placeholder') {
    return res.json({ reply: 'API-nyckel saknas.' });
  }
  try {
    const response = await axios.post('https://api.anthropic.com/v1/messages', {
      model: 'claude-sonnet-4-5',
      max_tokens: 1024,
      system: context || 'Du är en personlig träningscoach. Svara på svenska.',
      messages: [{ role: 'user', content: message }]
    }, {
      headers: {
        'x-api-key': process.env.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json'
      }
    });
    res.json({ reply: response.data.content[0].text });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Servern körs på http://localhost:${PORT}`);
});
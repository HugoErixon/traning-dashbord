const express = require('express');
const cors = require('cors');
const axios = require('axios');
require('dotenv').config();

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static('public'));

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

app.get('/api/status', (req, res) => {
  res.json({ status: 'ok' });
});

app.get('/api/activities', async (req, res) => {
  try {
    const client = await getGarmin();
    const days = parseInt(req.query.days) || 30;
    const activities = await client.getActivities(0, days);
    res.json({ activities });
  } catch(e) {
    console.error('Activities error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/refresh', async (req, res) => {
  try {
    const client = await getGarmin();

   const [activities] = await Promise.all([
  client.getActivities(0, 7)
]);
const stats = {};

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

    const context = `Du är en träningscoach. Analysera denna data och svara med ett JSON-objekt.

Garmin-data idag:
- Senaste aktiviteter analyseras nedan

Senaste 5 löppass:
${JSON.stringify(recentRuns, null, 2)}

Mål: springa 3 km under 10 minuter. Nuvarande bästa: 10:27.
Träningsplan: återhämtning v.23 → intervaller v.24-25 → tröskel v.26-29 → spetsning v.30-34.

Svara ENDAST med detta JSON (inga andra tecken):
{
  "todayRecommendation": "kort rekommendation för idag (1-2 meningar)",
  "todayType": "easy|quality|rest",
  "nextSession": {
    "title": "passnamn",
    "desc": "beskrivning",
    "tempo": "t.ex. 3:35 /km",
    "distance": "t.ex. ~8 km"
  },
  "prediction3k": "t.ex. 10:15",
  "insight": "en konkret insikt baserat på senaste passen (1 mening)"
}`;

    if (!process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_API_KEY === 'sk-ant-placeholder') {
      return res.json({
        todayRecommendation: 'API-nyckel saknas – lägg till Anthropic-nyckel för AI-analys.',
        todayType: 'easy',
        nextSession: { title: 'Lugnt jogg', desc: 'Z2, 30-40 min', tempo: '4:45-5:15 /km', distance: '~6 km' },
        prediction3k: '10:27',
        insight: 'Aktivera AI för personliga insikter.'
      });
    }

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
    res.json(analysis);

  } catch(e) {
    console.error('Refresh error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/chat', async (req, res) => {
  const { message, context } = req.body;

  if (!process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_API_KEY === 'sk-ant-placeholder') {
    return res.json({
      reply: 'API-nyckel saknas – lägg till en riktig Anthropic-nyckel i .env för att aktivera AI-chatten.'
    });
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

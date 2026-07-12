// Scriptable widget for Training Dashboard.
// Run the script once inside Scriptable to connect it to the dashboard.

const DASHBOARD_URL = "https://raspberrypi.tail91d6c1.ts.net";
const SITE_USER = "hugo";
const RESET_WIDGET_TOKEN = false;

const cleanBaseUrl = DASHBOARD_URL.replace(/\/$/, "");
const keychainKey = "training-dashboard-widget:" + cleanBaseUrl + ":" + SITE_USER;

async function loadJson(request) {
  const body = await request.loadJSON();
  const status = request.response ? request.response.statusCode : 0;
  if (status < 200 || status >= 300) {
    const error = new Error(body && body.error ? body.error : "HTTP " + status);
    error.statusCode = status;
    throw error;
  }
  return body;
}

function cookieHeader(cookies) {
  return (cookies || []).map(cookie => cookie.name + "=" + cookie.value).join("; ");
}

async function issueWidgetToken() {
  if (!config.runsInApp) {
    throw new Error("Öppna scriptet i Scriptable en gång för att ansluta widgeten.");
  }

  const alert = new Alert();
  alert.title = "Anslut träningswidgeten";
  alert.message = "Logga in som " + SITE_USER + ". Lösenordet används bara för anslutningen och sparas inte.";
  alert.addSecureTextField("Dashboardlösenord", "");
  alert.addAction("Anslut");
  alert.addCancelAction("Avbryt");
  const choice = await alert.presentAlert();
  if (choice === -1) throw new Error("Anslutningen avbröts.");

  const login = new Request(cleanBaseUrl + "/api/login");
  login.method = "POST";
  login.headers = { "Content-Type": "application/json" };
  login.body = JSON.stringify({ username: SITE_USER, password: alert.textFieldValue(0) });
  login.timeoutInterval = 15;
  const loginData = await loadJson(login);
  const cookies = cookieHeader(login.response && login.response.cookies);
  if (!cookies || !loginData.csrfToken) throw new Error("Inloggningen saknar sessionsdata.");

  const tokenRequest = new Request(cleanBaseUrl + "/api/widget/token");
  tokenRequest.method = "POST";
  tokenRequest.headers = {
    "Content-Type": "application/json",
    "Cookie": cookies,
    "X-CSRF-Token": loginData.csrfToken
  };
  tokenRequest.body = "{}";
  tokenRequest.timeoutInterval = 15;
  const tokenData = await loadJson(tokenRequest);
  Keychain.set(keychainKey, tokenData.token);
  return tokenData.token;
}

async function fetchWidgetData(token) {
  const request = new Request(cleanBaseUrl + "/api/widget/mobile");
  request.headers = { "Authorization": "Bearer " + token };
  request.timeoutInterval = 12;
  return await loadJson(request);
}

async function getWidgetData() {
  if (RESET_WIDGET_TOKEN && Keychain.contains(keychainKey)) Keychain.remove(keychainKey);
  let token = Keychain.contains(keychainKey) ? Keychain.get(keychainKey) : await issueWidgetToken();
  try {
    return await fetchWidgetData(token);
  } catch (error) {
    if (error.statusCode !== 401) throw error;
    if (Keychain.contains(keychainKey)) Keychain.remove(keychainKey);
    token = await issueWidgetToken();
    return await fetchWidgetData(token);
  }
}

let data;
try {
  data = await getWidgetData();
} catch (error) {
  data = { error: String(error.message || error) };
}

const ICE = "#5AC8FA";
const GREEN = "#7ED957";
const AMBER = "#FFB648";
const RED = "#FF5A5F";
const MUTED_ICON = "#6B7690";
const LABEL_GRAY = "#8B93A7";
const SUB_GRAY = "#AAB2C4";

const widget = new ListWidget();
const bg = new LinearGradient();
bg.colors = [new Color("#070C14"), new Color("#101B2E"), new Color("#0A1420")];
bg.locations = [0, 0.55, 1];
widget.backgroundGradient = bg;
widget.setPadding(13, 13, 11, 13);

const family = config.widgetFamily || "medium";
const isSmall = family === "small";

function addText(stack, value, size, color, weight, mono) {
  const text = stack.addText(String(value));
  const bold = weight === "bold";
  text.font = mono
    ? (bold ? Font.boldMonospacedSystemFont(size) : Font.regularMonospacedSystemFont(size))
    : (bold ? Font.boldRoundedSystemFont(size) : Font.systemFont(size));
  text.textColor = new Color(color || "#E5E7EB");
  text.minimumScaleFactor = 0.65;
  text.lineLimit = 2;
  return text;
}

function addIcon(stack, symbolName, color, size) {
  try {
    const symbol = SFSymbol.named(symbolName);
    symbol.applyFont(Font.systemFont(size));
    const image = stack.addImage(symbol.image);
    image.imageSize = new Size(size, size);
    image.tintColor = new Color(color);
    return image;
  } catch (_) {
    return null;
  }
}

function addCard(parent, card) {
  const stack = parent.addStack();
  stack.layoutVertically();
  stack.backgroundColor = new Color("#121B2B");
  stack.cornerRadius = 14;
  stack.borderColor = new Color("#22304A");
  stack.borderWidth = 1;
  stack.setPadding(11, 12, 10, 12);
  const top = stack.addStack();
  top.layoutHorizontally();
  top.centerAlignContent();
  addIcon(top, card.icon, MUTED_ICON, isSmall ? 10 : 11);
  top.addSpacer(5);
  addText(top, card.label.toUpperCase(), isSmall ? 8 : 9, LABEL_GRAY, "bold", false);
  top.addSpacer();
  const chip = top.addStack();
  chip.size = new Size(8, 11);
  chip.cornerRadius = 2;
  chip.backgroundColor = new Color(card.color);
  stack.addSpacer(isSmall ? 5 : 8);
  addText(stack, card.value, isSmall ? 20 : 26, card.color, "bold", true);
  stack.addSpacer(2);
  addText(stack, card.sub, isSmall ? 9 : 10, SUB_GRAY, "regular", false);
}

function addGrid(cards) {
  if (isSmall) {
    cards.forEach((card, index) => {
      addCard(widget, card);
      if (index < cards.length - 1) widget.addSpacer(6);
    });
    return;
  }
  const first = widget.addStack();
  first.layoutHorizontally();
  addCard(first, cards[0]);
  first.addSpacer(8);
  addCard(first, cards[1]);
  widget.addSpacer(8);
  const second = widget.addStack();
  second.layoutHorizontally();
  addCard(second, cards[2]);
  second.addSpacer(8);
  addCard(second, cards[3]);
}

function numberOrNull(value) {
  if (value === null || value === undefined) return null;
  const number = Number(value);
  return isNaN(number) ? null : number;
}

if (data.error) {
  addText(widget, "Dashboard", 13, LABEL_GRAY, "bold", false);
  widget.addSpacer(8);
  addText(widget, "Kunde inte hämta data", 16, RED, "bold", false);
  widget.addSpacer(4);
  addText(widget, data.error, 10, LABEL_GRAY, "regular", false);
} else {
  const volume = data.weeklyVolume || {};
  const cns = data.cns || {};
  const sleep = data.sleep || {};
  const next = data.nextQuality || {};
  const header = widget.addStack();
  header.layoutHorizontally();
  header.centerAlignContent();
  addText(header, "VECKA " + (data.week || ""), 11, ICE, "bold", false);
  header.addSpacer();
  addText(header, new Date().toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit" }), 10, "#5C6780", "regular", false);
  widget.addSpacer(6);
  const rule = widget.addStack();
  rule.size = new Size(isSmall ? 118 : 300, 1);
  rule.backgroundColor = new Color(ICE, 0.25);
  widget.addSpacer(9);

  const completed = numberOrNull(volume.completedKm) || 0;
  const planned = numberOrNull(volume.plannedKm);
  const remaining = numberOrNull(volume.remainingKm);
  const volumeValue = planned !== null ? completed.toFixed(1) + "/" + planned.toFixed(0) : completed.toFixed(1);
  const volumeSub = planned !== null ? (remaining !== null ? remaining.toFixed(1) : "0.0") + " km kvar" : "km denna vecka";
  const cnsScore = numberOrNull(cns.score);
  const cnsColor = cnsScore !== null && cnsScore >= 70 ? GREEN : cnsScore !== null && cnsScore >= 45 ? AMBER : RED;
  const cnsSub = cnsScore === null ? "ingen data" : cnsScore >= 70 ? "redo för kvalitet" : cnsScore >= 45 ? "normalt pass ok" : "vila eller Z2";
  const sleepScore = numberOrNull(sleep.score);
  const sleepColor = sleepScore !== null && sleepScore >= 80 ? GREEN : sleepScore !== null && sleepScore >= 60 ? AMBER : RED;
  const sleepSub = sleepScore === null ? "ingen data" : sleepScore >= 80 ? "bra återhämtning" : sleepScore >= 60 ? "acceptabelt" : "prioritera sömn";
  const qualityDate = next.date ? new Date(next.date).toLocaleDateString("sv-SE", { weekday: "short", day: "numeric", month: "short" }) : "";
  const qualityKm = numberOrNull(next.km);

  addGrid([
    { icon: "figure.run", label: "Veckovolym", value: planned !== null ? volumeValue : completed.toFixed(1), sub: volumeSub, color: ICE },
    { icon: "waveform.path.ecg", label: "CNS-score", value: cnsScore === null ? "-" : String(cnsScore), sub: cnsSub, color: cnsColor },
    { icon: "moon.zzz.fill", label: "Sömnscore", value: sleepScore === null ? "-" : String(sleepScore), sub: sleepSub, color: sleepColor },
    { icon: "calendar", label: "Kvalitet", value: qualityKm !== null ? qualityKm.toFixed(0) + " km" : "-", sub: next.title ? qualityDate + " · " + next.title : "inget planerat", color: ICE }
  ]);
}

if (!config.runsInWidget) await widget.presentMedium();
Script.setWidget(widget);
Script.complete();

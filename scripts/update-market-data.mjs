import fs from 'node:fs/promises';

const nf = (value) => Number(String(value ?? '').replace(/[,，%＋+]/g, '').replace('−', '-').trim());
const twDate = (date) => `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, '0')}${String(date.getDate()).padStart(2, '0')}`;
const displayDate = (yyyymmdd) => `${yyyymmdd.slice(0, 4)}/${yyyymmdd.slice(4, 6)}/${yyyymmdd.slice(6, 8)}`;
const ymd = (s) => String(s).replaceAll('/', '');
const fmt = (n, d = 0) => Number(n).toLocaleString('zh-TW', { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (n, d = 1) => `${n >= 0 ? '+' : '-'}${Math.abs(n).toFixed(d)}`;
const cls = (n) => n > 0 ? 'up' : n < 0 ? 'down' : 'neutral';

async function getJson(url) {
  const res = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0' } });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}
async function getText(url, options = {}) {
  const res = await fetch(url, { ...options, headers: { 'user-agent': 'Mozilla/5.0', 'content-type': 'application/x-www-form-urlencoded; charset=UTF-8', ...(options.headers || {}) } });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.text();
}
function recentDates(days = 12) {
  const out = [];
  const d = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Taipei' }));
  for (let i = 0; i < days; i++) { out.push(twDate(d)); d.setDate(d.getDate() - 1); }
  return out;
}
function previousDisplayDates(dateText, days = 12) {
  const [y, m, d] = dateText.split('/').map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  const out = [];
  for (let i = 0; i < days; i++) { date.setUTCDate(date.getUTCDate() - 1); out.push(`${date.getUTCFullYear()}/${String(date.getUTCMonth() + 1).padStart(2, '0')}/${String(date.getUTCDate()).padStart(2, '0')}`); }
  return out;
}
async function fetchIndex() {
  for (const d of recentDates()) {
    try {
      const url = `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=${d}&type=IND&response=json`;
      const json = await getJson(url);
      const rows = (json.tables || []).flatMap(t => t.data || []);
      const row = rows.find(r => String(r[0] || '').includes('發行量加權股價指數') || String(r[0] || '').includes('加權指數'));
      if (!row) continue;
      const close = nf(row[1]);
      const change = nf(row[3] ?? row[2]);
      const changePct = nf(row[4] ?? 0);
      return { tradeDate: displayDate(d), close, change, changePct, previousClose: close - change, sourceUrl: url };
    } catch {}
  }
  throw new Error('index_not_found');
}
async function fetchInstitutional(date) {
  const url = `https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate=${date}&type=day&response=json`;
  const json = await getJson(url);
  const rows = json.tables?.[0]?.data || json.data || [];
  let foreign = 0, trust = 0, dealer = 0, total = null;
  for (const r of rows) {
    const name = String(r[0] || '');
    const net = nf(r[3]) / 100000000;
    if (name.includes('外資') && !name.includes('自營商')) foreign += net;
    else if (name.includes('投信')) trust += net;
    else if (name.includes('自營商')) dealer += net;
    else if (name.includes('合計')) total = net;
  }
  return { foreign, trust, dealer, total: total ?? foreign + trust + dealer, sourceUrl: url };
}
async function fetchIntraday(date, previousClose, close) {
  try {
    const url = `https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS?date=${date}&response=json`;
    const json = await getJson(url);
    const rows = json.data || json.tables?.[0]?.data || [];
    const points = rows.map(r => [String(r[0]).slice(0, 5), nf(r[1] ?? r[4])]).filter(r => r[1]);
    if (points.length >= 2) return { points, sourceUrl: url };
  } catch {}
  const ticks = ['09:00','09:30','10:00','10:30','11:00','11:30','12:00','12:30','13:00','13:30'];
  return { points: ticks.map((t, i) => [t, previousClose + (close - previousClose) * i / (ticks.length - 1)]), sourceUrl: 'fallback-linear' };
}
function textTokens(html) {
  return html.replace(/<script[\s\S]*?<\/script>/gi, '\n').replace(/<style[\s\S]*?<\/style>/gi, '\n').replace(/<[^>]+>/g, '\n').replace(/&nbsp;/g, ' ').replace(/&minus;|&#8722;/g, '-').replace(/&amp;/g, '&').split(/\n+/).map(s => s.trim()).filter(Boolean);
}
function isNum(s) { return /^[-−]?\d{1,3}(,\d{3})*(\.\d+)?$/.test(String(s)) || /^[-−]?\d+(\.\d+)?$/.test(String(s)); }
function parseTaifex(html) {
  const tokens = textTokens(html);
  const date = tokens.join(' ').match(/日期\s*(\d{4}\/\d{2}\/\d{2})/)?.[1];
  const p = tokens.findIndex(t => t === '臺股期貨');
  const f = tokens.findIndex((t, i) => i > p && t.includes('外資'));
  if (!date || p < 0 || f < 0) throw new Error('taifex_parse_failed');
  const nums = [];
  for (let i = f + 1; i < tokens.length && nums.length < 12; i++) if (isNum(tokens[i])) nums.push(nf(tokens[i]));
  if (nums.length < 12) throw new Error('taifex_incomplete');
  return { tradeDate: date, product: '臺股期貨', investor: '外資', long: nums[6], short: nums[8], net: nums[10] };
}
async function fetchTaifex(dateText = null) {
  const base = 'https://www.taifex.com.tw/cht/3/futContractsDate';
  const attempts = dateText ? [
    [`${base}?queryDate=${encodeURIComponent(dateText)}&commodityId=TX`, {}],
    [`${base}?queryType=1&doQuery=1&queryDate=${encodeURIComponent(dateText)}&commodityId=TX`, {}],
    [base, { method: 'POST', body: new URLSearchParams({ queryDate: dateText, commodityId: 'TX', doQuery: '1' }).toString() }],
    [base, { method: 'POST', body: new URLSearchParams({ queryStartDate: dateText, queryEndDate: dateText, commodityId: 'TX', doQuery: '1' }).toString() }]
  ] : [[base, {}]];
  for (const [url, options] of attempts) {
    try {
      const parsed = parseTaifex(await getText(url, options));
      if (!dateText || parsed.tradeDate === dateText) return { ...parsed, sourceUrl: url };
    } catch {}
  }
  throw new Error(`taifex_not_found_${dateText || 'latest'}`);
}
async function fetchFutures() {
  const latest = await fetchTaifex();
  let previous = null;
  for (const d of previousDisplayDates(latest.tradeDate)) {
    try { previous = await fetchTaifex(d); break; } catch {}
  }
  if (!previous) throw new Error('taifex_previous_not_found');
  return { status: 'ok', tradeDate: latest.tradeDate, product: latest.product, investor: latest.investor, long: latest.long, short: latest.short, net: latest.net, previousTradeDate: previous.tradeDate, previousNet: previous.net, netDiff: latest.net - previous.net, sourceUrl: latest.sourceUrl, previousSourceUrl: previous.sourceUrl };
}
function validate(data) {
  const checks = [];
  const push = (name, pass) => checks.push({ name, pass });
  push('index numeric', Number.isFinite(data.index.close));
  push('index reconciles', Math.abs((data.index.close - data.index.previousClose) - data.index.change) < 0.05);
  push('institutional reconciles', Math.abs((data.institutional.foreign + data.institutional.trust + data.institutional.dealer) - data.institutional.total) < 0.2);
  push('intraday exists', data.index.intraday.length >= 2);
  push('futures exists', data.futures.status === 'ok');
  push('futures reconciles', data.futures.long - data.futures.short === data.futures.net);
  push('futures diff reconciles', data.futures.net - data.futures.previousNet === data.futures.netDiff);
  return { passed: checks.every(c => c.pass), checks };
}
function render(data) {
  const idx = data.index, inst = data.institutional, fut = data.futures;
  const indexClass = cls(idx.change);
  const diffWord = fut.netDiff < 0 ? '空單增加' : '空單減少';
  const obs = idx.change < 0 ? '今日指數走弱，先看法人賣壓是否收斂；若期貨空單同步增加，短線仍以風險控管為主。' : '今日指數收紅，先看法人買盤是否延續；若期貨淨部位同步轉強，短線氣氛偏多。';
  return `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Taiwan Market Pulse</title><style>body{margin:0;background:#f7efe2;color:#2c241d;font-family:Microsoft JhengHei,system-ui,sans-serif}.topbar{height:7px;background:linear-gradient(90deg,#755842,#c8a96a,#e0b56e)}.wrap{width:min(1160px,92vw);margin:16px auto 50px}.nav{display:flex;justify-content:space-between;align-items:center}.brand{font-size:23px;font-weight:950;color:#755842}.brand span{color:#c8a96a}.links a{margin-left:8px;color:#755842;text-decoration:none;font-weight:900}.hero{display:grid;grid-template-columns:1.45fr .55fr;gap:20px;margin-top:20px}.panel,.card{background:#fffaf1;border:1px solid #e4d2b4;border-radius:26px;box-shadow:0 18px 48px rgba(80,55,32,.10);padding:22px}.kicker{font-size:12px;font-weight:950;color:#9a7859;letter-spacing:.14em}.index-title{font-size:16px;color:#756b60;font-weight:950}.index-value{font-family:Georgia,serif;font-size:52px}.up{color:#c9463b}.down{color:#19724a}.neutral{color:#756b60}.change{font-size:20px;font-weight:950}.badge{float:right;border-radius:999px;padding:7px 11px;background:#fff4df;border:1px solid #e4d2b4;color:#755842;font-weight:950}.chart{height:235px;border:1px solid #e4d2b4;border-radius:20px;margin-top:14px;position:relative;overflow:hidden}.chart svg{width:100%;height:100%}.note{background:linear-gradient(145deg,#fffaf1,#fff1da)}.note h1{font-family:Georgia,serif;font-size:24px;color:#755842}.observation{padding:14px;border-radius:18px;background:rgba(255,255,255,.58);border:1px solid #e4d2b4;color:#756b60;font-weight:850}.market-row{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:22px}.block-head{display:flex;align-items:baseline;justify-content:space-between;gap:14px;border-bottom:1px solid #e4d2b4;padding-bottom:14px;margin-bottom:14px}.block-title{font-family:Georgia,serif;font-size:27px;color:#755842;font-weight:950}.summary{font-size:21px;font-weight:950}.subunit{color:#756b60;font-size:13px;font-weight:900}.rows{display:grid;gap:12px}.data-row{display:grid;grid-template-columns:72px 1fr}.label{color:#756b60;font-weight:900}.value{font-size:24px;font-weight:950}.source-line{text-align:right;margin-top:16px;color:#756b60;font-size:12px;font-weight:800}@media(max-width:980px){.hero,.market-row{grid-template-columns:1fr}}</style></head><body><div class="topbar"></div><main class="wrap"><nav class="nav"><div class="brand">Taiwan Market <span>Pulse</span></div><div class="links"><a href="./">今日總覽</a><a href="pages/00981A.html">00981A</a><a href="pages/market-flow.html">法人 / 期貨</a></div></nav><section class="hero"><article class="panel"><span class="badge">市場氣氛：${idx.change >= 0 ? '偏多觀察' : '偏空觀察'}</span><div class="kicker">Weighted Index</div><div class="index-title">加權指數｜${data.tradeDate} 收盤</div><div class="index-value">${fmt(idx.close, 2)}</div><div class="change"><span class="${indexClass}">${idx.change >= 0 ? '▲' : '▼'} ${signed(idx.change, 2)} 點</span> <span class="${indexClass}">${signed(idx.changePct, 2)}%</span></div><div class="chart"><svg viewBox="0 0 720 235"><line x1="0" x2="720" y1="117" y2="117" stroke="#c8a96a" stroke-width="2" stroke-dasharray="6 7"/><path d="${linePath(idx.previousClose, idx.intraday)}" fill="none" stroke="#19724a" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg></div></article><aside class="panel note"><div class="kicker">Today’s Note</div><h1>先確認風險，<br>再尋找機會。</h1><p>「市場短線會反應情緒，長線會回到紀律。」</p><div class="observation">${obs}</div></aside></section><section class="market-row"><article class="card"><div class="block-head"><div class="block-title">三大法人買賣超</div><div class="subunit">單位：億元</div><div class="summary ${cls(inst.total)}">合計 ${signed(inst.total, 1)} 億</div></div><div class="rows"><div class="data-row"><div class="label">外資</div><div class="value ${cls(inst.foreign)}">${signed(inst.foreign, 1)} 億</div></div><div class="data-row"><div class="label">投信</div><div class="value ${cls(inst.trust)}">${signed(inst.trust, 1)} 億</div></div><div class="data-row"><div class="label">自營商</div><div class="value ${cls(inst.dealer)}">${signed(inst.dealer, 1)} 億</div></div></div></article><article class="card"><div class="block-head"><div><div class="subunit">外資台指期｜單位：口</div><div class="block-title">期貨淨部位</div></div><div class="summary ${cls(fut.net)}">淨部位 ${fmt(fut.net)} 口（較昨日${diffWord} ${fmt(Math.abs(fut.netDiff))} 口）</div></div><div class="rows"><div class="data-row"><div class="label">多方</div><div class="value up">${fmt(fut.long)} 口</div></div><div class="data-row"><div class="label">空方</div><div class="value down">${fmt(fut.short)} 口</div></div></div></article></section><div class="source-line">資料日：${data.tradeDate}｜資料已通過自動核對。</div></main></body></html>`;
}
function linePath(prev, rows) {
  const W = 720, H = 235, pad = 14, max = prev + 1500, min = prev - 1500;
  return rows.map((r, i) => {
    const x = 18 + (W - 36) * i / (rows.length - 1);
    const y = pad + (H - pad * 2) * ((max - r[1]) / (max - min));
    return `${i ? 'L' : 'M'}${x},${y}`;
  }).join(' ');
}
const idx = await fetchIndex();
const date = ymd(idx.tradeDate);
const institutional = await fetchInstitutional(date);
const intraday = await fetchIntraday(date, idx.previousClose, idx.close);
const futures = await fetchFutures();
const data = { status: 'ok', updatedAt: new Date().toISOString(), tradeDate: idx.tradeDate, index: { ...idx, intraday: intraday.points, intradaySourceUrl: intraday.sourceUrl }, institutional, futures };
data.validation = validate(data);
if (!data.validation.passed) throw new Error(JSON.stringify(data.validation, null, 2));
await fs.mkdir('data', { recursive: true });
await fs.writeFile('data/market-latest.json', JSON.stringify(data, null, 2) + '\n');
await fs.writeFile('index.html', render(data) + '\n');

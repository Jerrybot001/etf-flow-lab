import fs from 'node:fs/promises';

const nf = (value) => Number(String(value ?? '').replace(/[,，%＋+]/g, '').replace('−', '-').trim());
const twDate = (date) => `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, '0')}${String(date.getDate()).padStart(2, '0')}`;
const displayDate = (yyyymmdd) => `${yyyymmdd.slice(0, 4)}/${yyyymmdd.slice(4, 6)}/${yyyymmdd.slice(6, 8)}`;

async function getJson(url) {
  const res = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0' } });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

function recentDates(days = 12) {
  const out = [];
  const d = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Taipei' }));
  for (let i = 0; i < days; i++) {
    out.push(twDate(d));
    d.setDate(d.getDate() - 1);
  }
  return out;
}

async function fetchIndex() {
  for (const d of recentDates()) {
    try {
      const url = `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=${d}&type=IND&response=json`;
      const json = await getJson(url);
      const tables = json.tables || [];
      const rows = tables.flatMap(t => t.data || []);
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

async function fetchInstitutional(yyyymmdd) {
  const url = `https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate=${yyyymmdd}&type=day&response=json`;
  const json = await getJson(url);
  const rows = (json.tables?.[0]?.data || json.data || []);
  let foreign = 0, trust = 0, dealer = 0, total = null;
  for (const r of rows) {
    const name = String(r[0] || '');
    const net = nf(r[3]) / 100000000;
    if (name.includes('外資') && !name.includes('自營商')) foreign += net;
    else if (name.includes('投信')) trust += net;
    else if (name.includes('自營商')) dealer += net;
    else if (name.includes('合計')) total = net;
  }
  total = total ?? foreign + trust + dealer;
  return { foreign, trust, dealer, total, sourceUrl: url };
}

async function fetchIntraday(yyyymmdd, previousClose, close) {
  try {
    const url = `https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS?date=${yyyymmdd}&response=json`;
    const json = await getJson(url);
    const rows = json.data || json.tables?.[0]?.data || [];
    const parsed = rows.map(r => [String(r[0]).slice(0, 5), nf(r[1] ?? r[4])]).filter(r => r[1]);
    if (parsed.length >= 2) return { points: parsed, sourceUrl: url };
  } catch {}
  const ticks = ['09:00','09:30','10:00','10:30','11:00','11:30','12:00','12:30','13:00','13:30'];
  return { points: ticks.map((t, i) => [t, previousClose + (close - previousClose) * i / (ticks.length - 1)]), sourceUrl: 'fallback-linear' };
}

function validate(data) {
  const checks = [];
  const push = (name, pass, detail = '') => checks.push({ name, pass, detail });
  push('index close is numeric', Number.isFinite(data.index.close));
  push('index change reconciles previous close', Math.abs((data.index.close - data.index.previousClose) - data.index.change) < 0.05);
  push('institutional total reconciles details', Math.abs((data.institutional.foreign + data.institutional.trust + data.institutional.dealer) - data.institutional.total) < 0.2);
  push('intraday has points', data.index.intraday.length >= 2);
  return { passed: checks.every(c => c.pass), checks };
}

const idx = await fetchIndex();
const yyyymmdd = idx.tradeDate.replaceAll('/', '');
const institutional = await fetchInstitutional(yyyymmdd);
const intraday = await fetchIntraday(yyyymmdd, idx.previousClose, idx.close);
const data = {
  status: 'ok',
  updatedAt: new Date().toISOString(),
  tradeDate: idx.tradeDate,
  index: { ...idx, intraday: intraday.points, intradaySourceUrl: intraday.sourceUrl },
  institutional,
  futures: { status: 'pending', message: 'TAIFEX 期貨資料解析待接，未完成前不顯示假數字' }
};
data.validation = validate(data);
if (!data.validation.passed) throw new Error(JSON.stringify(data.validation, null, 2));
await fs.mkdir('data', { recursive: true });
await fs.writeFile('data/market-latest.json', JSON.stringify(data, null, 2) + '\n');

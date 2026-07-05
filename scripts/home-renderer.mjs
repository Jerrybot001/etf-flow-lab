const nf = new Intl.NumberFormat('zh-TW');
const nf2 = new Intl.NumberFormat('zh-TW', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const ticks = ['09:00','09:30','10:00','10:30','11:00','11:30','12:00','12:30','13:00','13:30'];
const $ = (id) => document.getElementById(id);
const signed = (v, d = 1) => `${v >= 0 ? '+' : '-'}${Math.abs(v).toFixed(d)}`;

function setClass(el, value) {
  el.classList.remove('up', 'down', 'neutral');
  el.classList.add(value > 0 ? 'up' : value < 0 ? 'down' : 'neutral');
}

function setMoney(id, value, prefix = '') {
  const el = $(id);
  setClass(el, value);
  el.textContent = `${prefix}${signed(value, 1)} 億`;
}

function renderAxis(previousClose) {
  const yAxis = $('yAxis');
  yAxis.innerHTML = '';
  for (let i = 0; i <= 6; i++) {
    const tick = document.createElement('span');
    tick.className = 'y-tick';
    tick.style.top = `${(i / 6) * 100}%`;
    tick.textContent = nf.format(Math.round(previousClose + 1500 - i * 500));
    yAxis.appendChild(tick);
  }
}

function renderChart(previousClose, rows) {
  const svg = $('chartSvg');
  const xAxis = $('xAxis');
  svg.innerHTML = '';
  xAxis.innerHTML = '';
  ticks.forEach((time) => {
    const item = document.createElement('span');
    item.textContent = time;
    xAxis.appendChild(item);
  });
  const W = 720, H = 205, pad = 12;
  const max = previousClose + 1500;
  const min = previousClose - 1500;
  for (let i = 0; i <= 6; i++) {
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    const y = pad + (H - pad * 2) * (i / 6);
    line.setAttribute('x1', 0);
    line.setAttribute('x2', W);
    line.setAttribute('y1', y);
    line.setAttribute('y2', y);
    line.setAttribute('stroke', 'rgba(117,88,66,.13)');
    svg.appendChild(line);
  }
  const flat = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  const flatY = pad + (H - pad * 2) * ((max - previousClose) / (max - min));
  flat.setAttribute('x1', 0);
  flat.setAttribute('x2', W);
  flat.setAttribute('y1', flatY);
  flat.setAttribute('y2', flatY);
  flat.setAttribute('stroke', '#c8a96a');
  flat.setAttribute('stroke-width', '2');
  flat.setAttribute('stroke-dasharray', '6 7');
  svg.appendChild(flat);
  if (!rows || rows.length < 2) return;
  const pathText = rows.map((r, i) => {
    const x = 18 + (W - 36) * i / (rows.length - 1);
    const y = pad + (H - pad * 2) * ((max - r[1]) / (max - min));
    return `${i ? 'L' : 'M'}${x},${y}`;
  }).join(' ');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', pathText);
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', '#19724a');
  path.setAttribute('stroke-width', '4');
  path.setAttribute('stroke-linecap', 'round');
  path.setAttribute('stroke-linejoin', 'round');
  svg.appendChild(path);
}

function fail(message) {
  $('tradeDate').textContent = '資料待更新';
  $('mood').textContent = '市場氣氛：待更新';
  $('observation').textContent = message || '資料尚未通過核對，暫不顯示數字。';
  renderAxis(0);
  renderChart(0, []);
}

async function main() {
  try {
    const res = await fetch(`data/market-latest.json?ts=${Date.now()}`);
    const data = await res.json();
    if (data.status !== 'ok' || !data.validation?.passed) return fail('資料尚未通過核對，暫不顯示數字。');
    const idx = data.index;
    const inst = data.institutional;
    const fut = data.futures;
    $('tradeDate').textContent = `${data.tradeDate} 收盤`;
    $('closeValue').textContent = nf2.format(idx.close);
    $('closeValue').className = 'index-value';
    setClass($('pointChange'), idx.change);
    setClass($('pctChange'), idx.change);
    $('pointChange').textContent = `${idx.change >= 0 ? '▲' : '▼'} ${signed(idx.change, 2)} 點`;
    $('pctChange').textContent = `${signed(idx.changePct, 2)}%`;
    $('mood').textContent = idx.change >= 0 ? '市場氣氛：偏多觀察' : '市場氣氛：偏空觀察';
    renderAxis(idx.previousClose);
    renderChart(idx.previousClose, idx.intraday);
    setMoney('foreignNet', inst.foreign);
    setMoney('trustNet', inst.trust);
    setMoney('dealerNet', inst.dealer);
    setMoney('instTotal', inst.total, '合計 ');
    if (fut?.status === 'ok') {
      const longEl = $('futureLong');
      const shortEl = $('futureShort');
      const netEl = $('futureNet');
      longEl.textContent = `${nf.format(fut.long)} 口`;
      shortEl.textContent = `${nf.format(fut.short)} 口`;
      setClass(longEl, 1);
      setClass(shortEl, -1);
      setClass(netEl, fut.net);
      const diffWord = fut.netDiff < 0 ? '空單增加' : '空單減少';
      netEl.textContent = `淨部位 ${nf.format(fut.net)} 口（較昨日${diffWord} ${nf.format(Math.abs(fut.netDiff))} 口）`;
    }
    $('observation').textContent = idx.change < 0
      ? '今日指數走弱，先看法人賣壓是否收斂；若期貨空單同步增加，短線仍以風險控管為主。'
      : '今日指數收紅，先看法人買盤是否延續；若期貨淨部位同步轉強，短線氣氛偏多。';
    $('sourceLine').textContent = `資料日：${data.tradeDate}｜資料已通過自動核對。`;
  } catch (error) {
    fail('資料讀取失敗，暫不顯示數字。');
  }
}

main();

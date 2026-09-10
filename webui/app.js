"use strict";
/* ================= 工具 ================= */
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function fmtBytes(n){
  n = Number(n) || 0;
  if(n >= 1048576) return (n/1048576).toFixed(1) + ' MB';
  if(n >= 1024) return (n/1024).toFixed(1) + ' KB';
  return Math.round(n) + ' B';
}
function fmtHours(s){
  if(s <= 0) return '已过期';
  return (s/3600).toFixed(1) + 'h';
}
function fmtDate(ts){
  if(!ts) return '—';
  const d = new Date(ts*1000);
  const p = x => String(x).padStart(2,'0');
  return `${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
const RING_LEN = 326.7;

/* ================= 桥接 ================= */
const hasBridge = () => !!(window.pywebview && window.pywebview.api);
const call = (name, ...args) => hasBridge()
  ? window.pywebview.api[name](...args).catch(e => App.log(`${name} 调用异常: ${e}`, 'err'))
  : Promise.resolve(null);

/* ================= 全局 App ================= */
let errCount = 0;
const App = {
  state: null,

  /* ---- Python → JS：日志 ---- */
  log(msg, tag){
    const cls = tag === 'err' ? 'lerr' : tag === 'ok' ? 'lok' : tag === 'warn' ? 'lwarn' : 'ldim';
    const t = new Date().toTimeString().slice(0,8);
    const line = `<div><span class="lt">[${t}]</span> <span class="${cls}">${esc(msg)}</span></div>`;
    for(const el of [$('drawerLog'), $('logPage')]){
      el.insertAdjacentHTML('beforeend', line);
      el.scrollTop = el.scrollHeight;
    }
    const plain = String(msg);
    $('drawerHint').textContent = '最近：' + plain.slice(0, 28);
    if(tag === 'err'){
      $('errDot').classList.add('show');
      if(!$('page-logs').classList.contains('show')){
        errCount++;
        const b = $('logBadge');
        b.style.display = 'grid'; b.textContent = errCount;
      }
    }
  },

  /* ---- Python → JS：忙碌态 ---- */
  setBusy(on, label){
    const btn = $('btnSwitch');
    btn.classList.toggle('loading', !!on);
    btn.disabled = !!on;
    $('btnSwitchTxt').textContent = on ? (label || '处理中…') : '立即换号';
    for(const id of ['qRefresh','qTopup','qClean']) $(id).disabled = !!on;
  },

  /* ---- Python → JS：状态快照 ---- */
  setState(st){
    this.state = st;
    /* 横幅 */
    const beacon = $('beacon');
    const linkTxt = !st.engine_running ? '未运行'
      : st.link ? (st.link.ok ? '链路正常' : '链路异常') : '运行中';
    beacon.className = 'beacon' + (!st.engine_running ? ' off'
      : st.link ? (st.link.ok ? '' : ' err') : ' warn');
    $('curLabel').textContent = st.active ? `当前账号 · ${linkTxt}` : `无在用账号 · ${linkTxt}`;
    $('curMail').textContent = st.active || '—';
    $('curIp').textContent = st.link && st.link.ip
      ? `${st.link.ip} · 检查于 ${st.link.ts || '?'}`
      : (st.engine_running ? `端口 ${st.port || '?'}` : '');

    /* 流量环 */
    const tr = st.traffic;
    if(tr && tr.total > 0){
      const pct = Math.max(0, Math.min(1, tr.remain / tr.total));
      $('ringBar').style.strokeDashoffset = (RING_LEN * (1 - pct)).toFixed(1);
      $('ringPct').innerHTML = Math.round(pct*100) + '<small>%</small>';
      $('trafficBig').innerHTML = esc(fmtBytes(tr.remain)).replace(' ', ' <small>') + '</small>';
      $('trafficSub').textContent = `已用 ${fmtBytes(tr.used)} / ${fmtBytes(tr.total)}`;
      $('trafficBar').style.width = (pct*100).toFixed(1) + '%';
      $('trafficBig').style.color = tr.low ? 'var(--warn)' : '';
    } else {
      $('ringBar').style.strokeDashoffset = RING_LEN;
      $('ringPct').textContent = '–';
      $('trafficBig').textContent = '–';
      $('trafficSub').textContent = tr && tr.err ? tr.err : '等待数据…';
      $('trafficBar').style.width = '0';
    }

    /* 续航预测 */
    const fuel = $('fuelLine');
    if(st.fuel_days != null){
      fuel.innerHTML = `🔋 照近 3 天烧法，还能撑 <b>${esc(st.fuel_days)} 天</b>`;
    } else if(tr && tr.total > 0){
      fuel.textContent = '🔋 续航预测：数据积累中…';
    } else {
      fuel.textContent = '';
    }

    /* 流量热力图 */
    this.renderHeat(st.traffic_heatmap);

    /* 有效期（秒级跳变由本地 tick 接管） */
    this._expireTs = st.expire_ts || 0;
    this._expiryThreshold = st.expiry_threshold_s || 1800;
    this._tickTtl();

    /* 概况 */
    $('statAcc').textContent = `${st.valid_count} / ${st.accounts.length}`;
    $('statAuto').textContent = st.auto_on ? '已开启' : '已关闭';
    $('statAuto').className = 'v ' + (st.auto_on ? 'ok' : 'off');
    $('statProxy').textContent = st.sysproxy_on ? '已开启' : '已关闭';
    $('statProxy').className = 'v ' + (st.sysproxy_on ? 'ok' : 'off');
    $('statPort').textContent = st.port || '–';
    $('footText').textContent = st.engine_running ? '监控运行中' : '代理未运行';

    /* 开关同步（不触发 change） */
    $('swAuto').checked = !!st.auto_on;
    $('swProxy').checked = !!st.sysproxy_on;

    this.renderTable(st.accounts, st.active);
  },

  _tickTtl(){
    const el = $('ttlBig');
    if(!this._expireTs){
      el.textContent = '–'; el.className = 'ttl-big';
      $('ttlSub').textContent = '等待数据…';
      $('ttlWarn').style.display = 'none';
      return;
    }
    const left = this._expireTs - Date.now()/1000;
    if(left <= 0){
      el.textContent = '已过期'; el.className = 'ttl-big err';
      $('ttlSub').textContent = '';
      $('ttlWarn').style.display = 'none';
      return;
    }
    const warn = left < this._expiryThreshold;
    el.innerHTML = (left/3600).toFixed(1) + ' <small>小时</small>';
    el.className = 'ttl-big' + (warn ? ' warn' : '');
    const d = new Date(this._expireTs*1000);
    const p = x => String(x).padStart(2,'0');
    $('ttlSub').textContent = `到期 ${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    $('ttlWarn').style.display = warn ? 'flex' : 'none';
  },

  /* ---- 流量热力图（7 天 × 24 小时，亮度 = log 强度） ---- */
  renderHeat(series){
    const grid = $('heatGrid');
    if(!grid) return;
    const map = {};
    let max = 0;
    (series || []).forEach(([k, v]) => { map[k] = v; if(v > max) max = v; });
    const p = x => String(x).padStart(2, '0');
    const now = new Date();
    /* 从当前主题变量取 accent 色，换成 rgba 以承载强度 alpha */
    const acc = getComputedStyle(document.documentElement)
      .getPropertyValue('--accent').trim();
    const m = acc.match(/^#([0-9a-f]{6})$/i);
    const rgb = m
      ? [parseInt(m[1].slice(0,2),16), parseInt(m[1].slice(2,4),16), parseInt(m[1].slice(4,6),16)]
      : [77, 141, 255];
    let html = '<div class="heat-row"><span class="hd"></span>';
    for(let h = 0; h < 24; h++)
      html += `<span class="ht">${h % 6 === 0 ? h : ''}</span>`;
    html += '</div>';
    let weekTotal = 0;
    for(let i = 0; i < 7; i++){
      const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
      const day = `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
      const label = i === 0 ? '今天' : i === 1 ? '昨天'
        : `${p(d.getMonth()+1)}-${p(d.getDate())}`;
      html += `<div class="heat-row"><span class="hd">${label}</span>`;
      for(let h = 0; h < 24; h++){
        const v = map[`${day}T${p(h)}`] || 0;
        weekTotal += v;
        let style = '';
        if(v > 0 && max > 0){
          const a = 0.18 + 0.82 * (Math.log10(1 + v) / Math.log10(1 + max));
          style = ` style="background:rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a.toFixed(2)})"`;
        }
        const tip = `${label} ${p(h)}:00 · ${v > 0 ? fmtBytes(v) : '无消耗'}`;
        html += `<span class="heat-cell" title="${tip}"${style}></span>`;
      }
      html += '</div>';
    }
    grid.innerHTML = html;
    $('heatTotal').textContent = weekTotal > 0
      ? `· 7 天共 ${fmtBytes(weekTotal)}` : '· 暂无数据';
  },

  /* ---- 账号表 ---- */
  renderTable(accounts, active){
    const body = $('tblBody');
    $('tblEmpty').style.display = accounts.length ? 'none' : 'block';
    body.innerHTML = accounts.map((a, i) => {
      const capCls = a.status === 'active' ? 'on'
        : a.status === 'ready' ? (a.valid ? 'idle' : 'warn')
        : 'bad';
      const capTxt = a.status === 'active' ? '在用'
        : a.status === 'ready' ? '备用'
        : a.status === 'expired' ? '过期' : '失效';
      const pct = Math.max(0, Math.min(100, a.remain_pct));
      const low = pct < 25;
      const canSwitch = a.valid && a.status !== 'active';
      return `<div class="tbl-row ${a.email === active ? 'sel' : ''}">
        <span class="idx">${String(i+1).padStart(2,'0')}</span>
        <span class="mail">${esc(a.email)}</span>
        <span><span class="capsule ${capCls}">${capTxt}</span></span>
        <span class="minibar"><span class="bar"><i class="${low?'low':''}" style="width:${pct}%"></i></span><span class="t">${fmtHours(a.remain_s)}</span></span>
        <span class="created">${fmtDate(a.created_at)}</span>
        <span class="ops">
          ${canSwitch ? `<button class="opbtn" onclick="App.switchTo('${esc(a.email)}')">切换</button>` : ''}
          ${a.status !== 'active' ? `<button class="opbtn del" onclick="App.askDelete('${esc(a.email)}')">删除</button>` : ''}
        </span>
      </div>`;
    }).join('');
  },

  /* ---- JS → Python：操作 ---- */
  switchNow(){ if(!this.state || !this.state.switching) call('switch_now'); },
  switchTo(email){ call('switch_to', email); },
  topup(){ call('topup'); },
  refresh(){ call('refresh'); },
  askDelete(email){
    showModal('确认删除', `确定从账号库删除 ${email}？此操作不可恢复。`, () => {
      call('delete_account', email);
    });
  },
  cleanInactive(){
    showModal('清理失效账号', '将删除所有过期 / 失效账号，确定继续？', () => {
      call('delete_inactive');
    });
  },
  setAuto(on){ call('set_auto', on); },
  setSysproxy(on){ call('set_sysproxy', on); },
  fillSettings(s){
    $('inMinTraffic').value = s.min_traffic_mb ?? '';
    $('inExpiryMin').value = s.expiry_threshold_minutes ?? '';
    $('inLifetimeH').value = s.account_lifetime_hours ?? '';
    $('inReserve').value = s.reserve_accounts ?? '';
    $('inPort').value = s.proxy_port ?? '';
    $('inFeishuHook').value = s.feishu_webhook || '';
    $('inFeishuApp').value = s.feishu_app || '';
    $('inDailyTime').value = s.daily_report_time || '';
  },
  saveSettings(){
    const payload = {
      min_traffic_mb: $('inMinTraffic').value,
      expiry_threshold_minutes: $('inExpiryMin').value,
      account_lifetime_hours: $('inLifetimeH').value,
      reserve_accounts: $('inReserve').value,
      proxy_port: $('inPort').value,
      feishu_webhook: $('inFeishuHook').value.trim(),
      feishu_app: $('inFeishuApp').value.trim(),
      daily_report_time: $('inDailyTime').value.trim(),
    };
    if(!hasBridge()){ App.log('演示模式：设置未保存', 'warn'); return; }
    window.pywebview.api.save_settings(payload).then(r => {
      if(r && r.ok) App.log('设置已保存', 'ok');
      else App.log('设置格式错误: ' + (r && r.error || '未知'), 'err');
    });
  },
};
window.App = App;

/* ================= 弹窗 ================= */
let modalCb = null;
function showModal(title, desc, cb){
  $('modalTitle').textContent = title;
  $('modalDesc').textContent = desc;
  modalCb = cb;
  $('modalMask').classList.add('show');
}
$('modalCancel').onclick = () => $('modalMask').classList.remove('show');
$('modalMask').addEventListener('click', e => { if(e.target === $('modalMask')) $('modalMask').classList.remove('show'); });
$('modalOk').onclick = () => {
  $('modalMask').classList.remove('show');
  if(modalCb) modalCb();
};

/* ================= 导航 / 抽屉 ================= */
document.querySelectorAll('.nav-item').forEach(n => n.addEventListener('click', () => {
  document.querySelectorAll('.nav-item').forEach(x => x.classList.remove('active'));
  n.classList.add('active');
  document.querySelectorAll('.page').forEach(p => p.classList.remove('show'));
  $('page-' + n.dataset.page).classList.add('show');
  if(n.dataset.page === 'logs'){
    errCount = 0;
    $('logBadge').style.display = 'none';
    $('errDot').classList.remove('show');
  }
}));
$('drawerTab').addEventListener('click', () => {
  $('drawer').classList.toggle('open');
  if($('drawer').classList.contains('open')) $('errDot').classList.remove('show');
});

/* ================= 事件绑定 ================= */
$('btnSwitch').onclick = () => App.switchNow();
$('qRefresh').onclick = () => App.refresh();
$('qTopup').onclick = () => App.topup();
$('qClean').onclick = () => App.cleanInactive();
$('swAuto').onchange = e => App.setAuto(e.target.checked);
$('swProxy').onchange = e => App.setSysproxy(e.target.checked);
$('btnSaveSettings').onclick = () => App.saveSettings();

/* ===== 主题切换 ===== */
const THEMES = [
  {id:'aurora',  name:'极光蓝紫', a:'#4d8dff', b:'#38c6ff'},
  {id:'violet',  name:'星河紫',   a:'#8b5cf6', b:'#c084fc'},
  {id:'emerald', name:'翠松绿',   a:'#1fbf85', b:'#4ade80'},
  {id:'amber',   name:'熔岩橙',   a:'#f59e0b', b:'#fb923c'},
  {id:'rose',    name:'蔷薇粉',   a:'#e85d9e', b:'#ff7ab8'},
  {id:'slate',   name:'石墨灰',   a:'#8b96a8', b:'#c3ccd9'},
];
function applyTheme(id, save){
  document.documentElement.dataset.theme = id;
  if(save !== false) localStorage.setItem('ui_theme', id);
  document.querySelectorAll('.swatch').forEach(el =>
    el.classList.toggle('on', el.dataset.id === id));
  /* 热力图格子颜色是内联 rgba（取自 accent），换肤后需重绘 */
  if(window.App && App.state) App.renderHeat(App.state.traffic_heatmap);
}
function applyMode(mode, save){
  document.documentElement.dataset.mode = mode;
  if(save !== false) localStorage.setItem('ui_mode', mode);
  document.querySelectorAll('.mode-btn').forEach(el =>
    el.classList.toggle('on', el.dataset.mode === mode));
}
document.querySelectorAll('.mode-btn').forEach(el =>
  el.onclick = () => applyMode(el.dataset.mode));
applyMode(document.documentElement.dataset.mode || 'dark', false);
(function buildThemeRow(){
  const row = $('themeRow');
  if(!row) return;
  THEMES.forEach(t => {
    const el = document.createElement('div');
    el.className = 'swatch'; el.dataset.id = t.id;
    el.style.setProperty('--sa', t.a);
    el.style.setProperty('--sb', t.b);
    el.style.setProperty('--sg', t.a + '66');
    el.innerHTML = '<span class="dot"></span>' + t.name;
    el.onclick = () => applyTheme(t.id);
    row.appendChild(el);
  });
  applyTheme(document.documentElement.dataset.theme || 'aurora', false);
})();

/* 有效期每秒跳动 */
setInterval(() => App._tickTtl(), 1000);

/* ================= 启动 ================= */
window.addEventListener('pywebviewready', () => {
  call('refresh');
  window.pywebview.api.get_settings().then(s => { if(s) App.fillSettings(s); });
});

/* 浏览器预览回退（无 Python 桥时的演示数据） */
setTimeout(() => {
  if(hasBridge()) return;
  /* 演示热力数据：白天高、凌晨低的作息曲线 + 大前天凌晨一次异常尖峰 */
  const demoHeat = [];
  const p2 = x => String(x).padStart(2, '0');
  for(let i = 6; i >= 0; i--){
    const d = new Date(Date.now() - i * 86400000);
    for(let h = 0; h < 24; h++){
      const base = Math.max(0, Math.sin((h - 6) / 24 * Math.PI * 2)) * 40;
      const spike = (i === 3 && h === 3) ? 260 : 0;
      const v = (base + spike) * (0.5 + ((i * 7 + h * 13) % 10) / 10) * 1048576;
      if(v > 2 * 1048576)
        demoHeat.push([`${d.getFullYear()}-${p2(d.getMonth()+1)}-${p2(d.getDate())}T${p2(h)}`, Math.round(v)]);
    }
  }
  App.setState({
    active: 'dp0024yd83@emalupe.com', engine_running: true, port: 10808,
    link: {ok: true, ip: '103.170.233.101', ts: '08:53:12'},
    traffic: {total: 307.2*1048576, used: 205.4*1048576, remain: 101.8*1048576, low: false},
    traffic_heatmap: demoHeat, fuel_days: 3.2,
    expire_ts: Date.now()/1000 + 14.8*3600, expiry_threshold_s: 1800,
    auto_on: true, sysproxy_on: true, valid_count: 3, switching: false,
    accounts: [
      {email:'dp0024yd83@emalupe.com', status:'active', valid:true, remain_s:15.8*3600, remain_pct:66, created_at:Date.now()/1000-8.2*3600},
      {email:'8f9zlo3bo0@emalupe.com', status:'ready', valid:true, remain_s:16.2*3600, remain_pct:72, created_at:Date.now()/1000-7.8*3600},
      {email:'vs8tik474x@emalupe.com', status:'ready', valid:true, remain_s:5.2*3600, remain_pct:22, created_at:Date.now()/1000-18.8*3600},
      {email:'dead00beef@emalupe.com', status:'banned', valid:false, remain_s:0, remain_pct:0, created_at:Date.now()/1000-30*3600},
    ],
  });
  App.log('演示模式：未连接 Python 后端', 'warn');
  App.log('监控心跳: 第 452 轮，有效账号 3', '');
}, 300);

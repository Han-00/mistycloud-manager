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
const EV_EMPTY = '<div class="ev-empty">还没有事件记录。换号、注册、链路回退与自愈都会记在这里。</div>';

/* ================= 桥接 ================= */
const hasBridge = () => !!(window.pywebview && window.pywebview.api);
const call = (name, ...args) => hasBridge()
  ? window.pywebview.api[name](...args).catch(e => App.log(`${name} 调用异常: ${e}`, 'err'))
  : Promise.resolve(null);

/* ================= 全局 App ================= */
let errCount = 0;
const App = {
  state: null,
  _logTab: 'run',        /* 日志页当前标签：run(运行日志) / events(事件) */

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

  /* ---- Python → JS：浮窗提示（对应玻璃版的 _toast）---- */
  toast(msg, ok = true){
    const box = $('toasts');
    if(!box) return;
    const el = document.createElement('div');
    el.className = 'toast' + (ok ? '' : ' err');
    el.innerHTML = `<span class="ticon">${ok ? '✓' : '✗'}</span><span>${esc(msg)}</span>`;
    box.appendChild(el);
    /* 连点时不刷屏：只留最近 3 条 */
    while(box.children.length > 3) box.removeChild(box.firstChild);
    setTimeout(() => {
      el.classList.add('out');
      el.addEventListener('animationend', () => el.remove(), { once: true });
      setTimeout(() => el.remove(), 600);   // 动画被打断时的兜底
    }, 4000);
  },

  /* ---- 清空日志（抽屉与日志页是两份 DOM，必须一起清）---- */
  clearLog(){
    for(const el of [$('drawerLog'), $('logPage')]) el.innerHTML = '';
    $('drawerHint').textContent = '暂无日志';
    $('errDot').classList.remove('show');
    errCount = 0;
    $('logBadge').style.display = 'none';
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

    /* 链路可用率 */
    this.renderUptime(st.link_uptime);

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

  /* ---- 链路可用率（近 24h） ---- */
  renderUptime(u){
    const pctEl = $('uptimePct'), barEl = $('uptimeBar'), noteEl = $('uptimeNote');
    if(!pctEl) return;
    const pct = u ? u.pct : null;
    if(pct == null){
      /* 「没测过」必须和「全挂」分开显示：程序刚装上时是没数据，
         直接甩个 0% 会让人以为链路是坏的。 */
      pctEl.textContent = '数据积累中';
      pctEl.className = 'uptime-pct na';
      barEl.style.width = '0';
      barEl.className = '';
      noteEl.textContent = '';
      return;
    }
    pctEl.innerHTML = Math.round(pct) + '<small>%</small>';
    const cls = pct >= 95 ? '' : pct >= 80 ? 'warn' : 'bad';
    pctEl.className = ('uptime-pct ' + cls).trim();
    barEl.style.width = pct + '%';
    barEl.className = cls;
    noteEl.textContent = `${u.ok}/${u.total} 次探测`;
  },

  /* ---- 事件流（换号 / 注册 / 回退 / 自愈的留痕） ---- */
  switchLogTab(tab){
    this._logTab = tab;
    for(const b of document.querySelectorAll('#logTabs .log-tab'))
      b.classList.toggle('active', b.dataset.tab === tab);
    $('logPage').style.display   = tab === 'run'    ? '' : 'none';
    $('eventPage').style.display = tab === 'events' ? '' : 'none';
    if(tab === 'events') this.loadEvents();
  },

  loadEvents(){
    if(!hasBridge()){ this.renderEvents(DEMO_EVENTS); return; }
    call('get_events', 200).then(list => this.renderEvents(list || []));
  },

  renderEvents(list){
    const box = $('eventPage');
    if(!box) return;
    if(!list.length){ box.innerHTML = EV_EMPTY; return; }
    const KIND = {switch:'换号', register:'注册', restore:'回退', heal:'自愈', abort:'中止'};
    box.innerHTML = list.map(e => {
      const kind = e.kind || '?';
      const label = KIND[kind] || kind;
      const dt = String(e.time || '');
      const parts = [];
      if(e.email) parts.push(`<span class="ev-mail">${esc(e.email)}</span>`);
      if(e.reason) parts.push(`<span class="ev-meta">${esc(e.reason)}</span>`);
      if(e.ok && e.proxy_ip) parts.push(`<span class="ev-meta">→ ${esc(e.proxy_ip)}</span>`);
      if(e.duration) parts.push(`<span class="ev-meta">${esc(e.duration)}s</span>`);
      if(!e.ok && e.error) parts.push(`<span class="ev-fail">${esc(e.error)}</span>`);
      return `<div class="ev">
        <span class="ev-time">${esc(dt.slice(5))}</span>
        <span class="ev-badge k-${esc(kind)}">${esc(label)}</span>
        <span class="ev-badge" style="color:${e.ok ? 'var(--con-text)' : '#ff8b84'}">${e.ok ? '成功' : '失败'}</span>
        ${parts.join(' ')}
      </div>`;
    }).join('');
  },

  clearEvents(){
    if(hasBridge()) call('clear_events');
    $('eventPage').innerHTML = EV_EMPTY;
  },

  /* ---- 流量热力图（7 天 × 24 小时，4 档离散色阶，固定档界） ---- */
  renderHeat(series){
    const grid = $('heatGrid');
    if(!grid) return;
    const map = {};
    (series || []).forEach(([k, v]) => { map[k] = v; });
    const p = x => String(x).padStart(2, '0');
    const now = new Date();
    /* 4 档色板从 CSS 变量取（与图例同源），缺省用内置热力色带 */
    const cs = getComputedStyle(document.documentElement);
    const fallback = ['#60a5fa', '#facc15', '#fb923c', '#ef4444'];
    const levels = fallback.map((fb, i) =>
      cs.getPropertyValue(`--heat-${i + 1}`).trim() || fb);
    /* 固定绝对档界（用户拍板）：<50 蓝 / 50-200 黄 / 200-500 橙 / 500+ 红 */
    const MB = 1048576;
    const cuts = [50 * MB, 200 * MB, 500 * MB];
    const days = [];
    for(let i = 0; i < 7; i++){
      const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
      days.push(`${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`);
    }
    let html = '<div class="heat-row"><span class="hd"></span>';
    for(let h = 0; h < 24; h++)
      html += `<span class="ht">${h % 6 === 0 ? h : ''}</span>`;
    html += '</div>';
    let weekTotal = 0;
    for(let i = 0; i < 7; i++){
      const day = days[i];
      const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
      const label = i === 0 ? '今天' : i === 1 ? '昨天'
        : `${p(d.getMonth()+1)}-${p(d.getDate())}`;
      html += `<div class="heat-row"><span class="hd">${label}</span>`;
      for(let h = 0; h < 24; h++){
        const v = map[`${day}T${p(h)}`] || 0;
        weekTotal += v;
        let style = '';
        if(v > 0){
          /* 越过几条档界就是第几档（1-4） */
          const lv = 1 + cuts.filter(c => v >= c).length;
          style = ` style="background:${levels[lv - 1]}"`;
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
  /* 状态胶囊文案，表格与详情弹窗共用一份，避免两处各写一套后漂移 */
  statusInfo(a){
    if(a.status === 'active') return { cls: 'on',   txt: '在用' };
    if(a.status === 'ready')  return { cls: a.valid ? 'idle' : 'warn',
                                       txt: a.valid ? '备用' : '备用·不可用' };
    if(a.status === 'expired') return { cls: 'bad', txt: '过期' };
    return { cls: 'bad', txt: '失效' };
  },

  renderTable(accounts, active){
    const body = $('tblBody');
    $('tblEmpty').style.display = accounts.length ? 'none' : 'block';
    body.innerHTML = accounts.map((a, i) => {
      const st = this.statusInfo(a);
      const pct = Math.max(0, Math.min(100, a.remain_pct));
      const low = pct < 25;
      const canSwitch = a.valid && a.status !== 'active';
      return `<div class="tbl-row ${a.email === active ? 'sel' : ''}"
        oncontextmenu="return App.rowMenu(event, '${esc(a.email)}')">
        <span class="idx">${String(i+1).padStart(2,'0')}</span>
        <span class="mail">${esc(a.email)}</span>
        <span><span class="capsule ${st.cls}">${st.txt}</span></span>
        <span class="minibar"><span class="bar"><i class="${low?'low':''}" style="width:${pct}%"></i></span><span class="t">${fmtHours(a.remain_s)}</span></span>
        <span class="created">${fmtDate(a.created_at)}</span>
        <span class="ops">
          ${canSwitch ? `<button class="opbtn" onclick="App.switchTo('${esc(a.email)}')">切换</button>` : ''}
          ${a.status !== 'active' ? `<button class="opbtn del" onclick="App.askDelete('${esc(a.email)}')">删除</button>` : ''}
        </span>
      </div>`;
    }).join('');
  },

  /* ---- 右键菜单（对应玻璃版的 _row_menu）---- */
  rowMenu(e, email){
    e.preventDefault();
    if(this.state && this.state.switching) return false;   // 换号中不提供操作入口
    this.showCtx(e.clientX, e.clientY, email);
    return false;
  },

  showCtx(x, y, email){
    const m = $('ctxMenu');
    const a = (this.state && this.state.accounts || [])
      .find(v => v.email === email) || {};
    const canSwitch = a.valid && a.status !== 'active';
    const canDelete = a.status !== 'active';
    m.innerHTML =
      `<div class="ctx-item" data-act="copy">复制邮箱</div>
       <div class="ctx-item" data-act="info">查看详情</div>` +
      (canSwitch ? `<div class="ctx-item" data-act="switch">切换到该账号</div>` : '') +
      (canDelete ? `<div class="ctx-sep"></div>
                    <div class="ctx-item danger" data-act="del">删除该账号</div>` : '');
    m.querySelectorAll('.ctx-item').forEach(el => el.onclick = () => {
      const act = el.dataset.act;
      this.hideCtx();
      if(act === 'copy') this.copyEmail(email);
      else if(act === 'info') this.showInfo(email);
      else if(act === 'switch') this.switchTo(email);
      else if(act === 'del') this.askDelete(email);
    });
    /* 先显示再量尺寸，否则 getBoundingClientRect 拿不到宽高 */
    m.classList.add('show');
    const r = m.getBoundingClientRect();
    m.style.left = Math.max(8, Math.min(x, window.innerWidth  - r.width  - 8)) + 'px';
    m.style.top  = Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) + 'px';
  },

  hideCtx(){ $('ctxMenu').classList.remove('show'); },

  /* ---- 复制邮箱：剪贴板 API 在 WebView 里可能被拒，需要 execCommand 兜底 ---- */
  copyEmail(email){
    const done = () => this.toast(`已复制 ${email}`);
    const fallback = () => {
      try{
        const ta = document.createElement('textarea');
        ta.value = email;
        ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
        done();
      }catch(_e){ this.toast('复制失败，请手动选择', false); }
    };
    if(navigator.clipboard && navigator.clipboard.writeText)
      navigator.clipboard.writeText(email).then(done).catch(fallback);
    else fallback();
  },

  /* ---- 账号详情（数据全来自 state，无需再问后端）---- */
  showInfo(email){
    const a = (this.state && this.state.accounts || [])
      .find(v => v.email === email);
    if(!a){ this.toast(`账号 ${email} 已不在库中`, false); return; }
    const st = this.statusInfo(a);
    const rows = [
      ['邮箱',         a.email],
      ['状态',         st.txt],
      ['当前可用',     a.valid ? '是' : '否'],
      ['注册于',       fmtDate(a.created_at)],
      ['生命周期剩余', fmtHours(a.remain_s)],
      ['剩余比例',     a.remain_pct + '%'],
    ];
    $('infoBody').innerHTML = rows.map(([k, v]) =>
      `<div class="info-row"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>`
    ).join('');
    $('infoCopy').onclick = () => this.copyEmail(a.email);
    $('infoMask').classList.add('show');
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

  /* 开机自启：写注册表，失败必须把开关回滚——否则界面在骗人 */
  setAutostart(on){
    const revert = () => { $('swAutostart').checked = !on; };
    if(!hasBridge()){
      revert();
      this.toast('演示模式：未连接后端', false);
      return;
    }
    window.pywebview.api.set_autostart(on).then(r => {
      if(r && r.ok) this.toast(`开机自启已${on ? '开启' : '关闭'}`);
      else { revert(); this.toast('开机自启设置失败（注册表访问受限）', false); }
    }).catch(() => {
      revert();
      this.toast('开机自启设置失败', false);
    });
  },

  fillSettings(s){
    $('inMinTraffic').value = s.min_traffic_mb ?? '';
    $('inExpiryMin').value = s.expiry_threshold_minutes ?? '';
    $('inLifetimeH').value = s.account_lifetime_hours ?? '';
    $('inReserve').value = s.reserve_accounts ?? '';
    $('inPort').value = s.proxy_port ?? '';
    $('inFeishuHook').value = s.feishu_webhook || '';
    $('inFeishuApp').value = s.feishu_app || '';
    $('inDailyTime').value = s.daily_report_time || '';
    $('swAutostart').checked = !!s.autostart;
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
    /* 后端 save_settings 已写「设置已保存」日志，这里只弹回执，别重复记一遍 */
    window.pywebview.api.save_settings(payload).then(r => {
      if(r && r.ok) App.toast('设置已保存');
      else App.toast('设置格式错误: ' + (r && r.error || '未知'), false);
    }).catch(() => App.toast('设置保存失败', false));
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

/* 账号详情弹窗 */
$('infoClose').onclick = () => $('infoMask').classList.remove('show');
$('infoMask').addEventListener('click', e => { if(e.target === $('infoMask')) $('infoMask').classList.remove('show'); });

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
    /* 事件是历史数据，不会像日志那样被推送更新，进来时重新拉一次 */
    if(App._logTab === 'events') App.loadEvents();
  }
}));
$('drawerTab').addEventListener('click', () => {
  $('drawer').classList.toggle('open');
  if($('drawer').classList.contains('open')) $('errDot').classList.remove('show');
});
/* 「清空」在抽屉标题栏内，必须拦住冒泡，否则点它会把抽屉一起收起来 */
$('btnClearLogDrawer').addEventListener('click', e => {
  e.stopPropagation();
  App.clearLog();
});

/* ================= 事件绑定 ================= */
$('btnSwitch').onclick = () => App.switchNow();
$('qRefresh').onclick = () => App.refresh();
$('qTopup').onclick = () => App.topup();
$('qClean').onclick = () => App.cleanInactive();
/* 「清空」作用于当前标签：事件标签下清事件流，否则清运行日志 */
$('btnClearLogPage').onclick = () =>
  App._logTab === 'events' ? App.clearEvents() : App.clearLog();
for(const b of document.querySelectorAll('#logTabs .log-tab'))
  b.onclick = () => App.switchLogTab(b.dataset.tab);
$('swAuto').onchange = e => App.setAuto(e.target.checked);
$('swProxy').onchange = e => App.setSysproxy(e.target.checked);
$('swAutostart').onchange = e => App.setAutostart(e.target.checked);
$('btnSaveSettings').onclick = () => App.saveSettings();

/* ================= 快捷键（对应玻璃版的 F5 / Ctrl+T） ================= */
document.addEventListener('keydown', e => {
  /* 正在输入框里打字时不抢键：否则改设置时按 Ctrl+T 会莫名其妙换号 */
  const tag = (e.target.tagName || '').toLowerCase();
  if(tag === 'input' || tag === 'textarea' || e.target.isContentEditable) return;
  if(e.key === 'F5'){                       // F5 默认会重载页面，必须拦掉
    e.preventDefault();
    App.refresh();
  } else if(e.ctrlKey && (e.key === 't' || e.key === 'T')){
    e.preventDefault();
    App.switchNow();
  } else if(e.key === 'Escape'){
    App.hideCtx();
    for(const id of ['modalMask', 'infoMask']) $(id).classList.remove('show');
  }
});

/* 点空白处 / 滚动时收起右键菜单 */
document.addEventListener('click', () => App.hideCtx());
document.addEventListener('contextmenu', e => {
  if(!e.target.closest('.tbl-row')) App.hideCtx();
});
window.addEventListener('blur', () => App.hideCtx());

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
  /* 热力图格子颜色取自 CSS 变量，换肤后重绘以保持一致 */
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
/* 演示事件流：新的在前，与 events.recent() 的顺序一致 */
const DEMO_EVENTS = [
  {time:'2026-09-10 08:41:07', kind:'switch',   ok:true,  email:'dp0024yd83@emalupe.com', reason:'流量不足', proxy_ip:'103.170.233.101', duration:9.4},
  {time:'2026-09-10 08:22:51', kind:'register', ok:true,  email:'8f9zlo3bo0@emalupe.com'},
  {time:'2026-09-10 07:58:33', kind:'switch',   ok:false, email:'dead00beef@emalupe.com', reason:'流量不足', error:'登录失败（账号已失效）', duration:4.1},
  {time:'2026-09-10 07:58:29', kind:'restore',  ok:true,  reason:'换号失败后回退原节点'},
  {time:'2026-09-10 06:13:02', kind:'heal',     ok:true,  reason:'连续探测失败后重启引擎恢复', proxy_ip:'103.170.233.101'},
  {time:'2026-09-09 23:47:18', kind:'abort',    ok:false, reason:'流量/有效期不足', error:'备用账号不足（请检查账号库）'},
];

setTimeout(() => {
  if(hasBridge()) return;
  /* 演示热力数据：白天高、凌晨低的作息曲线 + 两次尖峰（覆盖 蓝/黄/橙/红 全部 4 档） */
  const demoHeat = [];
  const p2 = x => String(x).padStart(2, '0');
  for(let i = 6; i >= 0; i--){
    const d = new Date(Date.now() - i * 86400000);
    for(let h = 0; h < 24; h++){
      const base = Math.max(0, Math.sin((h - 6) / 24 * Math.PI * 2)) * 60;
      const spike = (i === 3 && h === 3) ? 420 : (i === 1 && h === 21) ? 1100 : 0;
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
    link_uptime: {pct: 97.2, ok: 315, total: 324},
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

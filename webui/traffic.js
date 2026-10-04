"use strict";
/* 独立分析视图；真实报告只由 Python 计算，无桥预览明确显示演示数据。 */
(() => {
  const byId = id => document.getElementById(id);
  const escapeText = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dateKey = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const dayFromKey = key => {
    const [y,m,d] = key.split('-').map(Number);
    return new Date(y, m-1, d);
  };
  const moveDay = (key, amount) => {
    const d = dayFromKey(key);
    d.setDate(d.getDate()+amount);
    return dateKey(d);
  };
  const utcDay = key => {
    const [year,month,day] = key.split('-').map(Number);
    return Date.UTC(year,month-1,day) / 86400000;
  };
  const bytes = n => {
    if(n == null || !Number.isFinite(Number(n))) return '—';
    n = Number(n);
    if(n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`;
    if(n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`;
    if(n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${Math.round(n)} B`;
  };
  const bridge = () => window.pywebview && window.pywebview.api;
  const rowStatus = row => row.baseline_only ? '仅有基线，等待增量采样' : row.bytes == null ? '无记录' : row.partial ? '今日进行中' : '有记录';
  const showPoint = event => {
    const point = event.target.closest('[data-index]');
    if(!point) return;
    const chart = byId('trafficChart');
    byId('trafficPoint').textContent = point.getAttribute('aria-label');
    const guide = chart.querySelector('.chart-guide');
    if(guide){
      guide.style.transform = `translateX(${point.dataset.x}px)`;
      guide.classList.add('show');
    }
    if(event.type === 'focusin' || event.type === 'click'){
      const previous = chart.querySelector('[data-index][tabindex="0"]');
      if(previous && previous !== point) previous.setAttribute('tabindex', '-1');
      point.setAttribute('tabindex', '0');
    }
  };
  const clearGuide = () => {
    const chart = byId('trafficChart');
    if(chart.contains(document.activeElement) && document.activeElement.matches('[data-index]')){
      showPoint({target:document.activeElement,type:'focusin'});
      return;
    }
    chart.querySelector('.chart-guide')?.classList.remove('show');
    byId('trafficPoint').textContent = '悬浮查看每日用量 · 断线表示无记录';
  };

  // 单调三次插值：穿过真实记录，峰谷切线归零，曲线不会越过相邻值。
  const smoothPath = points => {
    const slopes = points.slice(1).map((p, i) => (p.y-points[i].y)/(p.x-points[i].x));
    const tangents = points.map((p, i) => {
      if(i === 0) return slopes[0];
      if(i === points.length-1) return slopes[slopes.length-1];
      const before = slopes[i-1], after = slopes[i];
      return before*after <= 0 ? 0 : 2*before*after/(before+after);
    });
    let path = `M${points[0].x.toFixed(2)},${points[0].y.toFixed(2)}`;
    for(let i=1;i<points.length;i++){
      const before = points[i-1], after = points[i], third = (after.x-before.x)/3;
      path += ` C${(before.x+third).toFixed(2)},${(before.y+tangents[i-1]*third).toFixed(2)}`
        + ` ${(after.x-third).toFixed(2)},${(after.y-tangents[i]*third).toFixed(2)}`
        + ` ${after.x.toFixed(2)},${after.y.toFixed(2)}`;
    }
    return path;
  };

  const Traffic = {
    report: null,
    preset: '30',
    requestId: 0,
    loading: false,
    exporting: false,
    loadedAt: 0,
    opened: false,
    applyPreset(value){
      this.preset = String(value);
      const today = dateKey(new Date());
      byId('trafficEnd').value = today;
      byId('trafficStart').value = value === 'month' ? today.slice(0,8)+'01' : moveDay(today, 1-Number(value));
      this.markPreset();
    },
    markPreset(){
      byId('trafficPeriod').value = this.preset || 'custom';
    },
    open(){
      this.opened = true;
      this.load();
    },
    status(message, error=false){
      byId('trafficStatus').textContent = message;
      byId('trafficStatus').classList.toggle('error', error);
    },
    updateState(){
      // 复用客户端现有的状态更新，不另起一套轮询。
      if(this.opened && (this.preset || this.report) && byId('page-traffic').classList.contains('show') && !this.loading && !this.exporting && Date.now()-this.loadedAt > 30000){
        if(this.preset) this.applyPreset(this.preset);
        this.load();
      }
    },
    async load(){
      const today = dateKey(new Date());
      byId('trafficStart').max = today;
      byId('trafficEnd').max = today;
      const start = byId('trafficStart').value;
      const end = byId('trafficEnd').value;
      const days = utcDay(end)-utcDay(start)+1;
      if(!start || !end || start > end || end > today || !Number.isFinite(days) || days < 1 || days > 365){
        // 输入修改后旧结果失效，导出不会把新日期与旧数据混用。
        this.requestId++;
        this.loading = false;
        this.report = null;
        byId('trafficContent').hidden = true;
        this.busy();
        this.status('请选择有效日期：开始不晚于结束，不含未来，最多 365 天。', true);
        return;
      }
      const id = ++this.requestId;
      this.loading = true;
      this.report = null;
      byId('trafficContent').hidden = false;
      this.busy();
      this.status('正在读取所选日期的流量记录…');
      try{
        const api = bridge();
        const report = api ? await api.get_traffic_report(start, end) : this.demoReport(start, end);
        if(id !== this.requestId) return;
        if(!report || !report.ok) throw new Error(report && report.error || '流量历史暂不可用');
        if(!Array.isArray(report.series) || !report.summary) throw new Error('流量报告数据不完整');
        this.report = report;
        this.loadedAt = Date.now();
        this.render(report);
        const coverage = report.summary.recorded_days;
        this.status(`${api ? '' : '演示数据 · '}${report.start} 至 ${report.end} · ${coverage}/${report.days} 天有记录`);
      } catch(e){
        if(id !== this.requestId) return;
        byId('trafficContent').hidden = true;
        this.status(`${e.message || '记录读取失败'}，点击「刷新」重试。`, true);
      } finally{
        if(id === this.requestId){ this.loading = false; this.busy(); }
      }
    },
    busy(){
      byId('trafficContent').setAttribute('aria-busy', String(this.loading));
      const disabled = this.loading || this.exporting || !this.report || !this.report.summary.recorded_days;
      byId('trafficExportCsv').disabled = disabled;
      byId('trafficRefresh').disabled = this.loading;
    },
    render(report){
      const summary = report.summary;
      byId('trafficTotal').textContent = bytes(summary.total_bytes);
      byId('trafficAverage').textContent = bytes(summary.average_daily_bytes);
      byId('trafficInsights').innerHTML = (report.insights || []).map(text => `<li>${escapeText(text)}</li>`).join('');
      byId('trafficScope').textContent = report.scope || '';
      byId('trafficNotes').innerHTML = (report.notes || []).map(text => `<li>${escapeText(text)}</li>`).join('');
      byId('trafficDetailCount').textContent = `${report.days} 天`;
      byId('trafficRows').innerHTML = [...report.series].reverse().map(row => `<tr${row.bytes == null ? ' class="missing"' : ''}><td>${escapeText(row.date)}</td><td>${row.bytes == null ? '无记录' : escapeText(bytes(row.bytes))}</td><td>${row.bytes != null && summary.total_bytes > 0 ? (row.bytes/summary.total_bytes*100).toFixed(1)+'%' : '—'}</td><td>${rowStatus(row)}${row.legacy ? ' · 旧小时记录迁移' : ''}</td></tr>`).join('');
      this.renderChart(report);
    },
    renderChart(report){
      const rows = report.series;
      const recorded = rows.filter(row => row.bytes != null);
      if(!recorded.length){
        byId('trafficChart').innerHTML = '<div class="traffic-empty">这段时间还没有用量记录。<br>成功查询套餐后，新的历史会自动出现在这里。</div>';
        byId('trafficPoint').textContent = '无记录日期不会绘制成 0 用量。';
        return;
      }
      const width = Math.max(500, byId('trafficChart').clientWidth), height = byId('trafficChart').clientHeight;
      const left = 78, right = 18, top = 25, bottom = 42;
      const plotWidth = width-left-right, plotHeight = height-top-bottom;
      const highest = Math.max(...recorded.map(row => row.bytes), 1024 ** 2);
      const scale = 10 ** Math.floor(Math.log10(highest));
      const maximum = Math.ceil(highest/scale/0.5)*scale*0.5;
      const x = i => left+(rows.length === 1 ? plotWidth/2 : i/(rows.length-1)*plotWidth);
      const y = n => top+plotHeight-(n/maximum)*plotHeight;
      let svg = `<svg viewBox="0 0 ${width} ${height}" role="group" aria-labelledby="trafficSvgTitle trafficSvgDesc"><title id="trafficSvgTitle">每日记录用量趋势，${escapeText(report.start)} 至 ${escapeText(report.end)}</title><desc id="trafficSvgDesc">${report.summary.recorded_days} 天有记录。平滑曲线连接连续的每日记录，缺失日期断开；聚焦图表后用左右方向键查看每日真实数值。</desc><defs><linearGradient id="trafficAreaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity=".18"/><stop offset="100%" stop-color="var(--accent)" stop-opacity=".015"/></linearGradient></defs>`;
      for(let i=0;i<=4;i++){
        const value = maximum*i/4, at = y(value);
        svg += `<line class="chart-grid" x1="${left}" x2="${width-right}" y1="${at}" y2="${at}"/><text class="chart-label" text-anchor="end" x="${left-12}" y="${at+4}">${escapeText(bytes(value))}</text>`;
      }
      const runs = [];
      let run = [];
      rows.forEach((row,i) => {
        if(row.bytes == null){ if(run.length) runs.push(run); run = []; }
        else run.push({x:x(i),y:y(row.bytes),index:i});
      });
      if(run.length) runs.push(run);
      for(const points of runs){
        if(points.length < 2) continue;
        const line = smoothPath(points);
        svg += `<path class="chart-area" d="${line} L${points[points.length-1].x},${y(0)} L${points[0].x},${y(0)} Z"/><path class="chart-line" d="${line}"/>`;
      }
      if(!runs.some(points => points.length >= 2)){
        svg += `<text class="chart-note" text-anchor="middle" x="${left+plotWidth/2}" y="${top+plotHeight/2}">记录尚不连续，暂无可绘制的趋势</text>`;
      }
      svg += `<line class="chart-guide" x1="0" x2="0" y1="${top}" y2="${y(0)}" aria-hidden="true"/>`;
      const step = rows.length === 1 ? plotWidth : plotWidth/(rows.length-1);
      rows.forEach((row,i) => {
        const label = `${row.date} · ${row.bytes == null ? rowStatus(row) : bytes(row.bytes)}${row.bytes != null && row.partial ? ' · 今日进行中' : ''}${row.legacy ? ' · 旧小时记录迁移' : ''}`;
        const from = Math.max(left,x(i)-step/2), to = Math.min(width-right,x(i)+step/2);
        svg += `<rect class="chart-hit" data-index="${i}" data-x="${x(i)}" x="${from}" y="${top}" width="${to-from}" height="${plotHeight}" tabindex="${i===0 ? 0 : -1}" role="img" aria-label="${escapeText(label)}"/>`;
      });
      const labels = new Set([0,rows.length-1]);
      const count = Math.min(5, rows.length);
      for(let i=0;i<count;i++) labels.add(Math.round(i*Math.max(0,rows.length-1)/Math.max(1,count-1)));
      for(const i of [...labels].sort((a,b)=>a-b)){
        const anchor = rows.length===1 ? 'middle' : i===0 ? 'start' : i===rows.length-1 ? 'end' : 'middle';
        svg += `<text class="chart-label" text-anchor="${anchor}" x="${x(i)}" y="${height-12}">${escapeText(rows[i].date.slice(5))}</text>`;
      }
      byId('trafficChart').innerHTML = svg+'</svg>';
      byId('trafficPoint').textContent = '悬浮查看每日用量 · 断线表示无记录';
      byId('trafficChart').onpointerover = showPoint;
      byId('trafficChart').onpointerleave = clearGuide;
      byId('trafficChart').onclick = showPoint;
      byId('trafficChart').onkeydown = event => {
        if(!['ArrowLeft','ArrowRight'].includes(event.key)) return;
        const points = [...byId('trafficChart').querySelectorAll('[data-index]')];
        const index = points.indexOf(event.target);
        if(index < 0) return;
        event.preventDefault();
        points[Math.max(0,Math.min(points.length-1,index+(event.key==='ArrowRight'?1:-1)))].focus();
      };
    },
    async export(){
      if(!this.report || this.loading || this.exporting) return;
      const api = bridge();
      if(!api){ App.toast('演示模式：请在桌面客户端中导出真实报告', false); return; }
      const report = this.report;
      this.exporting = true;
      this.busy();
      try{
        const result = await api.export_traffic_report(report.start, report.end);
        if(!result || !result.ok) throw new Error(result && result.error || '报告导出失败');
        if(!result.cancelled) App.toast(`已保存 ${result.filename || '流量报告'}`);
      } catch(e){ App.toast(e.message || '报告导出失败', false); }
      finally{ this.exporting = false; this.busy(); }
    },
    demoReport(start,end){
      // 浏览器预览样例固定生成90天，其中一日缺失和一日实测零值。
      const today = dateKey(new Date()), historyStart = moveDay(today,-89);
      const series = [];
      for(let day=start;day<=end;day=moveDay(day,1)){
        const index = Math.round((dayFromKey(day)-dayFromKey(historyStart))/86400000);
        const known = day>=historyStart && index!==61;
        const value = known ? Math.round((index===74 ? 1900 : index===68 ? 0 : 210+(index*37)%340+Math.sin(index/4)*95)*(1024 ** 2)) : null;
        series.push({date:day, bytes:value, partial:day===today, legacy:false, samples:known?48:0});
      }
      const known = series.filter(row=>row.bytes!=null), complete = known.filter(row=>!row.partial);
      const total = known.length ? known.reduce((sum,row)=>sum+row.bytes,0) : null;
      const peak = known.length ? known.reduce((a,b)=>a.bytes>=b.bytes?a:b) : null;
      return {ok:true,start,end,today,days:series.length,history_start:historyStart,series,
        summary:{total_bytes:total,average_daily_bytes:complete.length?complete.reduce((sum,row)=>sum+row.bytes,0)/complete.length:null,average_days:complete.length,recorded_days:known.length,missing_days:series.length-known.length,peak},
        comparison:{status:'incomplete',percent:null},scope:'演示数据：仅用于预览页面，真实报告由桌面客户端计算。',
        insights:[peak ? `最高记录用量：${peak.date}，${bytes(peak.bytes)}（示例）。` : '这段时间没有记录。'],
        notes:['真实统计来自平台累计已用量的正增量，合计本机管理过的账号。','日汇总保留365天，旧版已清理的记录无法恢复。','无记录与有记录但无新增消耗分别显示；今日不参与日均。']};
    },
  };
  window.Traffic = Traffic;
  window.addEventListener('resize', () => {
    if(Traffic.report && byId('page-traffic').classList.contains('show')) Traffic.renderChart(Traffic.report);
  });
  byId('trafficChart').addEventListener('focusin', showPoint);
  byId('trafficChart').addEventListener('focusout', event => {
    if(!byId('trafficChart').contains(event.relatedTarget)){
      byId('trafficChart').querySelector('.chart-guide')?.classList.remove('show');
    }
  });
  Traffic.applyPreset('30');
  byId('trafficPeriod').onchange = event => {
    const value = event.target.value;
    byId('trafficRangeForm').hidden = value !== 'custom';
    if(value === 'custom'){ Traffic.preset=null; return; }
    Traffic.applyPreset(value);
    Traffic.load();
  };
  byId('trafficRangeForm').onsubmit = event => { event.preventDefault(); Traffic.preset=null; Traffic.markPreset(); Traffic.load(); };
  for(const id of ['trafficStart','trafficEnd']) byId(id).addEventListener('input', () => {
    Traffic.preset=null;
    Traffic.markPreset();
    Traffic.requestId++;
    Traffic.loading=false;
    Traffic.report=null;
    byId('trafficContent').hidden=true;
    Traffic.busy();
    Traffic.status('日期已修改，点击「查看日期」读取对应记录。');
  });
  byId('trafficRefresh').onclick = () => { if(Traffic.preset) Traffic.applyPreset(Traffic.preset); Traffic.load(); };
  byId('trafficExportCsv').onclick = () => Traffic.export();
  window.addEventListener('pywebviewready', () => {
    if(byId('page-traffic').classList.contains('show')) Traffic.load();
  });
})();

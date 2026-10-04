// 用 playwright-cli run-code --filename tests/_traffic_visual_check.js 执行。
// 浏览器预览 + 隔离的桥桩，不启动真实代理、不读取账号或统计文件。
async (page) => {
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const assert = (condition, message) => { if(!condition) throw new Error(message); };
  const visible = id => page.locator('#'+id).isVisible();
  await page.setViewportSize({width:1360,height:1000});
  await page.goto('http://127.0.0.1:8877/index.html');
  await page.getByRole('button',{name:'流量分析',exact:true}).click();
  await page.locator('#trafficStatus').filter({hasText:'演示数据'}).waitFor();
  await page.waitForFunction(()=>getComputedStyle(document.getElementById('page-traffic')).opacity==='1' && !!App.state && !Traffic.loading);
  assert((await page.locator('#trafficRows tr').count())===30,'默认周期必须为30天');
  assert((await page.locator('.chart-line').count())===1,'单个孤立记录之后的有效序列应独立绘制');
  assert((await page.locator('#trafficChart circle').count())===0,'曲线不应出现圆形数据点');
  assert((await page.locator('.chart-line').getAttribute('d')).includes(' C'),'曲线仍是折线');
  const geometryOk = await page.evaluate(() => {
    const chart=document.getElementById('trafficChart');
    const grid=[...chart.querySelectorAll('.chart-grid')].map(line=>Number(line.getAttribute('y1')));
    const missing=[...chart.querySelectorAll('.chart-hit')].filter(hit=>Traffic.report.series[Number(hit.dataset.index)].bytes==null);
    for(const path of chart.querySelectorAll('.chart-line')){
      const length=path.getTotalLength();
      for(let i=0;i<=500;i++){
        const point=path.getPointAtLength(length*i/500);
        if(point.y<Math.min(...grid)-.05 || point.y>Math.max(...grid)+.05) return false;
        if(missing.some(hit=>point.x>Number(hit.getAttribute('x'))+.05 && point.x<Number(hit.getAttribute('x'))+Number(hit.getAttribute('width'))-.05)) return false;
      }
    }
    return true;
  });
  assert(geometryOk,'平滑曲线越过用量边界或连接了缺失日期');
  assert((await page.locator('#trafficStatus').textContent()).includes('29/30'),'缺失日期不得计为已记录');
  assert(await page.locator('#trafficRangeForm').isHidden(),'自定义日期应默认折叠');
  await page.screenshot({path:'output/playwright/traffic-dark.png'});
  await page.locator('.traffic-chart-panel').screenshot({path:'output/playwright/traffic-smooth.png'});

  await page.getByLabel('统计周期',{exact:true}).selectOption('7');
  assert((await page.locator('#trafficRows tr').count())===7,'7天筛选失效');
  await page.getByLabel('统计周期',{exact:true}).selectOption('90');
  assert((await page.locator('#trafficRows tr').count())===90,'90天筛选失效');
  await page.getByLabel('统计周期',{exact:true}).selectOption('month');
  assert((await page.locator('#trafficRows tr').count())===new Date().getDate(),'本月筛选失效');
  await page.getByLabel('统计周期',{exact:true}).selectOption('custom');
  const today=await page.locator('#trafficEnd').inputValue();
  await page.getByLabel('开始日期',{exact:true}).fill(today);
  await page.getByRole('button',{name:'查看日期',exact:true}).click();
  assert((await page.locator('#trafficRows tr').count())===1,'自定义单日查询失效');
  assert((await page.locator('#trafficChart .chart-note').count())===1,'单日记录不应虚构连续趋势');
  await page.getByLabel('统计周期',{exact:true}).selectOption('30');
  const firstPoint = page.locator('.chart-hit').first();
  await firstPoint.focus();
  const pointText = await page.locator('#trafficPoint').textContent();
  await page.keyboard.press('ArrowRight');
  assert((await page.locator('#trafficPoint').textContent())!==pointText,'键盘不能查看相邻数据点');

  await page.locator('#trafficDetailSection summary').click();
  assert(await visible('trafficRows'),'每日明细不能展开');
  assert((await page.locator('#trafficRows').textContent()).includes('无记录'),'明细没有保留缺失标记');
  assert((await page.locator('#trafficRows').textContent()).includes('0 B'),'明细没有保留实测零值');
  await page.locator('#trafficDetailSection summary').click();
  await page.evaluate(() => document.documentElement.dataset.mode='light');
  await page.evaluate(() => document.querySelector('.pages').scrollTop=0);
  await page.waitForFunction(()=>getComputedStyle(document.querySelector('.traffic-chart-panel')).backgroundColor==='rgb(255, 255, 255)');
  await page.screenshot({path:'output/playwright/traffic-light.png'});
  await page.setViewportSize({width:390,height:844});
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth>window.innerWidth || document.querySelector('.pages').scrollWidth>document.querySelector('.pages').clientWidth);
  assert(!overflow,'窄窗口出现页面级横向溢出');
  await page.screenshot({path:'output/playwright/traffic-mobile.png'});
  await page.setViewportSize({width:1360,height:1000});

  // 真实桥形状下的状态切换：无数据、恢复、竞态、错误和导出。
  await page.evaluate(() => {
    const key = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    const today=key(new Date());
    window.__trafficRequests=[];
    window.__trafficExports=[];
    window.__trafficMode='empty';
    window.pywebview={api:{
      async get_traffic_report(start,end){
        const mode=window.__trafficMode;
        window.__trafficRequests.push({start,end,mode});
        const days=(Date.parse(end+'T00:00:00Z')-Date.parse(start+'T00:00:00Z'))/86400000+1;
        if(mode==='error') return {ok:false,error:'测试读取失败'};
        if(mode==='race') await new Promise(resolve=>setTimeout(resolve,days===7?180:20));
        const series=[];
        for(let i=0;i<days;i++){
          const d=new Date(Date.parse(start+'T00:00:00Z')+i*86400000);
          series.push({date:d.toISOString().slice(0,10),bytes:mode==='empty'?null:i===0?0:1048576,samples:mode==='empty'?0:2,partial:d.toISOString().slice(0,10)===today,legacy:false});
        }
        const recorded=series.filter(row=>row.bytes!=null);
        return {ok:true,start,end,today,days,series,history_start:recorded.length?start:null,
          summary:{total_bytes:recorded.length?(days-1)*1048576:null,average_daily_bytes:recorded.length?1048576:null,average_days:Math.max(0,recorded.length-1),recorded_days:recorded.length,missing_days:days-recorded.length,peak:recorded.length?recorded[recorded.length-1]:null},
          comparison:{status:'incomplete',percent:null},insights:['隔离桥测试报告'],notes:['隔离桥统计说明'],spikes:[],scope:'测试口径'};
      },
      async export_traffic_report(start,end){
        window.__trafficExports.push({start,end});
        return {ok:true,filename:'测试明细.csv'};
      }
    }};
  });
  await page.getByRole('button',{name:'刷新',exact:true}).click();
  await page.locator('#trafficChart').filter({hasText:'还没有用量记录'}).waitFor();
  assert((await page.locator('#trafficTotal').textContent())==='—','空历史被误报为0');
  assert(await page.locator('#trafficExportCsv').isDisabled(),'无数据不应导出空报告');

  await page.evaluate(() => window.__trafficMode='data');
  await page.getByRole('button',{name:'刷新',exact:true}).click();
  await page.locator('#trafficStatus').filter({hasText:'30/30'}).waitFor();
  assert(await visible('trafficContent'),'从无数据恢复后没有显示报告');
  await page.getByRole('button',{name:'导出明细',exact:true}).click();
  assert((await page.evaluate(()=>window.__trafficExports.length))===1,'导出没有调用后端');

  // 新输入尚未提交时禁用旧报告导出，定时/状态刷新不能抢走输入。
  await page.getByLabel('统计周期',{exact:true}).selectOption('custom');
  await page.getByLabel('开始日期',{exact:true}).fill('2026-09-01');
  assert(await page.locator('#trafficExportCsv').isDisabled(),'改日期后仍可以导出旧报告');
  const before=await page.evaluate(()=>window.__trafficRequests.length);
  await page.evaluate(()=>{Traffic.loadedAt=0;Traffic.updateState(App.state);});
  assert((await page.evaluate(()=>window.__trafficRequests.length))===before,'状态推送抢先查询了未提交日期');

  // 直接调用仅用于可靠地模拟同一事件循环内快速点击两次日期预设。
  await page.evaluate(async()=>{
    window.__trafficMode='race';
    Traffic.applyPreset('7');const oldRequest=Traffic.load();
    Traffic.applyPreset('90');const newRequest=Traffic.load();
    await Promise.all([oldRequest,newRequest]);
  });
  assert((await page.locator('#trafficRows tr').count())===90,'慢的旧请求覆盖了最新日期结果');
  assert((await page.locator('#trafficPeriod').inputValue())==='90','周期选择态不同步');

  await page.evaluate(()=>window.__trafficMode='error');
  await page.getByRole('button',{name:'刷新',exact:true}).click();
  await page.locator('#trafficStatus').filter({hasText:'测试读取失败'}).waitFor();
  assert(!(await visible('trafficContent')),'查询失败仍展示旧报告');
  assert(await page.locator('#trafficExportCsv').isDisabled(),'错误状态仍可导出');
  await page.evaluate(()=>window.__trafficMode='data');
  await page.getByRole('button',{name:'刷新',exact:true}).click();
  await page.locator('#trafficStatus').filter({hasText:'90/90'}).waitFor();
  assert(await visible('trafficContent'),'重试成功后没有恢复报告');
  assert(errors.length===0,'前端存在脚本错误：'+errors.join('; '));
  return {ok:true,checks:['7/30/90天周期','自定义日期折叠','本月和单日','缺失与零值','键盘详情','明细展开','深浅主题','390px布局','空数据恢复','导出桥接','日期编辑保护','异步请求竞争','错误重试'],pageErrors:errors,screenshots:['traffic-dark.png','traffic-light.png','traffic-mobile.png']};
}

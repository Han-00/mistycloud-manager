// 离线前端回归：只注入内存桥，不连接 Python 或真实代理。
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

(async () => {
  const cache = path.join(process.env.LOCALAPPDATA, 'ms-playwright');
  const executablePath = fs.readdirSync(cache).filter(d => /^chromium-\d+$/.test(d))
    .sort((a,b) => Number(b.split('-')[1]) - Number(a.split('-')[1]))
    .map(d => path.join(cache, d, 'chrome-win64', 'chrome.exe')).find(p => fs.existsSync(p));
  const browser = await chromium.launch({executablePath});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:830}});
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.addInitScript(() => {
      let rules = [];
      const state = () => ({rules:rules.map(r => ({...r})), builtin_domains:['qq.com','bilibili.com'], legacy_direct_domains:[], applying:false});
      window.__diagnosticsFixture = {
        sampled_at:'2026-10-03 18:00:00', engine_ports:{socks:10808,http:10809},
        rule_expectation:{domain:'claude.ai',action:'proxy',source:'user'},
        system_proxy:{message:'系统代理已开启；HTTP 指向本工具端口。'},
        applications:{items:[{query:'Claude',status:'observed',listener_verified:true,process_count:3,tool_proxy_connection_count:1,other_external_connections:2,message:'观测到已核验的本引擎端口连接，无法关联登录。'},{query:'Antigravity',status:'no_current_evidence',process_count:2,tool_proxy_connection_count:0,other_external_connections:1,message:'当前无证据，不能判为未使用代理。'}]},
        engine_access:{records:[{timestamp:'2026/10/03 17:59:40',domain:'claude.ai',port:443,outbound:'misty',action:'proxy',age_seconds:20}]}
      };
      window.pywebview = {api:{
        get_routing_rules:async () => state(),
        preview_domain:async raw => {
          let domain;
          try { domain = raw.includes('://') ? new URL(raw).hostname : raw.toLowerCase(); } catch(e) { return {ok:false,error:'域名无效'}; }
          const user = rules.find(r => domain === r.domain || domain.endsWith('.'+r.domain));
          const builtin = domain === 'qq.com' || domain.endsWith('.qq.com');
          return {ok:true,domain,action:user ? user.action : builtin ? 'direct' : 'unknown',source:user ? 'user' : builtin ? 'builtin' : 'template',matched_domain:user && user.domain};
        },
        save_routing_rules:async draft => {
          if(window.__failSave) return {ok:false,error:'模拟保存失败'};
          rules = draft.map(r => ({...r}));
          setTimeout(() => App.rulesSaved({ok:true,applied:true,state:state(),message:'模拟规则已应用'}),20);
          return {ok:true,pending:true};
        },
        diagnose_proxy:async () => { setTimeout(() => App.renderDiagnostics(window.__diagnosticsFixture),20); return {ok:true,pending:true}; },
      }};
    });
    await page.goto('http://127.0.0.1:18763/');
    await page.getByRole('button',{name:'网站分流',exact:true}).click();
    await page.waitForFunction(() => App._rulesLoaded);
    await page.locator('#ruleDomain').fill('https://qq.com/path?token=secret');
    await page.locator('#ruleAction').selectOption('proxy');
    await page.getByRole('button',{name:'添加规则',exact:true}).click();
    await page.waitForFunction(() => App._rulesDraft.length === 1);
    assert.match(await page.locator('#rulesRows').innerText(), /qq\.com/);
    assert.doesNotMatch(await page.locator('#rulesRows').innerText(), /secret/);
    await page.getByRole('button',{name:'代理诊断',exact:true}).click();
    await page.getByRole('button',{name:'网站分流',exact:true}).click();
    assert.equal(await page.locator('.rule-row').count(),1,'导航不应丢弃未保存草稿');
    await page.locator('#ruleDomain').fill('qq.com');
    await page.locator('#ruleAction').selectOption('direct');
    await page.getByRole('button',{name:'添加规则',exact:true}).click();
    await page.waitForFunction(() => App._rulesDraft[0].action === 'direct');
    assert.equal(await page.locator('.rule-row').count(),1,'同域名应覆盖而非重复');
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();
    await page.waitForFunction(() => document.getElementById('rulesStatus').textContent === '已保存并应用');
    await page.locator('#routePreviewDomain').fill('www.qq.com');
    await page.getByRole('button',{name:'查看匹配',exact:true}).click();
    await page.waitForFunction(() => document.getElementById('routePreviewResult').textContent.includes('预期直连'));
    await page.locator('#routePreviewDomain').fill('mistycapsule.xyz');
    await page.getByRole('button',{name:'查看匹配',exact:true}).click();
    await page.waitForFunction(() => document.getElementById('routePreviewResult').textContent.includes('由引擎模板决定，当前未确认'));
    await page.evaluate(() => window.__failSave = true);
    await page.locator('[data-rule-remove="0"]').click();
    await page.getByRole('button',{name:'保存并应用',exact:true}).click();
    await page.waitForFunction(() => document.getElementById('rulesStatus').textContent === '未保存');
    assert.equal(await page.locator('#btnSaveRules').isEnabled(),true);
    assert.equal(await page.evaluate(() => App._rulesDraft.length),0,'失败不应覆盖待保存草稿');
    await page.evaluate(() => {window.__failSave = false; App.setRules({rules:[{domain:'qq.com',action:'proxy'},{domain:'school.example.com',action:'direct'}],builtin_domains:['qq.com','bilibili.com'],applying:false});});
    const output = path.resolve(__dirname,'..','output','playwright');
    fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'routing-dark.png'),animations:'disabled'});
    await page.getByRole('button',{name:'代理诊断',exact:true}).click();
    await page.getByRole('button',{name:'采集连接证据',exact:true}).click();
    await page.waitForFunction(() => document.getElementById('diagnosticsTime').textContent.startsWith('采集于'));
    assert.match(await page.locator('#diagnosticsResult').innerText(),/当前无本工具连接证据/);
    assert.match(await page.locator('#diagnosticsResult').innerText(),/观测到本引擎端口连接/);
    assert.match(await page.locator('#diagnosticsResult').innerText(),/历史记录/);
    await page.screenshot({path:path.join(output,'diagnostics-dark.png'),animations:'disabled'});
    await page.evaluate(() => document.documentElement.dataset.mode = 'light');
    await page.screenshot({path:path.join(output,'diagnostics-light.png'),animations:'disabled'});
    await page.getByRole('button',{name:'网站分流',exact:true}).click();
    await page.screenshot({path:path.join(output,'routing-light.png'),animations:'disabled'});
    await page.evaluate(() => {
      document.querySelector('[data-page="dash"]').click();
      App.renderAnomalies({status:'ready',count:2,threshold_mb:200,ratio:3,items:[{hour:'2026-10-03T10',bytes:260*1048576,baseline:40*1048576,ratio:6.5,basis:'both',accounts:['sample@example.com'],inferred:true},{hour:'2026-10-02T19',bytes:220*1048576,baseline:null,basis:'threshold',accounts:[],inferred:true}]});
    });
    assert.equal(await page.locator('.anomaly-row').count(),2);
    assert.match(await page.locator('#anomalyRows').innerText(),/推断相关账号/);
    assert.match(await page.locator('#anomalyRows').innerText(),/历史基线不足/);
    await page.locator('.anomaly-card').screenshot({path:path.join(output,'anomaly-light.png'),animations:'disabled'});
    await page.evaluate(() => {
      App.renderAnomalies({status:'error'});
      App.renderDiagnostics({...window.__diagnosticsFixture,applications:{status:'unknown',message:'采集超时'},engine_access:{status:'unknown',message:'暂无日志',records:[]}});
    });
    assert.match(await page.locator('#anomalyRows').innerText(),/暂不可用/);
    assert.match(await page.locator('#diagnosticsResult').innerText(),/采集超时/);
    await page.evaluate(() => App.renderDiagnostics({...window.__diagnosticsFixture,
      rule_expectation:{domain:'mistycapsule.xyz',action:'unknown',source:'template'},
      applications:{items:[{query:'Claude',status:'observed',listener_verified:false}]}}));
    const uncertain = await page.locator('#diagnosticsResult').innerText();
    assert.match(uncertain,/配置端口有连接，监听者未确认/);
    assert.match(uncertain,/由引擎模板决定，当前未确认/);
    assert.doesNotMatch(uncertain,/观测到本引擎端口连接/);
    await page.setViewportSize({width:920,height:640});
    await page.getByRole('button',{name:'代理诊断',exact:true}).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false,'最小窗口不得横向溢出');
    await page.evaluate(() => App.renderDiagnostics({...window.__diagnosticsFixture, applications:{items:[{query:'<img src=x onerror=window.__injected=1>',status:'no_current_evidence'}]}}));
    assert.equal(await page.locator('#diagnosticsResult img').count(),0,'外部数据必须转义');
    assert.equal(errors.length,0,errors.join('\n'));
    console.log('前端回归通过：域名添加/去重、草稿保留、异步保存与失败、预览、诊断证据、异常分析、转义、深浅主题、920px窗口；pageerror=0');
  } finally { await browser.close(); }
})().catch(e => {console.error(e);process.exitCode=1;});

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function loadApp() {
  const elements = Object.fromEntries(['qr', 'qrwait', 'qrspinner', 'qrwaittext', 'qrstate', 'dot', 'qrmsg', 'reqr'].map(id =>
    [id, { hidden: false, textContent: '', className: '', disabled: false }]));
  const images = [];
  let finishRefresh;
  const context = vm.createContext({
    document: { querySelector: selector => elements[selector.slice(1)] || null },
    window: { TOKEN: 'test' },
    fetch: url => url === '/api/refresh_qr'
      ? new Promise(resolve => { finishRefresh = () => resolve({ json: async () => ({ ok: true }) }); })
      : Promise.resolve({ json: async () => ({ ok: true }) }),
    Image: class { constructor() { images.push(this); } },
    setInterval: () => 0, clearInterval: () => {}, setTimeout: () => 0, clearTimeout: () => {},
    encodeURIComponent, Date, console,
  });
  const source = fs.readFileSync(path.join(__dirname, '../app/web/app.js'), 'utf8');
  vm.runInContext(source.split('// ---------------- 入口 ----------------')[0], context);
  return { context, elements, images, finishRefresh: () => finishRefresh() };
}

test('an unreachable status endpoint is unknown, not offline', () => {
  const { context } = loadApp();
  context.result = { ok: false, message: '断开连接' };
  const view = vm.runInContext('statusView(result, false)', context);
  assert.equal(view.dot, 'wait');
  assert.match(view.text, /无法|连接/);
  assert.equal(view.canAutoStart, false);
});

test('permission-limited status allows the user to request a safe stop', async () => {
  const { context, elements } = loadApp();
  for (const name of ['app', 'up', 'down', 'diagnose', 'edit-keys', 'edit-prof', 'switch-qq', 'quit', 'uninstall', 'fin', 'stext', 'sdot', 'sqr', 'serr']) {
    elements[name] = { hidden: false, textContent: '', disabled: false };
  }
  context.fetch = async url => ({ json: async () => url === '/api/status'
    ? { astrbot: null, napcat: null, permission_denied: true, message: '需要管理员权限' }
    : { ok: true } });
  vm.runInContext('S.info = {}; shell = () => {}', context);
  await vm.runInContext('pageStatus()', context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(elements.up.disabled, true);
  assert.equal(elements.down.disabled, false);
  assert.match(elements.serr.textContent, /管理员/);
});

test('a manual stop prevents automatic startup when returning to the status page', () => {
  const { context } = loadApp();
  context.result = { astrbot: false, napcat: false, login: 'starting', starting: false, stopped: true };
  assert.equal(vm.runInContext('statusView(result, false).canAutoStart', context), false);
});

test('quit failure is shown without claiming that services stopped', async () => {
  const { context, elements } = loadApp();
  for (const name of ['app', 'up', 'down', 'diagnose', 'edit-keys', 'edit-prof', 'switch-qq', 'quit', 'uninstall', 'fin', 'stext', 'sdot', 'sqr', 'serr']) {
    elements[name] = { hidden: false, textContent: '', disabled: false };
  }
  context.titles = [];
  context.fetch = async url => ({ json: async () => url === '/api/status'
    ? { astrbot: true, napcat: true, login: 'online', starting: false }
    : { ok: false, message: '停止被拒绝' } });
  vm.runInContext("S.info = {}; shell = (page, title) => titles.push(title); confirmBox = async () => true", context);
  await vm.runInContext('pageStatus()', context);
  await elements.quit.onclick();
  assert.ok(!context.titles.includes('她下线了'));
  assert.match(elements.serr.textContent, /拒绝/);
});

test('failed settings restart stays on the error instead of polling old completed steps', async () => {
  const { context, elements } = loadApp();
  for (const name of ['app', 'tasks', 'msg', 'retry', 'apply-back']) elements[name] = { hidden: false, textContent: '' };
  context.titles = [];
  context.fetch = async url => ({ json: async () => url === '/api/apply'
    ? { ok: false, message: '服务停止失败' }
    : { finished: true, steps: [] } });
  vm.runInContext("S.info = {}; shell = (page, title) => titles.push(title); pageStatus = () => titles.push('returned'); renderApplying = () => {}", context);
  await vm.runInContext('applySettings()', context);
  await new Promise(resolve => setImmediate(resolve));
  assert.match(elements.msg.textContent, /停止失败/);
  assert.ok(!context.titles.includes('returned'));
});

test('re-entering installation does not start duplicate progress polling', () => {
  const { context } = loadApp();
  vm.runInContext('pollProgress(); pollProgress()', context);
  assert.equal(vm.runInContext('S.timers.length', context), 1);
});

test('failed state restore stays recoverable and does not open normal controls', async () => {
  const { context, elements } = loadApp();
  for (const id of ['recover-result', 'restore', 'recheck', 'diagnose']) elements[id] = { disabled: false };
  context.titles = [];
  context.fetch = async url => ({ json: async () => url === '/api/info'
    ? { mode: 'recovery', recovery: { backup_available: true } }
    : { ok: false, message: '文件仍被占用' } });
  vm.runInContext("S.mode = 'recovery'; S.info = { recovery: { backup_available: true } }; shell = (page, title) => titles.push(title); pageStatus = () => titles.push('status')", context);
  vm.runInContext('pageRecovery()', context);
  await elements.restore.onclick();
  assert.match(elements['recover-result'].textContent, /占用/);
  assert.equal(elements.restore.disabled, false);
  assert.ok(!context.titles.includes('status'));
});

test('rechecking a recovery updates availability of a newly provided backup', async () => {
  const { context, elements } = loadApp();
  for (const id of ['recover-result', 'restore', 'recheck', 'diagnose']) elements[id] = { disabled: false, hidden: true };
  context.fetch = async url => ({ json: async () => url === '/api/info'
    ? { mode: 'recovery', recovery: { backup_available: true } }
    : { ok: false, message: '状态损坏' } });
  vm.runInContext("S.mode = 'recovery'; S.info = { recovery: { backup_available: false } }; shell = () => {}", context);
  vm.runInContext('pageRecovery()', context);
  await elements.recheck.onclick();
  assert.equal(elements.restore.hidden, false);
});

test('an online QQ response with a missing process record does not trigger a restart', () => {
  const { context } = loadApp();
  context.result = { astrbot: false, napcat: false, login: 'online', starting: false };
  const view = vm.runInContext('statusView(result, false)', context);
  assert.equal(view.dot, 'wait');
  assert.equal(view.canAutoStart, false);
});

test('a refresh hides the old QR until a different image is available', async () => {
  const { context, elements, images, finishRefresh } = loadApp();
  const controller = vm.runInContext('makeQRController()', context);
  controller.update({ status: 'need_scan', qr_revision: 'old' });
  images[0].onload();
  assert.equal(elements.qr.hidden, false);
  controller.update({ status: 'need_scan', qr_revision: 'old' });
  assert.equal(images.length, 1);
  const refresh = elements.reqr.onclick();
  assert.equal(elements.qr.hidden, true);
  controller.update({ status: 'need_scan', qr_revision: 'old' });
  assert.equal(elements.qr.hidden, true);
  finishRefresh();
  await refresh;
  controller.update({ status: 'need_scan', qr_revision: 'new' });
  images.at(-1).onload();
  assert.equal(elements.qr.hidden, false);
  assert.match(elements.qr.src, /rev=new/);
});

test('a late image response cannot show an expired QR', () => {
  const { context, elements, images } = loadApp();
  const controller = vm.runInContext('makeQRController()', context);
  controller.update({ status: 'need_scan', qr_revision: 'old' });
  controller.update({ status: 'error', qr_revision: 'old', error: '二维码已过期' });
  images[0].onload();
  assert.equal(elements.qr.hidden, true);
  assert.match(elements.qrstate.textContent, /失效/);
  assert.equal(elements.qrspinner.hidden, true);
  assert.match(elements.qrwaittext.textContent, /失效/);
});

test('path page offers accessible radio choices and only shows repair for an existing installation', () => {
  const { context, elements } = loadApp();
  for (const id of ['diagnose', 'go', 'path', 'platform-hint', 'platform']) elements[id] = { value: '', textContent: '', className: '' };
  elements.platform.value = 'napcat';
  const query = context.document.querySelector;
  context.document.querySelector = selector => selector === 'input[name="platform"]:checked' ? elements.platform : query(selector);
  context.html = '';
  context.document.querySelectorAll = () => [];
  vm.runInContext("S.info = {default_path: 'D:/Fish', platform: 'napcat', can_repair: false}; shell = (page, title, sub, body, foot) => {html = body + foot}", context);
  vm.runInContext('pagePath()', context);
  assert.match(context.html, /type="radio"[^>]*value="napcat"[^>]*checked/);
  assert.match(context.html, /type="radio"[^>]*value="official"/);
  assert.doesNotMatch(context.html, /<select id="platform"/);
  assert.match(context.html, /id="diagnose"[^>]*hidden/);
  vm.runInContext("S.info = {default_path: 'D:/Fish', platform: 'official', can_repair: true}; pagePath()", context);
  assert.match(context.html, /value="official"[^>]*checked/);
  assert.doesNotMatch(context.html, /id="diagnose"[^>]*hidden/);
});

test('connection failure and non-JSON response have different useful messages', async () => {
  const { context } = loadApp();
  context.fetch = async () => { throw new Error('network failed'); };
  const network = await vm.runInContext("api('/api/path', {})", context);
  context.fetch = async () => ({ status: 503, json: async () => { throw new SyntaxError('bad json'); } });
  const invalid = await vm.runInContext("api('/api/path', {})", context);
  assert.equal(network.ok, false);
  assert.equal(invalid.ok, false);
  assert.notEqual(network.message, invalid.message);
  assert.match(invalid.message, /503/);
});

test('official credential help links to official setup instructions', () => {
  const { context, elements } = loadApp();
  for (const id of ['back', 'go', 'owner_qq', 'official_appid', 'official_secret', 'memory_enabled', 'voice_enabled', 'siliconflow', 'deepseek', 'app_id', 'token']) elements[id] = { value: '', checked: false };
  context.html = '';
  vm.runInContext("S.info = {platform: 'official'}; S.mode = 'install'; shell = (page, title, sub, body) => {html = body}", context);
  vm.runInContext('pageKeys()', context);
  assert.match(context.html, /q\.qq\.com/);
  assert.match(context.html, /docs\.astrbot\.app\/platform\/qqofficial\/websockets\.html/);
  assert.doesNotMatch(context.html, /几块钱就够试很久|模型是免费的|约 1 元\/分钟/);
});

test('saving keys shows request failures instead of silently leaving the user on the form', async () => {
  const { context, elements } = loadApp();
  for (const id of ['back', 'go', 'msg', 'owner_qq', 'memory_enabled', 'voice_enabled', 'siliconflow', 'deepseek', 'app_id', 'token']) elements[id] = { value: '', checked: false, textContent: '', disabled: false };
  context.fetch = async () => ({ json: async () => ({ ok: false, message: '磁盘不可写，日志：installer.log' }) });
  vm.runInContext("S.info = {}; S.mode = 'install'; shell = () => {}", context);
  vm.runInContext('pageKeys()', context);
  await elements.go.onclick();
  assert.match(elements.msg.textContent, /磁盘不可写.*installer\.log/);
});


test('official install does not demand OpenID before binding', () => {
  const { context, elements } = loadApp();
  for (const id of ['back', 'go', 'owner_qq', 'official_appid', 'official_secret', 'memory_enabled', 'voice_enabled', 'siliconflow', 'deepseek', 'app_id', 'token']) elements[id] = { value: '', checked: false };
  context.html = '';
  vm.runInContext("S.info = {platform: 'official'}; S.mode = 'install'; shell = (page, title, sub, body) => {html = body}", context);
  vm.runInContext('pageKeys()', context);
  assert.doesNotMatch(context.html, /id="owner_qq"/);
});

test('official install enters binding before completion', () => {
  const { context, elements } = loadApp();
  elements.tasks = { innerHTML: '' }; elements.retry = { hidden: true };
  context.calls = [];
  vm.runInContext("S.info = {platform: 'official'}; S.page = 3; S.progress = {steps:[{key:'login',status:'doing'}]}; pageOfficialBind = () => calls.push('bind'); pageDone = () => calls.push('done'); renderTasks()", context);
  assert.deepEqual(context.calls, ['bind']);
});


test('binding confirmation includes the exact candidate version shown', async () => {
  const { context, elements } = loadApp();
  for (const id of ['bind-code', 'bind-confirm', 'bind-message', 'bind-back', 'bind-retry', 'bind-copy', 'bind-sent', 'bind-copy-result', 'bind-help', 'bind-diagnose', 'bind-hint', 'finish-help', 'finish-retry', 'finish-error', 'finish-message', 'finish-time']) elements[id] = { textContent: '', disabled: true };
  let submitted;
  context.fetch = async (url, options) => ({ json: async () => {
    if (url === '/api/official_bind/confirm') { submitted = JSON.parse(options.body); return {ok:true}; }
    return {ok:true, code:'bind abc', candidate:'***1234', candidate_id:'version-1', bound:false};
  }});
  vm.runInContext("S.info = {platform:'official'}; shell = () => {}; pageOfficialBind()", context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(elements['bind-confirm'].disabled, false);
  await elements['bind-confirm'].onclick();
  assert.equal(submitted.candidate_id, 'version-1');
});


test('binding page reports login failure and allows retry after confirmation', async () => {
  const { context, elements } = loadApp();
  for (const id of ['bind-code', 'bind-confirm', 'bind-message', 'bind-back', 'bind-retry', 'bind-copy', 'bind-sent', 'bind-copy-result', 'bind-help', 'bind-diagnose', 'bind-hint', 'finish-help', 'finish-retry', 'finish-error', 'finish-message', 'finish-time']) elements[id] = { textContent: '', disabled: true, hidden: true };
  let retries = 0;
  context.fetch = async url => ({json: async () => url === '/api/official_bind' ? {ok:true, bound:true} :
    url === '/api/progress' ? {steps:[{key:'login', status:'error', message:'写入配置失败'}]} : (++retries, {ok:true})});
  vm.runInContext("S.info = {platform:'official'}; shell = () => {}; pageOfficialBind()", context);
  await new Promise(resolve => setImmediate(resolve));
  assert.match(elements['finish-error'].textContent, /写入配置失败/);
  assert.equal(elements['finish-retry'].hidden, false);
  await elements['finish-retry'].onclick();
  assert.equal(retries, 1);
});

test('binding page gives recovery instructions when the installer stops responding', async () => {
  const { context, elements } = loadApp();
  for (const id of ['bind-code', 'bind-confirm', 'bind-message', 'bind-back', 'bind-retry', 'bind-copy', 'bind-sent', 'bind-copy-result', 'bind-help', 'bind-diagnose', 'bind-hint', 'finish-help', 'finish-retry', 'finish-error', 'finish-message', 'finish-time']) elements[id] = { textContent: 'old', disabled: false };
  context.fetch = async () => { throw new Error('network'); };
  vm.runInContext("S.info = {platform:'official'}; shell = () => {}; pageOfficialBind()", context);
  await new Promise(resolve => setImmediate(resolve));
  assert.match(elements['bind-code'].placeholder, /无法获取/);
  assert.match(elements['bind-message'].textContent, /重新.*一键包/);
  assert.equal(elements['bind-confirm'].disabled, true);
});

test('binding page distinguishes QQ disconnection from installer failure', async () => {
  const { context, elements } = loadApp();
  for (const id of ['bind-code', 'bind-confirm', 'bind-message', 'bind-back', 'bind-retry', 'bind-copy', 'bind-sent', 'bind-copy-result', 'bind-help', 'bind-diagnose', 'bind-hint', 'finish-help', 'finish-retry', 'finish-error', 'finish-message', 'finish-time']) elements[id] = { textContent: '', disabled: true };
  context.fetch = async () => ({json: async () => ({ok:true, code:'绑定 code', connection:'disconnected', bound:false})});
  vm.runInContext("S.info = {platform:'official'}; shell = () => {}; pageOfficialBind()", context);
  await new Promise(resolve => setImmediate(resolve));
  assert.match(elements['bind-message'].textContent, /QQ.*断开/);
  assert.match(elements['bind-message'].textContent, /权限|网络/);
});


test('official setup explicitly uses WebSocket and shows the platform settings path', () => {
  const {context} = loadApp();
  assert.match(vm.runInContext('HELP.officialApp', context), /WebSocket/);
  assert.match(vm.runInContext('HELP.officialApp', context), /事件订阅与回调地址/);
});

function bindingPage(response) {
  const app = loadApp();
  for (const id of ['bind-code', 'bind-confirm', 'bind-message', 'bind-back', 'bind-retry', 'bind-copy', 'bind-sent', 'bind-copy-result', 'bind-help', 'bind-diagnose', 'bind-hint', 'finish-help', 'finish-retry', 'finish-error', 'finish-message', 'finish-time']) {
    app.elements[id] = {textContent:'', disabled:true, hidden:false, focus() {}, select() {this.selected = true;}};
  }
  app.context.fetch = async () => ({json: async () => response});
  vm.runInContext("S.info = {platform:'official'}; shell = () => {}; pageOfficialBind()", app.context);
  return app;
}

test('binding copy uses the full current command and reports clipboard failure', async () => {
  const {context, elements} = bindingPage({ok:true, code:'绑定 test-code', bound:false, receipt:'not_received', connection:'ready'});
  let copied;
  context.navigator = {clipboard:{writeText:async text => {copied=text;}}};
  await new Promise(resolve => setImmediate(resolve));
  await elements['bind-copy'].onclick();
  assert.equal(copied, '绑定 test-code');
  assert.match(elements['bind-copy-result'].textContent, /已复制/);
  context.navigator.clipboard.writeText = async () => {throw new Error('denied');};
  await elements['bind-copy'].onclick();
  assert.equal(elements['bind-code'].selected, true);
  assert.match(elements['bind-copy-result'].textContent, /Ctrl\+C/);
});

test('sent button reports no delivery and never grants ownership', async () => {
  const {elements} = bindingPage({ok:true, code:'绑定 test-code', bound:false, receipt:'not_received', connection:'ready'});
  await new Promise(resolve => setImmediate(resolve));
  await elements['bind-sent'].onclick();
  assert.match(elements['bind-message'].textContent, /还没收到/);
  assert.match(elements['bind-message'].textContent, /WebSocket/);
  assert.equal(elements['bind-confirm'].disabled, true);
  assert.equal(elements['bind-help'].open, true);
});

test('binding reports a rejected command and keeps confirmation locked', async () => {
  const {elements} = bindingPage({ok:true, code:'绑定 test-code', bound:false, receipt:'wrong_code', connection:'ready'});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(elements['bind-message'].textContent, /不匹配/);
  assert.equal(elements['bind-confirm'].disabled, true);
});

test('one click repair stops services first and will not repair after a stop error', async () => {
  const {context, elements} = loadApp();
  for (const id of ['back','fix','ai','diag-result']) elements[id] = {disabled:false};
  const calls = [];
  let stopOk = false;
  context.fetch = async url => { calls.push(url); return {json:async () =>
    url === '/api/diagnose' ? {items:[{code:'astrbot_path_missing',message:'路径缺失'}]} :
    url === '/api/progress' ? {running:false} :
    url === '/api/stop' ? {ok:stopOk,message:'停止被拒绝'} : {ok:true,report:{items:[]}}}; };
  vm.runInContext("shell = () => {}; S.mode='status'", context);
  await vm.runInContext('pageDiagnostics(() => {})', context);
  await elements.fix.onclick();
  assert.ok(!calls.includes('/api/repair'));
  assert.match(elements['diag-result'].textContent, /拒绝/);
  stopOk = true;
  calls.length = 0;
  await elements.fix.onclick();
  assert.deepEqual(calls, ['/api/progress','/api/stop','/api/repair']);
});


test('binding presents only one primary action after a message is received', async () => {
  const {elements} = bindingPage({ok:true, code:'绑定 test-code', candidate:'user', candidate_id:'current'});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(elements['bind-sent'].hidden, true);
  assert.equal(elements['bind-confirm'].hidden, false);
});

test('confirmed binding replaces the command page with a dedicated finishing screen', async () => {
  const {context, elements} = bindingPage({ok:true, bound:true});
  context.rendered = [];
  context.fetch = async () => ({json:async () => ({steps:[{key:'login', status:'doing', message:'正在保存主人资料'}]})});
  vm.runInContext('shell = (page,title,sub,body) => rendered.push({title,body})', context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(context.rendered.at(-1).title, '认主成功，正在收尾');
  assert.ok(!context.rendered.at(-1).body.includes('bind-code'));
  assert.match(elements['finish-message'].textContent, /保存主人/);
});

test('official connection failure is not displayed as an online bot', () => {
  const {context} = loadApp();
  context.result = {astrbot:true, napcat:false, login:'official', qq_connection:'ready', connection_error:'检查 AppSecret'};
  assert.equal(vm.runInContext('statusView(result,true).dot',context),'off');
  assert.equal(vm.runInContext('statusView(result,true).reconnect',context),true);
  context.result.connection_error = '';
  assert.equal(vm.runInContext('statusView(result,true).dot',context),'on');
});

test('done page copies individual credentials and never prints the password in feedback', async () => {
  const {context,elements} = loadApp();
  for (const id of ['fin','done-copy-result','copy-address','copy-username','copy-password']) elements[id] = {};
  for (const [id,value] of [['address','http://127.0.0.1:6185'],['username','fish'],['password','fake-password']]) elements['done-'+id] = {value};
  context.fetch = async () => ({json:async () => ({dashboard_url:'http://127.0.0.1:6185', username:'fish', password:'fake-password'})});
  const copied = [];
  context.navigator = {clipboard:{writeText:async value => copied.push(value)}};
  vm.runInContext("S.info = {}; shell = () => {}", context);
  await vm.runInContext('pageDone()', context);
  for (const field of ['address','username','password']) await elements['copy-'+field].onclick();
  assert.deepEqual(copied,['http://127.0.0.1:6185','fish','fake-password']);
  assert.ok(!elements['done-copy-result'].textContent.includes('fake-password'));
});

test('key page contains a single official credential guide', () => {
  const {context} = loadApp();
  const first = vm.runInContext('keyField("official_appid","AppID","","text")',context);
  const second = vm.runInContext('keyField("official_secret","AppSecret",HELP.officialApp)',context);
  assert.equal((first+second).match(/事件订阅与回调地址/g).length,1);
});

test('credential repair saves and reapplies settings instead of restarting the onboarding form', async () => {
  const {context,elements} = loadApp();
  for (const id of ['back','go','msg','official_appid','official_secret','memory_enabled','voice_enabled','siliconflow','deepseek','app_id','token']) elements[id] = {value:'',checked:false,textContent:''};
  context.applied = false;
  context.fetch = async url => ({json:async () => url==='/api/info' ? {platform:'official',profile:{}} : {ok:true}});
  vm.runInContext("S.info={platform:'official'}; S.mode='install'; shell=()=>{}; applySettings=()=>{applied=true}; pageKeys(true)",context);
  await elements.go.onclick();
  assert.equal(context.applied,true);
});

test('known QQ credential errors lead directly to credential repair', async () => {
  const {context,elements} = loadApp();
  for(const id of ['back','fix','ai','diag-result']) elements[id] = {};
  context.repair = null;
  context.fetch = async () => ({json:async () => ({items:[{code:'qq_credentials_invalid',message:'检查 AppSecret'}]})});
  vm.runInContext('shell=()=>{}; pageKeys=value=>{repair=value}',context);
  await vm.runInContext('pageDiagnostics(()=>{})',context);
  await elements.fix.onclick();
  assert.equal(context.repair,true);
});
test('rejected QQ credentials offer direct editing and disable useless sending', async () => {
  const {context, elements} = bindingPage({ok:true, code:'绑定 test-code', issue_code:'qq_credentials_invalid', problem:'QQ 平台拒绝凭证'});
  await new Promise(resolve => setImmediate(resolve));
  vm.runInContext('pageKeys = (repair, back) => { window.edit = {repair, back} }', context);
  elements['bind-back'].onclick();
  assert.equal(context.window.edit.repair, true);
  assert.equal(typeof context.window.edit.back, 'function');
  assert.equal(elements['bind-sent'].hidden, true);
  assert.equal(elements['bind-copy'].disabled, true);
  assert.equal(elements['bind-back'].className, 'primary');
});

test('binding connection retry uses the narrow reconnect flow', async () => {
  const {context, elements} = bindingPage({ok:true, code:'绑定 test-code', connection:'disconnected'});
  await new Promise(resolve => setImmediate(resolve));
  vm.runInContext('applySettings = action => { window.action = action }', context);
  elements['bind-retry'].onclick();
  assert.equal(context.window.action, 'reconnect');
});

test('key edit can return directly to binding without starting another install', () => {
  const {context, elements} = loadApp();
  for (const id of ['go','back','official_appid','official_secret','deepseek','siliconflow','app_id','token','memory_enabled','voice_enabled']) {
    elements[id] = {value:'',checked:false};
  }
  vm.runInContext("S.info={platform:'official', saved_keys:['official_appid','official_secret','deepseek']}; S.mode='install'; shell=()=>{}; pageKeys(true,()=>{window.returned=true})",context);
  elements.back.onclick();
  assert.equal(context.window.returned,true);
});

test('a busy directory has an existing-window route without a misleading backup restore', () => {
  const {context,elements} = loadApp();
  elements.recheck = {};
  vm.runInContext("S.info={recovery:{busy:true,continue_url:'http://127.0.0.1:1234/?token=test',message:'已有窗口'}}; shell=(step,title,sub,body,footer)=>{window.view={title,body,footer}}; pageRecovery()",context);
  assert.match(context.window.view.title,/已有/);
  assert.match(context.window.view.body,/打开已有窗口/);
  assert.doesNotMatch(context.window.view.footer,/恢复状态备份/);
});

test('returning after a failed reconnect does not leave stale polling behind', async () => {
  const {context,elements} = loadApp();
  for (const id of ['tasks','msg','retry','apply-back']) elements[id]={textContent:'',disabled:true,hidden:true};
  let requested;
  context.fetch=async url => {requested=url;return {json:async()=>({ok:false,message:'停止失败'})}};
  vm.runInContext("S.info={platform:'official'}; shell=()=>{}; pageOfficialBind=()=>{window.back=true}",context);
  await vm.runInContext("applySettings('reconnect')",context);
  assert.equal(requested,'/api/reconnect');
  assert.match(elements.msg.textContent,/停止失败/);
  assert.equal(elements['apply-back'].disabled,false);
  elements['apply-back'].onclick();
  assert.equal(context.window.back,true);
  assert.equal(vm.runInContext('S.timers.length',context),0);
});


test('initial key entry preserves the ongoing extraction progress poll', () => {
  const {context,elements}=loadApp();
  for(const id of ['go','back','owner_qq','deepseek','siliconflow','app_id','token','memory_enabled','voice_enabled']) elements[id]={value:'',checked:false};
  vm.runInContext("S.info={platform:'napcat'}; S.mode='install'; S.timers=[123]; shell=()=>{}; pageKeys()",context);
  assert.equal(vm.runInContext('S.timers.length',context),1);
});

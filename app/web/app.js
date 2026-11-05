"use strict";
// 大肥鱼一键包向导。没有构建步骤，纯原生 JS。

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, body) {
  const opt = { headers: { "X-Token": window.TOKEN } };
  if (body !== undefined) {
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  // 读取状态不能无限等待；耗时操作有更长的等待上限。
  const timeout = body === undefined ? 10000 : path === "/api/repair" ? 900000 : 120000;
  if (typeof AbortSignal !== "undefined" && AbortSignal.timeout) opt.signal = AbortSignal.timeout(timeout);
  let r;
  try { r = await fetch(path, opt); }
  catch (e) {
    if (e.name === "TimeoutError" || e.name === "AbortError")
      return { ok: false, message: "本鱼还没等到回应，操作可能仍在后台进行。请稍后检查结果，不用重复点击。" };
    return { ok: false, message: "本鱼没收到安装程序的回应。请关闭窗口，重新双击一键包并选择原安装位置继续。" };
  }
  try { return await r.json(); }
  catch (e) {
    return { ok: false, message: `安装程序返回的内容无法读取（HTTP ${r.status}），请重试并查看日志。` };
  }
}

setInterval(() => api("/api/ping", {}), 3000);
api("/api/ping", {});
window.addEventListener?.("pageshow", () => api("/api/ping", {}));
document.addEventListener?.("visibilitychange", () => {
  if (document.visibilityState === "visible") api("/api/ping", {});
});
window.addEventListener?.("pagehide", () => {
  fetch("/api/window_closed", {method:"POST", headers:{"X-Token":window.TOKEN}, body:"{}", keepalive:true}).catch(() => {});
});

async function copyField(input, feedback, success = "已复制") {
  const value = input.value;
  try {
    await navigator.clipboard.writeText(value);
    feedback.textContent = input.value === value ? success : "内容刚刚更新，请重新复制。";
  } catch {
    input.focus(); input.select();
    feedback.textContent = "无法自动复制，已选中内容，请按 Ctrl+C 复制。";
  }
}

const PAGES = ["选位置", "填 Key", "个性化", "安装", "扫码", "完成"];
const PICS = ["wave", "smile", "wink", "hug", "night", "wave"];
const SAYS = [
  "本鱼住哪儿？先说好，得有网、有电，还得管饭！Token 也算饭嗷！",
  "饭票呢饭票呢？没有 Token，本鱼就只能张嘴吐泡泡了……咕噜。",
  "想让本鱼乖乖听话？哼，本鱼可是有鱼格的！……不过加饭可以商量。",
  "正在深度求索……才不是 DeepSleep！本鱼只是吃个饭！",
  "快扫码开门！本鱼已经在 QQ 门口扑腾半天啦，尾巴都拍酸了！",
  "安装成功！本鱼宣布：这个 QQ 归本鱼管了！你负责大米饭！",
];
const STATUS_SAY = "我在这里陪你，今天想聊天还是先投喂呀？";

const S = { page: 0, info: null, progress: null, timers: [], timerGeneration: 0, progressPolling: false };

function clearTimers() {
  S.timers.forEach(clearInterval);
  S.timers = [];
  S.timerGeneration++;
  S.progressPolling = false;
}

function shell(page, title, sub, bodyHtml, footHtml, say) {
  const steps = PAGES.map((n, i) =>
    `<li class="${i === page ? "on" : i < page ? "past" : ""}"><span class="n">${i < page ? "✓" : i + 1}</span>${i === 4 && S.info?.platform === "official" ? "绑定" : n}</li>`).join("");
  const name = S.info?.profile?.name || "大肥鱼";
  $("#app").innerHTML = `
  <div class="shell">
    <aside class="side">
      <div class="brand">${esc(name)}一键包<small>把 DeepSeek 养进 QQ</small></div>
      <img class="avatar" src="/img/${PICS[Math.max(page, 0)]}.jpg" alt="">
      <div class="bubble">${esc((say || (page < 0 ? STATUS_SAY : SAYS[Math.max(page, 0)])).replace("大肥鱼", name))}</div>
      ${page >= 0 && S.mode === "install" ? `<ol class="steps">${steps}</ol>` : ""}
    </aside>
    <main class="main page-${page}">
      <h1>${title}</h1>
      <p class="sub">${sub}</p>
      <div class="body">${bodyHtml}</div>
      <div class="foot">${footHtml || ""}</div>
    </main>
  </div>`;
}

/** 居中弹窗确认。opts: {title, body, ok, danger, need}（need 填了就要照着抄一遍才能点确认）*/
function confirmBox(opts) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "modal";
    wrap.innerHTML = `<div class="card" role="dialog" aria-modal="true" aria-labelledby="mt">
      <h2 id="mt">${opts.title}</h2>
      <div class="mbody">${opts.body}</div>
      ${opts.need ? `<label class="field"><span>想继续，请在下面抄一遍「${esc(opts.need)}」</span>
        <input id="mneed" type="text" autocomplete="off" spellcheck="false"></label>` : ""}
      <div class="mfoot"><button id="mno">取消</button>
        <button id="myes" class="${opts.danger ? "danger" : "primary"}" ${opts.need ? "disabled" : ""}>${esc(opts.ok || "确定")}</button></div>
    </div>`;
    document.body.appendChild(wrap);
    const close = (v) => { wrap.remove(); document.removeEventListener("keydown", onKey); resolve(v); };
    const onKey = (e) => e.key === "Escape" && close(false);
    document.addEventListener("keydown", onKey);
    wrap.onclick = (e) => e.target === wrap && close(false);
    $("#mno", wrap).onclick = () => close(false);
    $("#myes", wrap).onclick = () => close(true);
    if (opts.need) {
      const inp = $("#mneed", wrap);
      inp.oninput = () => ($("#myes", wrap).disabled = inp.value.trim() !== opts.need);
      inp.focus();
    } else $("#myes", wrap).focus();
  });
}

function miniProgress() {
  const p = S.progress;
  if (!p || !p.steps.length) return `<div class="minibar">后台准备中…<div class="bar"><i style="width:3%"></i></div></div>`;
  const done = p.steps.filter((s) => s.status === "done").length;
  const cur = p.steps.find((s) => s.status === "doing" || s.status === "error");
  const label = cur ? (cur.status === "error" ? "后台出错了，到第 4 步看看" : (p.waiting ? "等你填完这页" : cur.title + "…")) : "后台准备好了";
  return `<div class="minibar">后台：${esc(label)}<div class="bar"><i style="width:${Math.max(3, (done / p.steps.length) * 100)}%"></i></div></div>`;
}

function pollProgress() {
  if (S.progressPolling) return;
  S.progressPolling = true;
  const generation = S.timerGeneration;
  let busy = false;
  const tick = async () => {
    if (busy) return;
    busy = true;
    try {
      const progress = await api("/api/progress");
      if (generation !== S.timerGeneration || !Array.isArray(progress.steps)) return;
      const changed = JSON.stringify(S.progress) !== JSON.stringify(progress);
      S.progress = progress;
      const el = $("#mini");
      if (el) el.innerHTML = miniProgress();
      if (S.page === 3 && changed) renderTasks();
    } finally { busy = false; }
  };
  tick();
  S.timers.push(setInterval(tick, 1500));
}

// ---------------- 第 1 页：位置 ----------------
function pagePath() {
  S.page = 0;
  shell(0, "欢迎来到大肥鱼", "先选一种 QQ 接入方式，再为她选个住处。", `
    <fieldset class="platform-choices"><legend>QQ 接入方式</legend>
      <label class="platform-choice"><input type="radio" name="platform" value="napcat" ${S.info.platform !== "official" ? "checked" : ""}>
        <span><strong>QQ 小号</strong><small>功能更完整，需使用小号登录</small></span></label>
      <label class="platform-choice"><input type="radio" name="platform" value="official" ${S.info.platform === "official" ? "checked" : ""}>
        <span><strong>QQ 官方机器人</strong><small>基础聊天，需预先申请并配置</small></span></label>
    </fieldset>
    <div class="mode-hint" id="platform-hint" role="status"></div>
    <label class="field">
      <span>安装位置</span>
      <input id="path" type="text" value="${esc(S.info.default_path)}" spellcheck="false" autocomplete="off">
      <small class="field-hint">建议预留 10 GB。路径只用英文、数字和符号，不含空格；已有安装可选原位置继续。</small>
    </label>
    <details class="help legal"><summary>关于组件和使用许可</summary><div>
      <p>本包为非商业整合安装工具。AstrBot（AGPL-3.0）、NapCat（QQ 协议适配）、Python（PSF）及大肥鱼配置包（MIT）的版权与许可归各原作者所有。</p>
      <p>人设文字、立绘和表情包素材仅供个人学习与自用。<strong>请勿将本包或其中素材用于商业用途、二次售卖或再分发。</strong>素材权利人如希望移除，请联系整合包作者。</p>
      <p>完整许可证见安装目录的 <code>pack\\LICENSE</code> 与 <code>astrbot\\LICENSE</code>。继续安装即表示同意上述条款；使用第三方 QQ 客户端需自行承担账号风险。</p>
    </div></details>
    <div id="msg" class="msg" role="alert"></div>`,
    `<button id="diagnose" ${S.info.can_repair ? "" : "hidden"}>检测与修复</button><button class="primary" id="go">${S.info.root ? "继续安装" : "开始安装"}</button>`);
  $("#diagnose").onclick = () => pageDiagnostics(pagePath);
  const selectedPlatform = () => $('input[name="platform"]:checked').value;
  const showPlatformHint = () => {
    const official = selectedPlatform() === "official";
    $("#platform-hint").className = official ? "mode-hint" : "mode-hint caution";
    $("#platform-hint").textContent = official
      ? "需要机器人 AppID 和 AppSecret；安装后通过私聊绑定主人。QQ 空间等小号功能不可用。"
      : "请使用 QQ 小号登录。第三方登录可能导致功能限制或账号风险。";
  };
  document.querySelectorAll('input[name="platform"]').forEach((radio) => { radio.onchange = showPlatformHint; });
  showPlatformHint();
  const go = async () => {
    $("#go").disabled = true;
    const r = await api("/api/path", { path: $("#path").value, platform: selectedPlatform() });
    $("#go").disabled = false;
    if (!r.ok) {
      if (r.recovery) {
        S.info = await api("/api/info");
        S.mode = S.info.mode;
        return pageRecovery();
      }
      $("#msg").className = "msg bad";
      $("#msg").textContent = r.message;
      return;
    }
    S.info = await api("/api/info");
    pollProgress();
    if (r.resume?.keys && r.resume?.profile) pageTasks();
    else if (r.resume?.keys) pageProfile();
    else pageKeys();
  };
  $("#go").onclick = go;
  $("#path").onkeydown = (e) => e.key === "Enter" && go();
}

// ---------------- 第 2 页：key ----------------
const OFFICIAL_CONNECTION_HELP = `<ol>
  <li>打开 <a href="https://q.qq.com/" target="_blank" rel="noopener">QQ 开放平台</a>，选择填写 AppID 时的同一个机器人。</li>
  <li>进入「开发基础设置」→「事件订阅与回调地址」，选择 <b>WebSocket</b> 并保存。本一键包使用这种方式；选成 Webhook 时，消息不会送到本机。</li>
  <li>在「开发基础设置」→「内部体验号码」中添加你发消息用的 QQ 号。</li>
  <li>回到 QQ，私聊这个机器人，重新发送上方完整指令，然后点「我已发送，检查结果」。</li>
</ol><p>平台设置需要你手动保存，一键包不能代改。<a href="https://bot.q.qq.com/wiki/#_8-开发基础设置" target="_blank" rel="noopener">查看腾讯官方说明</a></p>`;

const HELP = {
  qq: `她只认这个号当主人：只听你的、会主动找你聊天。填你自己平时用的大号，<b>不是</b>给她登录的小号。`,
  officialApp: `<ol><li>打开 <a href="https://q.qq.com/" target="_blank" rel="noopener">QQ 开放平台</a>，登录并创建 QQ 机器人。</li>
    <li>选择这个机器人，在开发设置中复制 AppID 和 AppSecret，分别填入对应输入框。AppSecret 是密钥，请勿分享。</li>
    <li>「开发基础设置」→「事件订阅与回调地址」选择 <b>WebSocket</b> 并保存。此一键包通过 WebSocket 接收消息，不能选 Webhook。</li>
    <li>「开发基础设置」→「内部体验号码」添加主人的 QQ 号；安装后再用这个 QQ 私聊机器人完成绑定。</li></ol>
    <p><a href="https://docs.astrbot.app/platform/qqofficial/websockets.html" target="_blank" rel="noopener">AstrBot 接入指南</a> · <a href="https://bot.q.qq.com/wiki/" target="_blank" rel="noopener">腾讯官方说明</a></p>`,
  deepseek: `<ol>
    <li>打开 <a href="https://platform.deepseek.com/" target="_blank">platform.deepseek.com</a>，用手机号登录。认准这个地址，别点广告和中转站。</li>
    <li>根据账户余额与实际需求充值，具体价格以平台显示为准。</li>
    <li>左侧「API keys」→「创建 API key」，名字随便填。</li>
    <li>弹出来的 key 只显示一次，点「复制」粘到这里。</li></ol>`,
  doubao: `<ol>
    <li>打开 <a href="https://www.volcengine.com/" target="_blank">volcengine.com</a> 注册，做<b>个人实名认证</b>（必须）。</li>
    <li>打开<b>旧版</b>豆包语音控制台 <a href="https://console.volcengine.com/speech/app" target="_blank">console.volcengine.com/speech/app</a>，「应用管理」里没有应用就「创建应用」。</li>
    <li>在「API 服务中心」找到「豆包音频生成模型 1.0」，选择你的应用并查看开通条件、试用与计费说明。</li>
    <li>在应用管理页复制 <b>APP ID</b> 和 <b>Access Token</b>。火山方舟的 API Key 和 Secret Key 不是这里需要的凭证。</li></ol>
    费用和可用额度以火山引擎控制台显示为准。`,
  siliconflow: `<ol>
    <li>打开 <a href="https://cloud.siliconflow.cn/" target="_blank">cloud.siliconflow.cn</a> 注册并登录。</li>
    <li>左侧「API 密钥」→「新建 API 密钥」，描述填「大肥鱼记忆」。</li>
    <li>点那一行的「密钥（点击复制）」，粘到这里。</li></ol>
    记忆使用 <code>BAAI/bge-m3</code> 模型，额度与费用以平台显示为准。`,
};

function keyField(id, label, help, type = "password", extra = "") {
  return `<label class="field" id="f-${id}">
    <span>${label} <em class="badge" id="b-${id}"></em></span>
    <input id="${id}" type="${type}" spellcheck="false" autocomplete="off" ${extra}>
    <div class="msg" id="m-${id}"></div>
  </label>
  ${help ? `<details class="help"><summary>${id === "owner_qq" ? "主人账号是什么？" : "如何获取？"}</summary>${help}</details>` : ""}`;
}

function setBadge(id, state, msg) {
  const b = $("#b-" + id), m = $("#m-" + id), f = $("#f-" + id);
  if (!b) return;
  b.className = "badge " + state;
  b.textContent = { ok: "✓ 可以用", bad: "✗ 不对", wait: "检查中…", "": "" }[state];
  m.className = "msg " + (state === "bad" ? "bad" : state === "ok" ? "ok" : "");
  m.textContent = msg || "";
  f.classList.toggle("bad", state === "bad");
}

function pageKeys(repair = false, returnTo = null) {
  const editing = S.mode === "status" || repair === true;
  if (editing) clearTimers();
  S.page = 1;
  const saved = (S.info.saved_keys || []);
  const official = S.info.platform === "official";
  const ph = (f, normal) => saved.includes(f) ? 'placeholder="已保存，不用改就留空"' : normal;
  shell(1, editing ? "修改凭证" : "填写接入凭证",
    editing ? "只修改需要更换的项目，已保存的密钥留空即可。运行配置保存在本机，请勿分享。"
      : (official ? "填写机器人凭证和 DeepSeek Key；安装后通过私聊绑定主人。" : "主人 QQ 和 DeepSeek Key 必填；语音和长期记忆可选。") + "部分密钥在输入完成后联网检查，运行配置保存在本机，请勿分享。", `
    ${official ? `<div class="note">请先把 QQ 平台的接收方式设为 <b>WebSocket</b>（下方「如何获取」有步骤）。安装后私聊绑定，无需提前填写 OpenID。</div>` : keyField("owner_qq", "你的大号 QQ", HELP.qq, "text", 'inputmode="numeric" placeholder="比如 123456789"')}
    ${official ? keyField("official_appid", "QQ 官方机器人 AppID", "", "text", ph("official_appid", "")) : ""}
    ${official ? keyField("official_secret", "QQ 官方机器人 AppSecret", HELP.officialApp, "password", ph("official_secret", "")) : ""}
    ${keyField("deepseek", "DeepSeek API key（她的脑子）", HELP.deepseek, "password", ph("deepseek", 'placeholder="sk-..."'))}
    <div class="section-label">更多能力 <span>可选，以后也能开启</span></div>
    <section class="option-card">
      <label class="option-toggle"><span><strong>语音与唱歌</strong><small>使用豆包语音服务，可能产生费用</small></span><input type="checkbox" id="voice_enabled" ${S.info.voice === "3" ? "checked" : ""}></label>
      <div class="option-fields" id="voice-fields" ${S.info.voice === "3" ? "" : "hidden"}>
    <div class="grid2">
      <label class="field" id="f-app_id"><span>豆包语音 APP ID <em class="badge" id="b-app_id"></em></span>
        <input id="app_id" type="text" spellcheck="false" autocomplete="off" ${ph("app_id", "")}><div class="msg" id="m-app_id"></div></label>
      <label class="field" id="f-token"><span>豆包语音 Access Token</span>
        <input id="token" type="password" spellcheck="false" autocomplete="off" ${ph("token", "")}><div class="msg" id="m-token"></div></label>
    </div>
    <details class="help"><summary>豆包语音去哪拿？（她发语音、唱歌用）</summary>${HELP.doubao}</details>
      </div>
    </section>
    <section class="option-card">
      <label class="option-toggle"><span><strong>长期记忆</strong><small>使用硅基流动，让她记住更多事情</small></span><input type="checkbox" id="memory_enabled" ${S.info.memory === "2" ? "checked" : ""}></label>
      <div class="option-fields" id="memory-fields" ${S.info.memory === "2" ? "" : "hidden"}>
    ${keyField("siliconflow", "硅基流动 API key（长期记忆）", HELP.siliconflow, "password", ph("siliconflow", ""))}
      </div>
    </section>
    <div id="msg" class="msg bad" role="alert"></div>
    ${editing ? "" : `<div id="mini">${miniProgress()}</div>`}`,
    `<button id="back">${editing ? "返回" : "上一步"}</button>
     <button class="primary" id="go" disabled>${editing ? "保存并重启她" : "下一步"}</button>`);

  // 留空保留已保存的 key，关闭功能不会删除凭证。
  const blankOk = (f) => saved.includes(f) && !$("#" + f).value.trim();
  const state = { owner_qq: official, deepseek: false, doubao: false, siliconflow: false };
  const refresh = () => ($("#go").disabled = !state.owner_qq || !(state.deepseek || blankOk("deepseek")) ||
    ($("#memory_enabled").checked && !(state.siliconflow || blankOk("siliconflow"))) ||
    ($("#voice_enabled").checked && !(state.doubao || (blankOk("app_id") && blankOk("token")))) ||
    (official && !((/^\d+$/.test($("#official_appid").value.trim()) || blankOk("official_appid")) &&
      ($("#official_secret").value.trim() || blankOk("official_secret")))));
  const val = (id) => $("#" + id).value.trim();

  const checkQQ = () => {
    const ok = official ? /^[A-Za-z0-9_-]{16,128}$/.test(val("owner_qq")) : /^\d{5,12}$/.test(val("owner_qq"));
    state.owner_qq = ok;
    setBadge("owner_qq", val("owner_qq") ? (ok ? "ok" : "bad") : "", ok || !val("owner_qq") ? "" :
      (official ? "请填写官方机器人私聊 OpenID" : "QQ 号只能是 5 到 12 位数字"));
    refresh();
  };
  const check = async (field) => {
    const badgeId = field === "doubao" ? "app_id" : field;
    const body = field === "doubao" ? { field, app_id: val("app_id"), token: val("token") } : { field, value: val(field) };
    if (field === "doubao" ? !(body.app_id || blankOk("app_id")) || !(body.token || blankOk("token")) : !body.value) {
      state[field] = false;
      setBadge(badgeId, "", "");
      return refresh();
    }
    state[field] = false;
    refresh();
    setBadge(badgeId, "wait", field === "doubao" ? "豆包要试念一句，十几秒…" : "");
    const originalInput = $("#" + badgeId);
    const r = await api("/api/check", body);
    if ($("#" + badgeId) !== originalInput || (field === "doubao"
      ? val("app_id") !== body.app_id || val("token") !== body.token : val(field) !== body.value)) return;
    state[field] = !!r.ok;
    setBadge(badgeId, r.ok ? "ok" : "bad", r.ok ? r.message : `本鱼检查饭票时遇到了问题：${r.message || "请检查后重试。"}`);
    refresh();
  };
  if (official) {
    $("#official_appid").oninput = refresh;
    $("#official_secret").oninput = refresh;
  }
  $("#memory_enabled").onchange = () => { $("#memory-fields").hidden = !$("#memory_enabled").checked; refresh(); };
  $("#voice_enabled").onchange = () => { $("#voice-fields").hidden = !$("#voice_enabled").checked; refresh(); };
  if (!official) {
    $("#owner_qq").oninput = checkQQ;
    $("#owner_qq").value = S.info.owner_qq || "";
    if (S.info.owner_qq) checkQQ();
  }
  ["deepseek", "siliconflow"].forEach((f) => {
    $("#" + f).oninput = () => { state[f] = false; setBadge(f, "", ""); refresh(); };
    $("#" + f).onchange = () => check(f);
  });
  ["app_id", "token"].forEach((f) => {
    $("#" + f).oninput = () => { state.doubao = false; setBadge("app_id", "", ""); refresh(); };
    $("#" + f).onchange = () => check("doubao");
  });
  $("#back").onclick = returnTo || (repair === true && S.mode === "install" ? pageTasks : editing ? pageStatus : pagePath);
  $("#go").onclick = async () => {
    $("#go").disabled = true;
    $("#go").innerHTML = '<span class="spin"></span> 确认中';
    const r = await api("/api/keys", { owner_qq: official ? (S.info.owner_qq || "") : val("owner_qq"), deepseek: val("deepseek"), siliconflow: val("siliconflow"),
      app_id: val("app_id"), token: val("token"),
      official_appid: official ? val("official_appid") : "", official_secret: official ? val("official_secret") : "",
      memory: $("#memory_enabled").checked ? "2" : "0", voice: $("#voice_enabled").checked ? "3" : "0" });
    if (r.ok) {
      S.info = await api("/api/info");
      return editing ? applySettings() : pageProfile();
    }
    $("#go").textContent = editing ? "保存并重启她" : "下一步";
    for (const [k, msg] of Object.entries(r.errors || {})) setBadge(k === "doubao" ? "app_id" : k, "bad", msg);
    $("#msg").textContent = r.message || (r.errors?.mode ? r.errors.mode : "本鱼没能保存，请检查上面的提示后重试。");
    refresh();
  };
}

// ---------------- 第 3 页：个性化 ----------------
const MEME_NAMES = { "blue-fat-fish-meme-pack": "蓝色大肥鱼（157 张）", "deepseek-fanart-meme-pack": "DeepSeek 二创（174 张）" };

function pageProfile() {
  S.page = 2;
  const p = S.info.profile;
  const presets = S.info.voice_presets.map((v, i) =>
    `<button class="chip${v.voice === p.voice ? " on" : ""}" data-i="${i}" type="button">${esc(v.name)}</button>`).join("");
  const memes = Object.entries(p.memes).map(([id, on]) =>
    `<label><input type="checkbox" data-meme="${esc(id)}" ${on ? "checked" : ""}> ${esc(MEME_NAMES[id] || id)}</label>`).join("");
  shell(2, "她是什么样的", "都有默认值，不想改直接点「下一步」。以后在「启动大肥鱼」里也能改。", `
    <label class="field"><span>名字</span><input id="name" type="text" maxlength="20" value="${esc(p.name)}"></label>
    <label class="field"><span>性格（直接用大白话改）</span><textarea id="persona" rows="7">${esc(p.persona)}</textarea></label>
    <div class="note" style="margin-top:-8px">里面的 <code>{OWNER_QQ}</code> 别删，安装时会换成所选接入方式的主人标识。</div>
    <label class="field"><span>声音（用一句话描述她的声音）</span>
      <div class="row"><input id="voice" type="text" value="${esc(p.voice)}"><button id="listen" type="button">▶ 试听</button></div>
      <div class="row" style="gap:8px">${presets}</div>
      <div class="msg" id="m-voice"></div></label>
    <audio id="player" hidden></audio>
    <div class="field"><span style="font-weight:600">表情包（至少留一套）</span><div class="checks">${memes}</div>
      <div class="msg">想加自己的图：装好后在 QQ 里直接发给她说「存一下」，或者在后台的表情包管理里上传。</div></div>
    <div id="msg" class="msg bad" role="alert"></div>
    ${S.mode === "status" ? "" : `<div id="mini">${miniProgress()}</div>`}`,
    `<button id="back">上一步</button><button class="primary" id="go">${S.mode === "status" ? "保存并重启她" : "下一步"}</button>`);

  document.querySelectorAll(".chip").forEach((b) => (b.onclick = () => {
    $("#voice").value = S.info.voice_presets[b.dataset.i].voice;
    document.querySelectorAll(".chip").forEach((x) => x.classList.toggle("on", x === b));
  }));
  $("#listen").disabled = S.info.voice !== "3";
  if (S.info.voice !== "3") $("#m-voice").textContent = "未启用豆包语音，可稍后在设置中开启。";
  $("#listen").onclick = async () => {
    $("#listen").disabled = true;
    $("#m-voice").className = "msg";
    $("#m-voice").textContent = "豆包在试念，请稍候；试听可能产生费用。";
    const r = await api("/api/preview_voice", { voice: $("#voice").value, name: $("#name").value });
    $("#listen").disabled = false;
    if (!r.ok) {
      $("#m-voice").className = "msg bad";
      $("#m-voice").textContent = r.message;
      return;
    }
    $("#m-voice").textContent = "";
    const a = $("#player");
    a.src = "data:audio/mpeg;base64," + r.audio_b64;
    a.play();
  };
  const collect = () => {
    const memes = {};
    document.querySelectorAll("[data-meme]").forEach((c) => (memes[c.dataset.meme] = c.checked));
    return { name: $("#name").value.trim(), persona: $("#persona").value, voice: $("#voice").value.trim(), memes };
  };
  $("#back").onclick = S.mode === "status" ? pageStatus : pageKeys;
  $("#go").onclick = async () => {
    const prof = collect();
    const r = await api("/api/profile", prof);
    if (!r.ok) return ($("#msg").textContent = r.message);
    S.info.profile = prof;
    S.mode === "status" ? applySettings() : pageTasks();
  };
}

// ---------------- 第 4 页：安装进度 ----------------
const ICON = { done: "✓", error: "!", doing: "", todo: "" };

function renderTasks() {
  const p = S.progress;
  const ul = $("#tasks");
  if (!ul || !p) return;
  ul.innerHTML = p.steps.filter((s) => s.key !== "login").map((s) => `
    <li class="${s.status}"><span class="ic">${ICON[s.status]}</span><div>${esc(s.title)}
      ${s.status === "error" ? `<div class="t-msg" style="white-space:pre-wrap">本鱼遇到一点状况，请看具体原因：\n${esc(s.message)}</div>` : ""}
      ${s.status === "doing" && s.key === "import" ? `<div class="t-wait">要装插件、写人格、接语音，几分钟</div>` : ""}
      ${s.status === "doing" && s.key === "extract" ? `<div class="t-wait">文件多，大概一两分钟</div>` : ""}
    </div></li>`).join("");
  const err = p.steps.some((s) => s.status === "error");
  $("#retry").hidden = !err;
  const login = p.steps.find((s) => s.key === "login");
  if (login && (login.status === "doing" || login.status === "done") && S.page === 3) {
    if (S.info.platform === "official") {
      if (login.status === "done") { S.page = 5; pageDone(); }
      else pageOfficialBind();
    } else pageQR();
  }
}

function pageTasks() {
  S.page = 3;
  shell(3, "正在安装", "正在后台准备；如果 Windows 显示安全提示，请核对程序来源后再决定是否允许。", `
    <ul class="tasks" id="tasks"></ul>
    <div class="note">关掉窗口也没关系，下次双击一键包、选同一个位置，会接着装。</div>`,
    `<button id="diagnose">检测与修复</button><button class="primary" id="retry" hidden>重试</button>`);
  $("#diagnose").onclick = () => pageDiagnostics(() => { pollProgress(); pageTasks(); });
  $("#retry").onclick = async () => {
    $("#retry").textContent = "别急别急，本鱼再扑腾两下！";
    const result = await api("/api/retry", {});
    if (result.ok) $("#retry").hidden = true;
    else $("#retry").textContent = result.message || "重试失败，请查看日志";
  };
  renderTasks();
}

// ---------------- 本机诊断和受限修复 ----------------
function pageRecovery() {
  clearTimers();
  S.page = -1;
  if (S.info.recovery?.busy) {
    const url = S.info.recovery.continue_url;
    shell(-1, "已有安装窗口", "同一个小窝只交给一个窗口管理，避免重复安装。", `
      <div class="note">${esc(S.info.recovery.message)}</div>
      ${url ? `<a class="button primary" href="${esc(url)}">打开已有窗口</a>` : ""}
      <div id="recover-result" class="msg" role="status"></div>`, `<button id="recheck">已关闭旧窗口，重新检查</button>`);
    $("#recheck").onclick = async () => {
      $("#recheck").disabled = true;
      const result = await api("/api/recover", {action:"recheck"});
      $("#recheck").disabled = false;
      S.info = await api("/api/info");
      if (result.ok && S.info.mode && S.info.mode !== "recovery") {
        S.mode = S.info.mode;
        return S.mode === "status" ? pageStatus() : pagePath();
      }
      if (S.info.recovery?.continue_url && !url) return pageRecovery();
      $("#recover-result").textContent = result.message || "已有窗口仍在运行，请在那里继续。";
    };
    return;
  }
  shell(-1, "需要恢复安装状态", "先恢复状态，再继续启动或修改配置。", `
    <div class="msg bad" role="alert">${esc(S.info.recovery?.message || "安装状态暂时无法读取。")}</div>
    <div class="note">原文件会保留。${S.info.recovery?.backup_available
      ? "已找到可用备份，可以恢复到上一次保存的状态。恢复后不会自动启动服务，请先确认运行状态。"
      : "未找到有效备份。请保留安装目录，检查文件权限或联系维护者；诊断仍可使用。"}</div>
    <div class="msg" id="recover-result" role="status"></div>`,
    `<button id="diagnose">查看诊断</button><button id="recheck">重新检查</button>
     <button class="primary" id="restore" ${S.info.recovery?.backup_available ? "" : "hidden"}>恢复状态备份</button>`,
    "窝里有点小状况，我们一起慢慢收拾好～");
  $("#diagnose").onclick = () => pageDiagnostics(pageRecovery);
  const recover = async (action) => {
    const ids = ["restore", "recheck", "diagnose"];
    ids.forEach(id => $("#" + id).disabled = true);
    $("#recover-result").textContent = "正在检查并恢复…";
    const result = await api("/api/recover", { action });
    S.info = await api("/api/info");
    if (result.ok && S.info.mode && S.info.mode !== "recovery") {
      S.mode = S.info.mode;
      return S.mode === "status" ? pageStatus() : pagePath();
    }
    ids.forEach(id => $("#" + id).disabled = false);
    $("#restore").hidden = !S.info.recovery?.backup_available;
    $("#recover-result").textContent = result.message || "暂时无法恢复，请保留原文件后联系维护者。";
  };
  $("#restore").onclick = () => recover("restore_backup");
  $("#recheck").onclick = () => recover("recheck");
}

async function pageDiagnostics(back) {
  clearTimers();
  S.page = -1;
  shell(-1, "帮本鱼检查一下", "正在检查安装文件，请稍候…", "", "", "让我检查一下小窝，别着急～");
  const report = await api("/api/diagnose");
  if (!Array.isArray(report.items)) {
    shell(-1, "检测失败", "", `<div class="msg bad">${esc(report.message || "无法读取诊断结果")}</div>`,
      `<button id="back">返回</button>`, "让我再检查一下小窝，别着急～");
    $("#back").onclick = back;
    return;
  }
  let repairable = report.items.some(x => ["astrbot_core_missing", "astrbot_path_missing"].includes(x.code));
  const items = report.items.map(x => `<li>${esc(x.message)} <small>(${esc(x.code)})</small></li>`).join("");
  const credentialsInvalid = report.items.some(x => x.code === "qq_credentials_invalid");
  const title = !items ? "检查完成，未发现已知故障" : repairable ? "发现可以修复的问题" : "有问题需要处理";
  const summary = !items ? "文件和最近启动日志未发现已知问题。这不代表 QQ 已成功收信；若没有回复，请返回检查 QQ 连接和平台的内部体验号码。"
    : repairable ? "点一次修复即可，程序会先暂停机器人，再补齐缺失文件。聊天记录和配置会保留。"
    : report.items[0].message;
  shell(-1, title, summary, `
    <div class="msg" id="diag-result" role="status" style="white-space:pre-wrap"></div>
    <details class="help"><summary>查看详细结果与其他帮助</summary>
      ${items ? `<ul class="bullets">${items}</ul>` : "<p>已检查安装文件、运行环境、配置和最近启动错误；QQ 收信需要在绑定页另外确认。</p>"}
      <p>自动修复只补齐同版本的核心文件和运行路径。其他问题请把检测结果交给维护者。</p>
      <button id="ai" ${report.items.length ? "" : "hidden"}>补充解释（DeepSeek）</button>
    </details>`,
    `<button id="back">返回</button><button class="primary" id="fix">${credentialsInvalid ? "修改机器人凭证" : repairable ? "一键修复" : "重新检查"}</button>`,
    "本鱼把能修的先修好，你不用研究一堆按钮啦！");
  $("#back").onclick = back;
  if (S.mode === "recovery") {
    $("#fix").hidden = true; $("#ai").hidden = true;
    $("#diag-result").textContent = "请先返回恢复安装状态，再继续操作。";
  }
  $("#fix").onclick = async () => {
    if (credentialsInvalid) return pageKeys(true);
    if (!repairable) return pageDiagnostics(back);
    const button = $("#fix"), result = $("#diag-result");
    button.disabled = true; $("#back").disabled = true; $("#ai").disabled = true;
    try {
      result.textContent = "正在准备修复…";
      const progress = await api("/api/progress");
      if (typeof progress.running !== "boolean") {
        result.textContent = progress.message || "无法确认安装状态，请重试。"; return;
      }
      if (progress.running) {
        result.textContent = progress.waiting === "official_bound"
          ? "现在正在等待绑定，请返回绑定页完成 QQ 设置并发送指令。"
          : "安装仍在进行，请等完成或失败后再修复。";
        return;
      }
      result.textContent = "正在暂停机器人；如弹出 Windows 授权，请按提示操作…";
      const stopped = await api("/api/stop", {});
      if (!stopped.ok) { result.textContent = stopped.message || "无法暂停机器人，尚未进行修复。"; return; }
      result.textContent = "正在补齐安装文件，可能需要几分钟…";
      const r = await api("/api/repair", {});
      if (r.ok) { repairable = false; button.textContent = "重新检查"; }
      result.textContent = r.ok && Array.isArray(r.report?.items)
        ? `修复步骤已完成。${r.report.items.length ? "还有提示，请展开详细结果或联系维护者。\n" + r.report.items.map(x => x.message).join("\n") : "未发现其他已知文件问题。"}\n请返回后${S.mode === "status" ? "启动机器人" : "点击重试安装"}；历史启动错误会在下次启动后更新。`
        : (r.message || "修复没有完成，请重试或联系维护者。");
    } finally { button.disabled = false; $("#back").disabled = false; $("#ai").disabled = false; }
  };
  $("#ai").onclick = async () => {
    const yes = await confirmBox({
      title: "发送诊断码给 DeepSeek",
      body: `<p>将使用你保存的 DeepSeek Key，向 DeepSeek 发送以下固定诊断码：<br><code>${esc(report.items.map(x => x.code).join(", ") || "无")}</code>。</p>
        <p>如果日志显示缺少 Python 模块，也会发送该模块名。不会发送原始日志、聊天记录、路径或其他配置。请求可能产生少量 API 费用；模型只给建议，不会执行命令。</p>`,
      ok: "同意并分析",
    });
    if (!yes) return;
    $("#ai").disabled = true;
    $("#diag-result").textContent = "正在分析…";
    const r = await api("/api/ai_diagnose", {consent: true});
    $("#ai").disabled = false;
    $("#diag-result").textContent = r.ok ? r.advice : (r.message || "分析失败");
  };
}

// ---------------- 第 5 页：官方机器人绑定 ----------------
function pageOfficialBind() {
  clearTimers();
  S.page = 4;
  shell(4, "绑定主人", "复制指令 → 用你的 QQ 私聊机器人 → 回来检查结果。", `
    <div class="note">QQ 平台的接收方式需选 <b>WebSocket</b>。绑定前不会开启普通聊天，请保管好指令。</div>
    <div class="status-card">
      <label for="bind-code"><strong>发送给机器人的指令</strong></label>
      <div class="bind-command"><input id="bind-code" type="text" readonly spellcheck="false" autocomplete="off" placeholder="正在获取…">
        <button id="bind-copy" disabled>复制指令</button></div>
      <div id="bind-copy-result" class="msg" role="status"></div>
      <p id="bind-hint">请发送整行文字。指令 10 分钟有效，过期会自动更新。</p>

      <div id="bind-message" role="status">正在检查连接…</div>
    </div>
    <details class="help" id="bind-help"><summary>已经发送，为什么没收到？</summary>
      ${OFFICIAL_CONNECTION_HELP}<button id="bind-diagnose">检查安装文件</button>
    </details>`,
    `<button id="bind-back">修改机器人凭证</button><button id="bind-retry">重新连接</button>
      <button id="bind-sent" class="primary" disabled>我已发送，检查结果</button>
      <button id="bind-confirm" class="primary" disabled hidden>是我发的，完成绑定</button>`,
    "快私聊本鱼发送绑定指令，认准主人后就能一起玩啦！");
  const anchor = $("#bind-code"), button = $("#bind-confirm"), message = $("#bind-message");
  const retry = $("#bind-retry"), copy = $("#bind-copy"), sent = $("#bind-sent"), help = $("#bind-help");
  let busy = false, confirming = false, candidateId = "", code = "", checked = false, bound = false, renewed = false;
  $("#bind-back").onclick = () => pageKeys(true, pageOfficialBind);
  $("#bind-diagnose").onclick = () => pageDiagnostics(pageOfficialBind);
  copy.onclick = async () => {
    if (!code) return;
    renewed = false;
    await copyField(anchor, $("#bind-copy-result"), "已复制！去 QQ 私聊机器人粘贴发送，再回来检查。");
  };
  retry.onclick = () => applySettings("reconnect");
  button.onclick = async () => {
    if (confirming || !candidateId) return;
    confirming = true; button.disabled = true; sent.disabled = true;
    const r = await api("/api/official_bind/confirm", {candidate_id: candidateId});
    if ($("#bind-code") !== anchor) return;
    confirming = false;
    if (r.ok) return pageBindingFinish();
    message.textContent = r.message || "绑定未完成，请重试。";
    if (!r.ok) { button.disabled = false; sent.disabled = false; }
  };
  const tick = async (manual = false) => {
    if (busy || confirming || $("#bind-code") !== anchor) return;
    busy = true; sent.disabled = true;
    if (manual) { sent.textContent = "正在检查…"; message.textContent = "本鱼正在检查有没有收到你的指令…"; }
    try {
      const r = await api("/api/official_bind" + (manual ? "?check=1" : ""));
      if ($("#bind-code") !== anchor) return;
      if (!r.ok) {
        candidateId = ""; code = ""; anchor.value = ""; anchor.placeholder = "暂时无法获取绑定指令";
        message.textContent = r.message || "无法获取绑定状态，请点击检查重试。";
        message.className = "msg bad"; button.disabled = true; button.hidden = true; copy.disabled = true; sent.hidden = true; return;
      }
      candidateId = r.candidate_id || ""; bound = !!r.bound;
      if (bound) return pageBindingFinish();
      if (code && r.code && code !== r.code) { renewed = true; $("#bind-copy-result").textContent = ""; }
      const credentialsInvalid = r.issue_code === "qq_credentials_invalid";
      $("#bind-back").className = credentialsInvalid ? "primary" : "";
      retry.hidden = credentialsInvalid;
      $("#bind-hint").textContent = credentialsInvalid ? "请先修正凭证，验证通过后再继续私聊绑定。"
        : "请发送整行文字。指令 10 分钟有效，过期会自动更新。";
      code = credentialsInvalid ? "" : r.code || "";
      anchor.value = code; anchor.placeholder = credentialsInvalid ? "先修正凭证，再获取指令" : "正在获取…";
      copy.disabled = !code || bound;
      button.disabled = !r.candidate || !candidateId || bound;
      button.hidden = button.disabled;
      sent.hidden = !button.disabled || r.issue_code === "qq_credentials_invalid";
      message.className = "msg";
      if (r.candidate && candidateId) {
        message.textContent = "本鱼收到绑定指令啦！如果是你刚才发送的，请点「是我发的，完成绑定」。";
        message.className = "msg ok";
      } else {
        const receipts = {
          wrong_code: "收到绑定指令，但和当前指令不匹配。请重新复制上方整行文字再发送。",
          not_command: "收到私聊消息，但不是完整的绑定指令。请点「复制指令」，把整行文字发给机器人。",
          expired: "刚收到的指令已过期，请复制上方最新指令再发送。",
          invalid_sender: "收到指令，但 QQ 没有提供可用的私聊身份。请确认是在消息列表私聊机器人，并联系维护者。",
          write_error: "收到指令，但没能保存绑定结果。请检查安装目录是否可写，再发送一次。",
          received: "已收到指令，正在读取绑定结果，请稍候…",
        };
        const connection = {
          ready: checked ? "还没收到你的私聊消息。请展开下方帮助，确认 QQ 平台选了 WebSocket，并添加了你的内部体验号码，再重新发送。"
            : "QQ 连接已建立。发送指令后，点「我已发送，检查结果」。",
          starting: "正在连接 QQ，请稍候再发送指令。",
          disconnected: "QQ 连接已断开，请检查网络和平台设置；恢复连接后再发送指令。",
          unknown: "暂时无法确认 QQ 连接，请稍后再检查；仍无变化时请展开下方帮助。",
        };
        message.textContent = r.problem || receipts[r.receipt] || (renewed ? "绑定指令已更新，请重新复制上方指令并发送。" : connection[r.connection]) || r.message || "正在等待私聊指令…";
        if (r.problem || ["wrong_code", "not_command", "expired", "invalid_sender", "write_error"].includes(r.receipt)) message.className = "msg bad";
        if (checked && (!r.receipt || r.receipt === "not_received")) help.open = true;
      }
    } finally {
      busy = false;
      if ($("#bind-code") === anchor) { sent.disabled = !code || bound || confirming; sent.textContent = "我已发送，检查结果"; }
    }
  };
  sent.onclick = () => { checked = true; return tick(true); };
  tick();
  S.timers.push(setInterval(() => tick(), 1500));
}

function pageBindingFinish() {
  clearTimers();
  S.page = 4;
  const generation = S.timerGeneration, started = Date.now();
  shell(4, "认主成功，正在收尾", "主人已经记住了，不用再发送指令。完成后会自动进入下一页。", `
    <div class="finishing" role="status" aria-live="polite">
      <img class="finishing-avatar" src="/img/hug.jpg" alt="正在整理小窝的大肥鱼">
      <h2>本鱼正在把主人写进小本本…</h2>
      <div class="working-bar" aria-hidden="true"></div>
      <p id="finish-message">正在保存主人资料…</p>
      <small id="finish-time">已等待 0 秒</small>
    </div><div id="finish-error" class="msg bad" role="alert"></div>`,
    `<button id="finish-help">遇到问题</button><button id="finish-retry" class="primary" hidden>重试收尾</button>`,
    "认准主人啦！本鱼收好小本本，再检查一下 QQ 的门开了没有～");
  $("#finish-help").onclick = () => pageDiagnostics(pageBindingFinish);
  $("#finish-retry").onclick = async () => {
    $("#finish-retry").disabled = true;
    const result = await api("/api/retry", {});
    if (generation !== S.timerGeneration) return;
    $("#finish-retry").disabled = false;
    if (result.ok) { $("#finish-retry").hidden = true; tick(); }
    else $("#finish-error").textContent = result.message || "重试未完成";
  };
  let busy = false;
  const tick = async () => {
    if (busy) return;
    busy = true;
    try {
      const progress = await api("/api/progress");
      if (generation !== S.timerGeneration) return;
      const login = progress.steps?.find(x => x.key === "login");
      if (login?.status === "done") return pageDone();
      $("#finish-time").textContent = `已等待 ${Math.floor((Date.now() - started) / 1000)} 秒`;
      $("#finish-message").textContent = login?.status === "doing" ? login.message || "正在保存资料并确认 QQ 连接…" : "正在准备收尾…";
      $("#finish-error").textContent = login?.status === "error" ? login.message : progress.message || "";
      $("#finish-retry").hidden = login?.status !== "error";
    } finally { busy = false; }
  };
  tick();
  S.timers.push(setInterval(tick, 1000));
}

// ---------------- 第 5 页：扫码 ----------------
const LOGIN_TEXT = {
  starting: ["正在准备二维码…", "wait"],
  need_scan: ["用登着小号的手机 QQ 扫一下", "wait"],
  scanned: ["扫到了，在手机上点「确认登录」", "wait"],
  online: ["已登录，小号的大门打开啦！", "on"],
  offline: ["小号掉线了，等一下会出新的二维码", "off"],
  error: ["二维码已失效，请重新获取", "off"],
};

function qrBlock() {
  return `<div class="qr">
    <div class="frame"><img id="qr" alt="QQ 登录二维码" hidden><div id="qrwait" class="qr-wait"><span class="spin" id="qrspinner"></span><span id="qrwaittext">正在准备二维码</span></div></div>
    <div class="status-big"><span class="dot wait" id="dot"></span><span id="qrstate" role="status">正在准备二维码…</span></div>
    <div><button id="reqr">刷新二维码</button></div>
    <div class="msg" id="qrmsg">请用 QQ 小号扫码；如果加载时间较长，可尝试刷新。</div>
  </div>`;
}

function makeQRController() {
  const btn = $("#reqr"), img = $("#qr"), wait = $("#qrwait"), spinner = $("#qrspinner"), waitText = $("#qrwaittext"), state = $("#qrstate"), dot = $("#dot"), msg = $("#qrmsg");
  let latest = "", blocked = null, loaded = "", loading = "", generation = 0, refreshing = false;
  const active = () => $("#qr") === img;
  const hide = () => { img.hidden = true; wait.hidden = false; };
  btn.onclick = async () => {
    if (refreshing) return;
    refreshing = true;
    blocked = latest;
    generation++;
    loaded = loading = "";
    hide();
    spinner.hidden = false;
    waitText.textContent = "正在刷新二维码";
    btn.disabled = true;
    btn.textContent = "正在刷新…";
    state.textContent = "正在获取新二维码，请稍候…";
    const r = await api("/api/refresh_qr", {});
    if (!active()) return;
    refreshing = false;
    btn.disabled = false;
    btn.textContent = "刷新二维码";
    msg.textContent = r.ok ? "正在等待新二维码，请勿扫描之前的二维码。" : (r.message || "刷新失败，请重试。");
  };
  return { update(r) {
    if (!active()) return;
    const status = r.status || "starting";
    const revision = r.qr_revision || "";
    latest = revision || latest;
    if (status === "error") blocked = latest;
    const [text, color] = LOGIN_TEXT[status] || LOGIN_TEXT.starting;
    state.textContent = refreshing ? "正在获取新二维码，请稍候…" : text;
    dot.className = "dot " + color;
    spinner.hidden = status === "error" && !refreshing;
    waitText.textContent = refreshing ? "正在刷新二维码" : status === "error" ? "二维码已失效" :
      status === "scanned" ? "已扫码，请在手机上确认" : "正在准备二维码";
    if (r.error) msg.textContent = "NapCat：" + r.error + "。请尝试刷新二维码。";
    if (status !== "need_scan" || !revision || revision === blocked || refreshing) {
      generation++;
      loading = "";
      hide();
      if (status === "online") wait.hidden = true;
      return;
    }
    if (revision === loaded) { img.hidden = false; wait.hidden = true; return; }
    if (revision === loading) return;
    hide();
    loading = revision;
    const request = ++generation;
    const fresh = new Image();
    fresh.onload = () => {
      if (!active() || request !== generation) return;
      img.src = fresh.src;
      img.hidden = false;
      wait.hidden = true;
      loaded = revision;
      loading = "";
      msg.textContent = "请用 QQ 小号扫描上方二维码。";
    };
    fresh.onerror = () => {
      if (!active() || request !== generation) return;
      loading = "";
      hide();
      msg.textContent = "二维码没搬过来，本鱼正在重试；也可以点击刷新。";
    };
    fresh.src = `/api/qr?token=${encodeURIComponent(window.TOKEN)}&rev=${encodeURIComponent(revision)}`;
  } };
}

function pollLogin(onOnline) {
  const qr = makeQRController();
  let busy = false;
  const tick = async () => {
    if (busy) return;
    busy = true;
    try {
      const r = await api("/api/login");
      if (!$("#qrstate")) return;
      qr.update(r);
      if (r.status === "online") onOnline();
    } finally { busy = false; }
  };
  tick();
  S.timers.push(setInterval(tick, 5000));
}

function pageQR() {
  S.page = 4;
  shell(4, "扫码登录小号", "用<b>小号</b>扫，不是你自己的大号。扫完在手机上确认。", qrBlock(), "");
  let done = false;
  pollLogin(() => {
    if (done) return;
    done = true;
    clearTimers();
    pollProgress();
    setTimeout(pageDone, 800);
  });
}

// 状态页里的重新扫码：登录好了就回主页，不像安装时往下走
function pageRescan() {
  clearTimers();
  S.page = -1;
  shell(-1, "扫码登录小号", "用<b>小号</b>扫，不是你自己的大号。扫完在手机上确认。", qrBlock(),
    `<button id="back">返回</button><span></span>`, "换好小号，我们再一起出发～");
  $("#back").onclick = pageStatus;
  let done = false;
  pollLogin(() => {
    if (done) return;
    done = true;
    clearTimers();
    setTimeout(pageStatus, 800);
  });
}

// ---------------- 第 6 页：完成 ----------------
async function pageDone() {
  S.page = 5;
  clearTimers();
  const d = await api("/api/done");
  if (!d.dashboard_url || !d.username || !d.password) {
    shell(5, "安装已收尾", "后台信息暂时没有读到，可以重试获取。", `<div class="msg bad">${esc(d.message || "无法获取后台信息")}</div>`, `<button id="done-retry">重新获取</button>`);
    $("#done-retry").onclick = pageDone;
    return;
  }
  const fields = [["address", "后台地址", d.dashboard_url], ["username", "后台账号", d.username], ["password", "后台密码", d.password]];
  const name = S.info.profile?.name || "大肥鱼";
  shell(5, `${esc(name)}已配置`, S.info.platform === "official"
    ? "主人绑定已完成。用刚才绑定的 QQ 私聊机器人，说句「在吗」试试。"
    : "用你的大号私聊小号，说句「在吗」试试。", `
    <div class="note">桌面放了一个「启动大肥鱼」。电脑重启后双击它，可以重新启动服务。<br>
    电脑睡眠或关机时她就下线了，笔记本记得关掉自动睡眠。</div>
    <dl class="info credentials">${fields.map(([id, label, value]) => `<dt><label for="done-${id}">${label}</label></dt>
      <dd><input id="done-${id}" type="${id === "password" ? "password" : "text"}" value="${esc(value)}" readonly autocomplete="off">
        <button id="copy-${id}" aria-label="复制${label}">复制</button></dd>`).join("")}</dl>
    <div id="done-copy-result" class="msg" role="status"></div>
    <div class="msg">这组账号密码也存进了安装目录里的「我的信息.txt」。别截图发群。</div>`,
    `<a href="${esc(d.dashboard_url)}" target="_blank"><button type="button">打开后台</button></a><button class="primary" id="fin">完成</button>`);
  fields.forEach(([id, label]) => {
    $("#copy-" + id).onclick = () => copyField($("#done-" + id), $("#done-copy-result"), `${label}已复制`);
  });
  $("#fin").onclick = pageStatus;
}

// ---------------- 状态页（桌面快捷方式打开的） ----------------
function statusView(s, official) {
  if (typeof s.astrbot !== "boolean" || typeof s.napcat !== "boolean")
    return { text: "暂时无法获取状态，正在重试…", dot: "wait", known: false, canAutoStart: false };
  const running = s.astrbot && (official || s.napcat);
  let text, dot;
  if (s.starting) [text, dot] = ["正在启动，请稍候…", "wait"];
  else if (!official && s.login === "online" && !s.napcat) [text, dot] = ["QQ 已连接，正在确认服务状态", "wait"];
  else if (!running && (s.astrbot || s.napcat)) [text, dot] = ["部分服务未运行", "wait"];
  else if (!running) [text, dot] = ["她下线了", "off"];
  else if (official && s.connection_error) [text, dot] = ["QQ 连接失败", "off"];
  else if (official && s.qq_connection === "ready") [text, dot] = ["QQ 已连接，可以聊天了", "on"];
  else if (official && s.qq_connection === "disconnected") [text, dot] = ["QQ 连接中断", "wait"];
  else if (official) [text, dot] = ["程序已启动，正在确认 QQ 连接", "wait"];
  else if (s.login === "online") [text, dot] = [`在线${s.qq ? "（小号 " + s.qq + "）" : ""}`, "on"];
  else if (["need_scan", "scanned", "error"].includes(s.login)) [text, dot] = ["需要扫码登录", "wait"];
  else if (s.login === "offline") [text, dot] = ["QQ 已断开，等待重新连接", "wait"];
  else [text, dot] = ["正在确认 QQ 连接…", "wait"];
  return { text, dot, known: true, running, reconnect: official && running && !s.starting && (s.qq_connection !== "ready" || !!s.connection_error), canAutoStart: !s.stopped && !s.astrbot && !s.napcat && !s.starting && s.login !== "online" && !s.error };
}

async function pageStatus() {
  clearTimers();
  S.page = -1;
  const name = S.info.profile?.name || "大肥鱼";
  shell(-1, esc(name), "她住在你电脑里。关掉这个窗口不影响她在线。", `
    <div class="status-card"><div class="status-big" role="status"><span class="dot" id="sdot"></span><span id="stext">正在检查状态…</span></div>
      <p>「暂停聊天」只停止机器人，保留本窗口；「全部停止并退出」会同时关闭控制程序。直接关窗口会让机器人继续运行。</p>
      <div class="acts"><button class="primary big" id="up" disabled>让她上线</button><button class="big" id="down" disabled>暂停聊天</button><button id="quit">全部停止并退出</button></div></div>
    <div id="sqr" hidden>${qrBlock()}</div>
    <div id="serr" class="msg bad" role="alert"></div>
    <div class="group"><h3>遇到问题</h3><div class="acts">
      <button id="diagnose">检测与修复</button>
    </div></div>
    <div class="group"><h3>改设置</h3><div class="acts">
      <button id="edit-prof">改名字 / 性格 / 声音</button>
      <button id="edit-keys">改 key</button>
      <button id="switch-qq" ${S.info.platform === "official" ? "hidden" : ""}>退出小号 / 换一个</button>
    </div></div>
    <div class="group"><h3>移除程序</h3><div class="acts">
      <button class="danger-ghost" id="uninstall">彻底卸载</button>
    </div>
    <div class="msg">卸载会删除本安装目录内的文件；暂时不用时选择上方「全部停止并退出」。</div></div>`, "");
  let actionPending = false, actionError = "", revision = 0, reconnect = false;
  const act = async (path, text) => {
    if (actionPending) return { ok: false, message: "请等待当前操作完成" };
    actionPending = true;
    revision++;
    actionError = "";
    const controls = ["up", "down", "diagnose", "edit-keys", "edit-prof", "switch-qq", "quit", "uninstall"];
    controls.forEach(id => $("#" + id).disabled = true);
    $("#stext").textContent = text;
    const result = await api(path, {});
    actionPending = false;
    if ($("#stext") !== anchor) return result;
    controls.forEach(id => $("#" + id).disabled = false);
    actionError = result.ok ? "" : result.message || "操作失败，请重试";
    $("#serr").textContent = actionError;
    return result;
  };
  $("#up").onclick = async () => {
    if (reconnect && !(await act("/api/stop", "正在重新连接前停止服务…")).ok) return tick();
    await act("/api/start", "正在启动并连接 QQ…"); tick();
  };
  $("#down").onclick = async () => { await act("/api/stop", "正在停止后台；如弹出 Windows 授权，请按提示操作…"); tick(); };
  $("#diagnose").onclick = () => pageDiagnostics(pageStatus);
  $("#edit-keys").onclick = () => { clearTimers(); pageKeys(); };
  $("#edit-prof").onclick = () => { clearTimers(); pageProfile(); };
  $("#switch-qq").onclick = async () => {
    const yes = await confirmBox({
      title: "退出小号 / 换一个",
      body: `<p>会停止当前 QQ 服务、忘记本安装记住的小号，然后准备新的二维码。</p>
        <div class="note">想换号就用另一个小号扫；不想再用了，出码后直接点「让她下线」就行。<br>
        QQ 客户端的登录记录和聊天数据会保留；若客户端仍自动登录原账号，请在 QQ 中手动退出后再扫码。<br>
        名字、性格、聊天记忆和表情包都留着。</div>`,
      ok: "退出登录",
    });
    if (!yes) return;
    if (!(await act("/api/switch_qq", "正在退出小号；如弹出 Windows 授权，请按提示操作…")).ok) return tick();
    if (!(await act("/api/start", "正在准备新二维码…")).ok) return tick();
    pageRescan();
  };
  $("#quit").onclick = async () => {
    const yes = await confirmBox({
      title: "全部停止并退出",
      body: `<p>她会下线，后台也一起关掉，不再占电脑资源。</p>
        <div class="note">什么都不会删。想让她回来，双击桌面的「启动大肥鱼」。</div>`,
      ok: "全部停止并退出",
    });
    if (!yes) return;
    if (!(await act("/api/quit", "正在停止并退出；如弹出 Windows 授权，请按提示操作…")).ok) return tick();
    clearTimers();
    shell(-1, "她下线了", "已经全部停掉。", `
      <div class="note">想让她回来，双击桌面的「启动大肥鱼」，再点「让她上线」。这个窗口可以关了。</div>`,
      `<span></span><button class="primary" id="fin">关闭窗口</button>`, "我先睡一小会儿，等你叫我再一起玩～");
    $("#fin").onclick = () => window.close();
  };
  $("#uninstall").onclick = async () => {
    const yes = await confirmBox({
      title: "彻底卸载",
      danger: true,
      need: "卸载",
      body: `<p>会删掉下面这些，<b>删了找不回来</b>：</p>
        <ul class="bullets">
          <li>安装目录 <code>${esc(S.info.root || "")}</code> 里的全部文件</li>
          <li>她的聊天记忆、表情包、你填的 key</li>
          <li>指向本安装的桌面图标及可确认归属的窗口缓存</li>
        </ul>
        <div class="note">QQ 客户端、QQ 登录记录、聊天数据及其他安装会保留。无法确认归属的旧版窗口缓存也会保留。<br>
        只是暂时不用的话，点上一个「全部停止并退出」就够了。</div>`,
      ok: "我确定，卸载",
    });
    if (!yes) return;
    const r = await act("/api/uninstall", "正在停止服务；如弹出 Windows 授权，请按提示操作…");
    if (!r.ok) return tick();
    clearTimers();
    shell(-1, "正在卸载", "服务已停止，正在清理文件。", `
      <div class="note">窗口将自动关闭。清理结果会记在：<br><code>${esc(r.log || "")}</code><br>
      想再装回来，重新双击一键包。</div>`,
      `<span></span><button class="primary" id="fin">关闭窗口</button>`, "行李已经收好啦，谢谢你陪我玩～");
    $("#fin").onclick = () => window.close();
  };
  const anchor = $("#stext"), qr = makeQRController();
  let busy = false, initial = !S.statusVisited;
  S.statusVisited = true;
  const tick = async () => {
    if (busy || actionPending) return;
    busy = true;
    const observed = revision;
    try {
      const s = await api("/api/status");
      if ($("#stext") !== anchor || actionPending || observed !== revision) return;
      const official = S.info.platform === "official", view = statusView(s, official);
      anchor.textContent = view.text;
      $("#sdot").className = "dot " + view.dot;
      if (!view.known) {
        $("#serr").textContent = s.message || "状态服务暂时不可用，请稍后重试。";
        $("#up").disabled = !!s.permission_denied;
        $("#down").disabled = false;
        return;
      }
      reconnect = !!view.reconnect;
      $("#up").textContent = reconnect ? "重新连接" : "让她上线";
      $("#serr").textContent = actionError || s.error || s.connection_error || "";
      $("#up").disabled = view.running && !reconnect || s.starting || s.login === "online" && !s.napcat;
      $("#down").disabled = !s.astrbot && !s.napcat && !s.starting;
      const needQR = !official && ["need_scan", "scanned", "error"].includes(s.login);
      $("#sqr").hidden = !needQR;
      if (needQR) qr.update({ status: s.login, error: s.login_error, qr_revision: s.qr_revision });
      if (initial) {
        initial = false;
        if (view.canAutoStart) await api("/api/start", {});
      }
    } finally { busy = false; }
  };
  tick();
  S.timers.push(setInterval(tick, 3000));
}

// 改完设置：原地等重启，不要把人甩回安装向导
async function applySettings(action = "apply") {
  clearTimers();
  S.page = -1;
  shell(-1, action === "reconnect" ? "正在重新连接" : "正在应用改动",
    action === "reconnect" ? "正在重启连接，已安装的文件和聊天资料会保留。如弹出 Windows 授权，请按提示操作。"
      : "在重写配置、重启她。如弹出 Windows 授权，请按提示操作。", `
    <ul class="tasks" id="tasks"></ul>
    <div class="note">完成后会自动回到下一步；还没认主的会继续绑定。</div>
    <div id="msg" class="msg bad" role="alert"></div>`,
    `<button id="apply-back" disabled>返回</button><button class="primary" id="retry" hidden>重试</button>`, "我去整理一下小窝，等会儿就回来陪你～");
  $("#apply-back").onclick = S.info.platform === "official" ? pageOfficialBind : S.mode === "install" ? pageTasks : pageStatus;
  $("#retry").onclick = async () => { await api("/api/retry", {}); $("#retry").hidden = true; };
  const generation = S.timerGeneration;
  const result = await api(action === "reconnect" ? "/api/reconnect" : "/api/apply", {});
  if (generation !== S.timerGeneration) return;
  $("#apply-back").disabled = false;
  if (!result.ok) {
    const title = $(".main h1");
    if (title) title.textContent = action === "reconnect" ? "重新连接未完成" : "应用改动未完成";
    $("#msg").textContent = result.message || "应用改动失败，请重试。";
    $("#retry").hidden = false;
    $("#retry").onclick = () => applySettings(action);
    return;
  }
  const tick = async () => {
    const progress = await api("/api/progress");
    if (generation !== S.timerGeneration || !$("#tasks")) return;
    S.progress = progress;
    renderApplying();
    if (S.progress.finished) return clearTimers(), S.mode === "install" ? pageDone() : pageStatus();
    if (S.progress.waiting === "official_bound") return pageOfficialBind();
    // 改完 key 后登录可能失效。只有真的要扫码才跳过去：登录还在的话这一步瞬间就过了，
    // 光看 status === "doing" 会白闪一下二维码页
    const login = S.progress.steps?.find((s) => s.key === "login");
    if (login && login.status === "doing") {
      const l = await api("/api/login");
      if (["need_scan", "scanned", "error"].includes(l.status)) { clearTimers(); pageRescan(); }
    }
  };
  tick();
  S.timers.push(setInterval(tick, 1500));
}

function renderApplying() {
  const p = S.progress;
  const ul = $("#tasks");
  if (!ul || !p) return;
  // 只列这次重跑的几步，解压之类早就做完的不用再刷一遍
  const keys = ["configs", "astrbot", "napcat", "import", "login"];
  ul.innerHTML = p.steps.filter((s) => keys.includes(s.key)).map((s) => `
    <li class="${s.status}"><span class="ic">${ICON[s.status]}</span><div>${esc(s.title)}
      ${s.status === "error" ? `<div class="t-msg" style="white-space:pre-wrap">${esc(s.message)}</div>` : ""}
    </div></li>`).join("");
  $("#retry").hidden = !p.steps.some((s) => s.status === "error");
}

// ---------------- 入口 ----------------
(async () => {
  S.info = await api("/api/info");
  if (!S.info || !S.info.mode) {
    $("#app").innerHTML = `<div class="shell"><main class="main"><h1>出错了</h1><p class="sub" style="white-space:pre-wrap">${esc(S.info?.message || "打不开安装程序")}</p></main></div>`;
    return;
  }
  S.mode = S.info.mode;
  if (S.mode === "recovery") pageRecovery();
  else if (S.mode === "status") pageStatus();
  else pagePath();
})();

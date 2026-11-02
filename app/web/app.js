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
  try {
    const r = await fetch(path, opt);
    return await r.json();
  } catch (e) {
    return { ok: false, message: "和安装程序断开了，关掉窗口重新双击试试" };
  }
}

setInterval(() => api("/api/ping", {}), 3000);
api("/api/ping", {});

const PAGES = ["选位置", "填 key", "个性化", "安装", "扫码", "完成"];
const PICS = ["wave", "smile", "wink", "hug", "night", "wave"];
const SAYS = [
  "嗨，我是大肥鱼。帮我在你电脑上找个窝吧",
  "这几把钥匙是我的饭票，别给别人看哦",
  "想让我变成什么样子都行，不改也可以",
  "在搬家啦，等我一下下",
  "用你的 QQ 小号扫一下，我就住进去了",
  "搞定！快去 QQ 找我玩",
];
// 状态页不是安装流程，别让她继续说「帮我找个窝」
const STATUS_SAY = "有事就点下面的按钮，我在这儿";

const S = { page: 0, info: null, progress: null, timers: [] };

function clearTimers() {
  S.timers.forEach(clearInterval);
  S.timers = [];
}

function shell(page, title, sub, bodyHtml, footHtml) {
  const steps = PAGES.map((n, i) =>
    `<li class="${i === page ? "on" : i < page ? "past" : ""}"><span class="n">${i < page ? "✓" : i + 1}</span>${n}</li>`).join("");
  const name = S.info?.profile?.name || "大肥鱼";
  $("#app").innerHTML = `
  <div class="shell">
    <aside class="side">
      <div class="brand">${esc(name)}一键包<small>把 DeepSeek 养进 QQ</small></div>
      <img class="avatar" src="/img/${PICS[Math.max(page, 0)]}.jpg" alt="">
      <div class="bubble">${esc((page < 0 ? STATUS_SAY : SAYS[Math.max(page, 0)]).replace("大肥鱼", name))}</div>
      ${page >= 0 && S.mode === "install" ? `<ol class="steps">${steps}</ol>` : ""}
    </aside>
    <main class="main">
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
  const tick = async () => {
    S.progress = await api("/api/progress");
    const el = $("#mini");
    if (el) el.innerHTML = miniProgress();
    if (S.page === 3) renderTasks();
  };
  tick();
  S.timers.push(setInterval(tick, 1500));
}

// ---------------- 第 1 页：位置 ----------------
function pagePath() {
  S.page = 0;
  shell(0, "欢迎", "装好以后，她就住在你的 QQ 小号里，用 DeepSeek 当脑子，会发表情包、发语音、记得你说过的事。", `
    <div class="warn"><b>请用 QQ 小号，别用常用的号。</b>用第三方程序登 QQ 有被限制功能或封号的风险，新号更容易被风控掉线。</div>
    <div class="note"><b>关于本包内容与授权</b><br>
      本包为非商业的整合安装工具，内含以下第三方组件，各自版权与许可归原作者所有：
      <br>· <b>AstrBot</b>（AGPL-3.0）· <b>NapCat</b>（QQ 协议适配）· <b>Python</b>（PSF）· <b>大肥鱼配置包</b>（MIT）
      <br>内置的人设文字、立绘与表情包素材，版权属于各自的创作者，仅供个人学习与自用。
      <b>请勿将本包或其中素材用于商业用途、二次售卖或再分发。</b>
      如你是素材权利人并希望移除，请联系整合包作者。
      <br>各组件完整许可证见安装目录下的 <code>pack\\LICENSE</code> 与 <code>astrbot\\LICENSE</code>。
      继续安装即表示你已知悉并同意上述条款，并自行承担使用第三方 QQ 客户端的账号风险。
    </div>
    <label class="field">
      <span>装在哪</span>
      <input id="path" type="text" value="${esc(S.info.default_path)}" spellcheck="false" autocomplete="off">
    </label>
    <div class="note">要留出 10G 左右的空间。路径只能用英文、不能有空格，比如 <code>D:\\DaFeiYu</code>。以前装过的，填同一个位置会接着上次的进度。</div>
    <div id="msg" class="msg" role="alert"></div>`,
    `<span></span><button class="primary" id="go">开始安装</button>`);
  const go = async () => {
    $("#go").disabled = true;
    const r = await api("/api/path", { path: $("#path").value });
    $("#go").disabled = false;
    if (!r.ok) {
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
const HELP = {
  qq: `她只认这个号当主人：只听你的、会主动找你聊天。填你自己平时用的大号，<b>不是</b>给她登录的小号。`,
  deepseek: `<ol>
    <li>打开 <a href="https://platform.deepseek.com/" target="_blank">platform.deepseek.com</a>，用手机号登录。认准这个地址，别点广告和中转站。</li>
    <li>左侧「充值」，先充小额（几块钱就够试很久）。</li>
    <li>左侧「API keys」→「创建 API key」，名字随便填。</li>
    <li>弹出来的 key 只显示一次，点「复制」粘到这里。</li></ol>`,
  doubao: `<ol>
    <li>打开 <a href="https://www.volcengine.com/" target="_blank">volcengine.com</a> 注册，做<b>个人实名认证</b>（必须）。</li>
    <li>打开<b>旧版</b>豆包语音控制台 <a href="https://console.volcengine.com/speech/app" target="_blank">console.volcengine.com/speech/app</a>，「应用管理」里没有应用就「创建应用」。</li>
    <li>左侧「API 服务中心」，模型选「豆包音频生成模型 1.0」，选你的应用，点<b>「试用」</b>领免费额度。<b>先别点「开通」</b>，开通会开始收费，试用额度也会清零。</li>
    <li>同一页复制 <b>APP ID</b> 和 <b>Access Token</b>。火山方舟的 API Key、同一页的 Secret Key 都不是这个。</li></ol>
    按语音时长收费，约 1 元/分钟，一条语音几分钱。`,
  siliconflow: `<ol>
    <li>打开 <a href="https://www.siliconflow.cn/" target="_blank">siliconflow.cn</a> 注册并登录。</li>
    <li>左侧「API 密钥」→「新建 API 密钥」，描述填「大肥鱼记忆」。</li>
    <li>点那一行的「密钥（点击复制）」，粘到这里。</li></ol>
    记忆用的 <code>BAAI/bge-m3</code> 模型是免费的。`,
};

function keyField(id, label, help, type = "password", extra = "") {
  return `<label class="field" id="f-${id}">
    <span>${label} <em class="badge" id="b-${id}"></em></span>
    <input id="${id}" type="${type}" spellcheck="false" autocomplete="off" ${extra}>
    <div class="msg" id="m-${id}"></div>
  </label>
  <details class="help"><summary>去哪拿？</summary>${help}</details>`;
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

function pageKeys() {
  S.page = 1;
  const saved = (S.info.saved_keys || []);
  const ph = (f, normal) => saved.includes(f) ? 'placeholder="已保存，不用改就留空"' : normal;
  shell(1, S.mode === "status" ? "改 key" : "填写你的 key",
    S.mode === "status" ? "只想改一项，别的留空就行。这些 key 只存在你这台电脑上，加密保存。"
      : "填完一项会马上检查一下。这些 key 只存在你这台电脑上，加密保存。", `
    ${keyField("owner_qq", "你的大号 QQ", HELP.qq, "text", 'inputmode="numeric" placeholder="比如 123456789"')}
    ${keyField("deepseek", "DeepSeek API key（她的脑子）", HELP.deepseek, "password", ph("deepseek", 'placeholder="sk-..."'))}
    <div class="grid2">
      <label class="field" id="f-app_id"><span>豆包语音 APP ID <em class="badge" id="b-app_id"></em></span>
        <input id="app_id" type="text" spellcheck="false" autocomplete="off" ${ph("app_id", "")}><div class="msg" id="m-app_id"></div></label>
      <label class="field" id="f-token"><span>豆包语音 Access Token</span>
        <input id="token" type="password" spellcheck="false" autocomplete="off" ${ph("token", "")}><div class="msg" id="m-token"></div></label>
    </div>
    <details class="help"><summary>豆包语音去哪拿？（她发语音、唱歌用）</summary>${HELP.doubao}</details>
    ${keyField("siliconflow", "硅基流动 API key（她的记忆）", HELP.siliconflow, "password", ph("siliconflow", ""))}
    ${S.mode === "status" ? "" : `<div id="mini">${miniProgress()}</div>`}`,
    `<button id="back">${S.mode === "status" ? "返回" : "上一步"}</button>
     <button class="primary" id="go" disabled>${S.mode === "status" ? "保存并重启她" : "下一步"}</button>`);

  // 改设置时留空 = 用已保存的那个，所以空着也算过关；新装时每项都得填
  const blankOk = (f) => S.mode === "status" && saved.includes(f) && !$("#" + f).value.trim();
  const state = { owner_qq: false, deepseek: false, doubao: false, siliconflow: false };
  const refresh = () => ($("#go").disabled = !["owner_qq", "deepseek", "siliconflow"].every(
    (f) => state[f] || (f !== "owner_qq" && blankOk(f))) ||
    !(state.doubao || (blankOk("app_id") && blankOk("token"))));
  const val = (id) => $("#" + id).value.trim();

  const checkQQ = () => {
    const ok = /^\d{5,12}$/.test(val("owner_qq"));
    state.owner_qq = ok;
    setBadge("owner_qq", val("owner_qq") ? (ok ? "ok" : "bad") : "", ok || !val("owner_qq") ? "" : "QQ 号只能是 5 到 12 位数字");
    refresh();
  };
  const check = async (field) => {
    const badgeId = field === "doubao" ? "app_id" : field;
    const body = field === "doubao" ? { field, app_id: val("app_id"), token: val("token") } : { field, value: val(field) };
    if (field === "doubao" ? !(body.app_id && body.token) : !body.value) {
      state[field] = false;
      setBadge(badgeId, "", "");
      return refresh();
    }
    state[field] = false;
    refresh();
    setBadge(badgeId, "wait", field === "doubao" ? "豆包要试念一句，十几秒…" : "");
    const r = await api("/api/check", body);
    state[field] = !!r.ok;
    setBadge(badgeId, r.ok ? "ok" : "bad", r.message);
    refresh();
  };
  $("#owner_qq").oninput = checkQQ;
  $("#owner_qq").value = S.info.owner_qq || "";
  if (S.info.owner_qq) checkQQ();
  ["deepseek", "siliconflow"].forEach((f) => ($("#" + f).onchange = () => check(f)));
  ["app_id", "token"].forEach((f) => ($("#" + f).onchange = () => check("doubao")));
  $("#back").onclick = S.mode === "status" ? pageStatus : pagePath;
  $("#go").onclick = async () => {
    $("#go").disabled = true;
    $("#go").innerHTML = '<span class="spin"></span> 确认中';
    const r = await api("/api/keys", { owner_qq: val("owner_qq"), deepseek: val("deepseek"), siliconflow: val("siliconflow"),
      app_id: val("app_id"), token: val("token") });
    if (r.ok) return S.mode === "status" ? applySettings() : pageProfile();
    $("#go").textContent = S.mode === "status" ? "保存并重启她" : "下一步";
    for (const [k, msg] of Object.entries(r.errors || {})) setBadge(k === "doubao" ? "app_id" : k, "bad", msg);
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
    <div class="note" style="margin-top:-8px">里面的 <code>{OWNER_QQ}</code> 别删，装的时候会换成你的 QQ 号，她靠这个认主人。</div>
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
  $("#listen").onclick = async () => {
    $("#listen").disabled = true;
    $("#m-voice").className = "msg";
    $("#m-voice").textContent = "豆包在念，十几秒…（每次试听花几分钱）";
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
      ${s.status === "error" ? `<div class="t-msg" style="white-space:pre-wrap">${esc(s.message)}</div>` : ""}
      ${s.status === "doing" && s.key === "import" ? `<div class="t-wait">要装插件、写人格、接语音，几分钟</div>` : ""}
      ${s.status === "doing" && s.key === "extract" ? `<div class="t-wait">文件多，大概一两分钟</div>` : ""}
    </div></li>`).join("");
  const err = p.steps.some((s) => s.status === "error");
  $("#retry").hidden = !err;
  const login = p.steps.find((s) => s.key === "login");
  if (login && (login.status === "doing" || login.status === "done") && S.page === 3) pageQR();
}

function pageTasks() {
  S.page = 3;
  shell(3, "正在安装", "全部在后台自动完成。中途弹出 Windows 安全提示或防火墙提示，点「允许」或「运行」。", `
    <ul class="tasks" id="tasks"></ul>
    <div class="note">关掉窗口也没关系，下次双击一键包、选同一个位置，会接着装。</div>`,
    `<span></span><button class="primary" id="retry" hidden>重试</button>`);
  $("#retry").onclick = async () => {
    await api("/api/retry", {});
    $("#retry").hidden = true;
  };
  renderTasks();
}

// ---------------- 第 5 页：扫码 ----------------
const LOGIN_TEXT = {
  starting: ["正在准备二维码…", "wait"],
  need_scan: ["用登着小号的手机 QQ 扫一下", "wait"],
  scanned: ["扫到了，在手机上点「确认登录」", "wait"],
  online: ["登录成功", "on"],
  offline: ["小号掉线了，等一下会出新的二维码", "off"],
  error: ["二维码过期了，正在换一张", "off"],
};

function qrBlock() {
  return `<div class="qr">
    <div class="frame"><img id="qr" alt="QQ 登录二维码" hidden><span id="qrwait" class="spin"></span></div>
    <div class="status-big" style="font-size:16px"><span class="dot wait" id="dot"></span><span id="qrstate" role="status">正在准备二维码…</span></div>
    <div><button id="reqr" class="ghost">重新获取二维码</button></div>
    <div class="msg" id="qrmsg">二维码不出来？等半分钟。一直不出来，可能是杀毒软件拦了 NapCat，给安装目录添加信任后重开。</div>
  </div>`;
}

function bindRefreshQR() {
  const btn = $("#reqr");
  if (!btn) return;
  btn.onclick = async () => {
    btn.disabled = true;
    const r = await api("/api/refresh_qr", {});
    $("#qrmsg").textContent = r.message || "";
    setTimeout(() => { btn.disabled = false; }, 3000);
  };
}

function pollLogin(onOnline) {
  const tick = async () => {
    const r = await api("/api/login");
    const [text, dot] = LOGIN_TEXT[r.status] || LOGIN_TEXT.starting;
    if (!$("#qrstate")) return;
    $("#qrstate").textContent = text;
    $("#dot").className = "dot " + dot;
    // NapCat 说「已登录」时换码没用，得点按钮重启它，所以要把原因显示出来
    if (r.error && $("#qrmsg")) $("#qrmsg").textContent = "NapCat：" + r.error + "（点上面的「重新获取二维码」）";
    const show = r.status === "need_scan" || r.status === "scanned";
    if (show) {
      const img = $("#qr");
      const fresh = new Image();
      fresh.onload = () => { img.src = fresh.src; img.hidden = false; $("#qrwait").hidden = true; };
      fresh.src = `/api/qr?token=${encodeURIComponent(window.TOKEN)}&t=${Date.now()}`;
    } else {
      $("#qr").hidden = true;
      $("#qrwait").hidden = r.status === "online";
    }
    if (r.status === "online") onOnline();
  };
  tick();
  S.timers.push(setInterval(tick, 3000));
}

function pageQR() {
  S.page = 4;
  shell(4, "扫码登录小号", "用<b>小号</b>扫，不是你自己的大号。扫完在手机上确认。", qrBlock(), "");
  bindRefreshQR();
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
    `<button id="back">返回</button><span></span>`);
  $("#back").onclick = pageStatus;
  bindRefreshQR();
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
  const name = S.info.profile?.name || "大肥鱼";
  shell(5, `${esc(name)}上线了`, "用你的大号私聊小号，说句「在吗」试试。", `
    <div class="note">桌面放了一个「启动大肥鱼」。电脑重启后双击它，她就会重新上线（登录没失效就不用再扫码）。<br>
    电脑睡眠或关机时她就下线了，笔记本记得关掉自动睡眠。</div>
    <dl class="info">
      <dt>后台地址</dt><dd>${esc(d.dashboard_url)}</dd>
      <dt>后台账号</dt><dd>${esc(d.username)}</dd>
      <dt>后台密码</dt><dd>${esc(d.password)}</dd>
    </dl>
    <div class="msg">这组账号密码也存进了安装目录里的「我的信息.txt」。别截图发群。</div>`,
    `<a href="${esc(d.dashboard_url)}" target="_blank"><button type="button">打开后台</button></a><button class="primary" id="fin">完成</button>`);
  $("#fin").onclick = () => window.close();
}

// ---------------- 状态页（桌面快捷方式打开的） ----------------
async function pageStatus() {
  clearTimers();
  S.page = -1;
  const name = S.info.profile?.name || "大肥鱼";
  shell(-1, esc(name), "她住在你电脑里。关掉这个窗口不影响她在线。", `
    <div class="status-big"><span class="dot" id="sdot"></span><span id="stext">看看她在不在…</span></div>
    <div id="sqr" hidden>${qrBlock()}</div>
    <div id="serr" class="msg bad" role="alert"></div>
    <div class="acts">
      <button class="primary big" id="up">让她上线</button>
      <button class="big" id="down">让她下线</button>
    </div>
    <div class="group"><h3>改设置</h3><div class="acts">
      <button id="edit-prof">改名字 / 性格 / 声音</button>
      <button id="edit-keys">改 key</button>
      <button id="switch-qq">退出小号 / 换一个</button>
    </div></div>
    <div class="group"><h3>不用了</h3><div class="acts">
      <button id="quit">停掉她并退出</button>
      <button class="danger-ghost" id="uninstall">彻底卸载</button>
    </div>
    <div class="msg">「停掉她并退出」只是让她下线，桌面图标还在，随时双击让她回来。「彻底卸载」会删掉全部文件。</div></div>`, "");
  $("#up").onclick = async () => { await api("/api/start", {}); tick(); };
  $("#down").onclick = async () => { await api("/api/stop", {}); tick(); };
  $("#edit-keys").onclick = () => { clearTimers(); pageKeys(); };
  $("#edit-prof").onclick = () => { clearTimers(); pageProfile(); };
  $("#switch-qq").onclick = async () => {
    const yes = await confirmBox({
      title: "退出小号 / 换一个",
      body: `<p>当前这个小号会<b>退出登录</b>，不再是机器人，然后出一张新的二维码。</p>
        <div class="note">想换号就用另一个小号扫；不想再用了，出码后直接点「让她下线」就行。<br>
        这个小号在这台电脑上的登录数据会一起清掉，以后不会自己登回来。<br>
        名字、性格、聊天记录和表情包都留着。不会动你自己常用的那个 QQ。</div>`,
      ok: "退出登录",
    });
    if (!yes) return;
    await api("/api/switch_qq", {});
    await api("/api/start", {});
    pageRescan();
  };
  $("#quit").onclick = async () => {
    const yes = await confirmBox({
      title: "停掉她并退出",
      body: `<p>她会下线，后台也一起关掉，不再占电脑资源。</p>
        <div class="note">什么都不会删。想让她回来，双击桌面的「启动大肥鱼」。</div>`,
      ok: "停掉并退出",
    });
    if (!yes) return;
    await api("/api/quit", {});
    shell(-1, "她下线了", "已经全部停掉。", `
      <div class="note">想让她回来，双击桌面的「启动大肥鱼」。这个窗口可以关了。</div>`,
      `<span></span><button class="primary" id="fin">关闭窗口</button>`);
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
          <li>桌面上的「启动大肥鱼」图标</li>
          <li>小号的登录状态和它在这台电脑上的聊天数据</li>
        </ul>
        <div class="note">不装注册表、不设环境变量、不加开机自启，所以删完就真没了。<br>
        不会动你电脑上的 QQ 软件，也不会动你自己常用那个号的聊天记录。<br>
        只是暂时不用的话，点上一个「停掉她并退出」就够了。</div>`,
      ok: "我确定，卸载",
    });
    if (!yes) return;
    clearTimers();
    const r = await api("/api/uninstall", {});
    shell(-1, r.ok ? "已经卸载了" : "卸载没成功", r.ok ? "谢谢你陪了她一段时间。" : "", r.ok ? `
      <div class="note">文件会在几秒内删干净。关掉这个窗口就行。<br>
      想再装回来，重新双击一键包。</div>` : `<div class="msg bad" style="white-space:pre-wrap">${esc(r.message || "")}</div>`,
      `<span></span><button class="primary" id="fin">关闭窗口</button>`);
    $("#fin").onclick = () => window.close();
  };
  let qrPolling = false;
  const tick = async () => {
    const s = await api("/api/status");
    if (!$("#stext")) return;
    const running = s.astrbot && s.napcat;
    let text, dot;
    if (s.starting) [text, dot] = ["正在启动，大概一分钟…", "wait"];
    else if (!running) [text, dot] = ["她下线了", "off"];
    else if (s.login === "online") [text, dot] = [`在线${s.qq ? "（小号 " + s.qq + "）" : ""}`, "on"];
    else if (s.login === "need_scan" || s.login === "scanned" || s.login === "error") [text, dot] = ["要重新扫码登录", "wait"];
    else [text, dot] = ["正在连接 QQ…", "wait"];
    $("#stext").textContent = text;
    $("#sdot").className = "dot " + dot;
    $("#serr").textContent = s.error || "";
    $("#up").disabled = running || s.starting;
    $("#down").disabled = !s.astrbot && !s.napcat;
    const needQR = running && ["need_scan", "scanned", "error"].includes(s.login);
    $("#sqr").hidden = !needQR;
    if (needQR && !qrPolling) {
      qrPolling = true;
      bindRefreshQR();
      pollLogin(() => {});
    }
  };
  tick();
  S.timers.push(setInterval(tick, 3000));
  const s = await api("/api/status");
  if (!s.astrbot && !s.napcat && !s.starting) { await api("/api/start", {}); tick(); }
}

// 改完设置：原地等重启，不要把人甩回安装向导
async function applySettings() {
  clearTimers();
  S.page = -1;
  shell(-1, "正在应用改动", "在重写配置、重启她。大概一两分钟，别关窗口。", `
    <ul class="tasks" id="tasks"></ul>
    <div class="note">改完会自己回到主页。登录没失效就不用再扫码。</div>
    <div id="msg" class="msg bad" role="alert"></div>`,
    `<span></span><button class="primary" id="retry" hidden>重试</button>`);
  $("#retry").onclick = async () => { await api("/api/retry", {}); $("#retry").hidden = true; };
  await api("/api/apply", {});
  const tick = async () => {
    S.progress = await api("/api/progress");
    if (!$("#tasks")) return;
    renderApplying();
    if (S.progress.finished) return clearTimers(), pageStatus();
    // 改完 key 后登录可能失效。只有真的要扫码才跳过去：登录还在的话这一步瞬间就过了，
    // 光看 status === "doing" 会白闪一下二维码页
    const login = S.progress.steps.find((s) => s.key === "login");
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
  if (S.mode === "status") pageStatus();
  else pagePath();
})();

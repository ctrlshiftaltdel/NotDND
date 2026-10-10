/* ═══════════════════════════════════════════════════════════
   棱镜 · 前端共享运行时（core）
   原生 JS，无构建步骤、无第三方运行时依赖。

   结构约定（U0 切分后）：
     · 状态只有一份（ST / SAVES / SETTINGS），渲染不反向写状态；
     · 所有状态变化都经 applyState() 这一个入口，再分发到各 render*；
     · 视图显隐只改 .view 的 .is-on，测试据此断言；
     · 浮层用栈管理，Esc 逐层关闭；
     · 各「片」的代码在自己的文件里（lobby / turn / atlas / panels /
       voice / ph）。本文件只放**跨片共用**的东西。某一「片」若要在
       启动时绑事件，就在自己的文件里 onInit(fn) 登记（boot 统一跑一遍），
       这样后加的片不必回头改本文件。

   加载顺序：core.js 必须**最先**——各片在加载期就会调用 onInit。
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* ── 未捕获错误收集 ──────────────────────────────────────
   没有它，浏览器里炸掉的函数只会静默失败：界面停在旧状态，
   验收脚本只看得到「元素没出现」，分不清是逻辑没跑到还是中途
   抛了异常。收进一个数组，测试可直接读 window.__ERRS。 */
window.__ERRS = [];
window.addEventListener('error', function (e) {
  window.__ERRS.push(String((e.error && e.error.stack) || e.message));
});
window.addEventListener('unhandledrejection', function (e) {
  window.__ERRS.push('unhandled: ' + String((e.reason && e.reason.message) || e.reason));
});

/* ── 存储键（产品前缀，避免同域其它项目串味）────────────── */
var SID_KEY = 'prism.sid';
var SET_KEY = 'prism.settings';

/* ── 全局状态 ─────────────────────────────────────────── */
var SID = '';
var VIEW = 'v-lobby';
var ST = null;          // 当前会话快照（applyState 的唯一输入）
var SAVES = [];         // /api/saves 的摘要列表
var SAVE_DIR = '';      // 存档目录（服务端回传，只读展示）
var SAVES_TOTAL = 0;
var SAVES_TRUNCATED = 0;

try { SID = localStorage.getItem(SID_KEY) || ''; } catch (e) { SID = ''; }

/* ── 工具 ─────────────────────────────────────────────── */
function $(sel) { return document.querySelector(sel); }
function $$(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }

/* XSS 底线：任何拼进 innerHTML 的动态文本都要先过它。 */
function esc(v) {
  return String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

var toastTimer = null;
function toast(msg) {
  var t = $('#toast');
  if (!t) return;
  t.textContent = String(msg);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { t.hidden = true; }, 2600);
}

/* 震动反馈：受设置控制，设备不支持就当无事发生。 */
function buzz(ms) {
  if (!SETTINGS.haptics) return;
  if (!navigator.vibrate) return;
  try { navigator.vibrate(ms); } catch (e) { /* 不支持就算了 */ }
}

/* 视图切换：只改 .is-on，不用 hidden。 */
function showView(id) {
  VIEW = id;
  $$('.view').forEach(function (v) {
    v.classList.toggle('is-on', v.id === id);
  });
}

/* ── 与后端通信 ───────────────────────────────────────── */
async function api(path, opts) {
  var o = Object.assign({ method: 'GET', headers: {} }, opts || {});
  if (o.body !== undefined) {
    o.headers['Content-Type'] = 'application/json';
    o.body = JSON.stringify(o.body);
  }
  if (SID) o.headers['X-Session'] = SID;

  var res;
  try {
    res = await fetch(path, o);
  } catch (e) {
    throw new Error('无法连接服务，请确认后端仍在运行');
  }
  var data = null;
  try { data = await res.json(); } catch (e) { /* 非 JSON 响应 */ }

  if (!res.ok) {
    // 会话失效是唯一会自动清状态的一类错误：留着一个过期 SID，
    // 之后每个请求都会撞同一堵墙，而用户看不到原因。
    if (res.status === 400 && data && /会话/.test(String(data.error || ''))) {
      SID = '';
      try { localStorage.removeItem(SID_KEY); } catch (e) { /* 隐私模式 */ }
    }
    throw new Error((data && data.error) || ('请求失败（' + res.status + '）'));
  }
  return data;
}

/* ── 六维属性（产品层统一用 PRISM 名与缩写）────────────── */
var ATTR_ORDER = ['MGT', 'FIN', 'VIG', 'INS', 'MND', 'PRE'];
var ATTR_LABEL = { MGT: '力道', FIN: '灵巧', VIG: '体魄', INS: '洞察', MND: '心智', PRE: '气场' };

/* 占位会话：服务端还没有可开的会话时，用它把主画面的各渲染层跑通。
   这是演示数据，不是真实进度。 */
function placeholderState() {
  return {
    save: { name: '示例 · 未命名会话', named: false, log_count: 3 },
    world: { key: 'emberfall', name: '余烬纪元' },
    scenario: { name: '序章 · 灰原边缘' },
    scene: {
      name: '灰原边缘 · 破晓',
      read: '风把灰吹成一堵低矮的墙，贴着地面往南走。你落脚的地方比别处温热——说明脚底下还埋着没熄的东西。'
    },
    party: [{
      name: '示例角色',
      attributes: { MGT: 5, FIN: 6, VIG: 5, INS: 6, MND: 7, PRE: 4 },
      hp: 18, max_hp: 24, focus: 4
    }],
    log: [
      { seq: 1, kind: 'narrate', text: '你从昨夜的宿营地醒来，火堆只剩一点温。' },
      { seq: 2, kind: 'act', text: '（你）观察四周。' },
      { seq: 3, kind: 'narrate', text: '东边的沙脊上有一串刚被踩出来的脚印，比你的鞋码大不少。' }
    ],
    actions: [
      { id: 'a1', label: '观察沙脊', hint: '洞察' },
      { id: 'a2', label: '沿脚印前进', hint: '灵巧' },
      { id: 'a3', label: '就地拾取余烬', hint: '心智' }
    ],
    exits: [
      { id: 'e1', label: '向北 · 灰原更深处', via: 'north' },
      { id: 'e2', label: '向南 · 旧渡口', via: 'south' }
    ]
  };
}

/* ═══════════════ 渲染层（跨片共用）═══════════════
   每个 render* 只读 ST / SAVES / SETTINGS，绝不写回状态。
   各片专属的 render 在各自的文件里（见 applyState 的调用点注释）。 */

function renderTop() {
  var save = (ST && ST.save) || {};
  var scene = (ST && ST.scene) || {};
  $('#playSave').textContent = save.name || '未命名会话';
  $('#playScene').textContent = scene.name || '—';
}

function renderSceneCard() {
  var world = (ST && ST.world) || {};
  var scenario = (ST && ST.scenario) || {};
  var scene = (ST && ST.scene) || {};
  var crumb = [world.name, scenario.name].filter(Boolean).join(' · ');
  $('#sceneCard').innerHTML =
    (crumb ? '<div class="sc-crumb">' + esc(crumb) + '</div>' : '') +
    '<div class="sc-name">' + esc(scene.name || scene.id || '—') + '</div>' +
    (scene.read ? '<p class="sc-read">' + esc(scene.read) + '</p>' : '');
}

/* 单一状态入口：状态一变就整屏重画，而不是各处零散地改 DOM。
   跨片共用件 + 各片的 render 都挂在这条链路上。 */
function applyState(s) {
  ST = s || placeholderState();
  renderTop();
  renderSceneCard();
  renderStream();      // 片 U2 / U5（js/turn.js）
  renderActions();     // 片 U2（js/turn.js）
  renderExits();       // 片 U3（js/atlas.js）
  scrollToLatest();
}

function scrollToLatest() {
  if (!SETTINGS.autoScroll) return;   // 设置里关掉自动置底时不动滚动位置
  var sc = $('#playScroll');
  if (!sc) return;
  sc.scrollTop = sc.scrollHeight;
}

/* ═══════════════ 设置系统 ═══════════════
   三项职责：持久化（localStorage）、坏数据回退（类型不符即丢）、
   实际生效（写 <html data-*> 与 class）。 */

var SETTINGS_DEF = {
  font: 'm',          // 's' | 'm' | 'l'
  anim: true,         // 动效
  haptics: true,      // 震动反馈
  autoScroll: true,   // 新叙事自动置底
  denseText: false    // 紧凑排版
};
var SETTINGS = Object.assign({}, SETTINGS_DEF);

function loadSettings() {
  try {
    var raw = JSON.parse(localStorage.getItem(SET_KEY) || '{}');
    if (raw && typeof raw === 'object') {
      Object.keys(SETTINGS_DEF).forEach(function (k) {
        // 只接受类型一致的值：存档被人手改成 "huge" 之类的坏数据一律忽略。
        if (k in raw && typeof raw[k] === typeof SETTINGS_DEF[k]) SETTINGS[k] = raw[k];
      });
    }
  } catch (e) { /* 坏数据：用默认值 */ }
  if (['s', 'm', 'l'].indexOf(SETTINGS.font) < 0) SETTINGS.font = 'm';
}

function saveSettings() {
  try { localStorage.setItem(SET_KEY, JSON.stringify(SETTINGS)); } catch (e) { /* 隐私模式 */ }
}

function applySettings() {
  var r = document.documentElement;
  r.dataset.font = SETTINGS.font;
  r.dataset.anim = SETTINGS.anim ? 'on' : 'off';
  r.dataset.dense = SETTINGS.denseText ? 'on' : 'off';
  r.classList.toggle('no-anim', !SETTINGS.anim);
  saveSettings();
}

function renderSettings() {
  var seg = function (key, opts, cur) {
    return '<div class="seg">' + opts.map(function (p) {
      return '<button type="button" data-set="' + key + '" data-val="' + p[0] + '"' +
        ' class="' + (cur === p[0] ? 'is-on' : '') + '">' + p[1] + '</button>';
    }).join('') + '</div>';
  };
  var sw = function (key, label, hint) {
    return '<div class="set-row">' +
      '<div class="set-label">' + label + (hint ? '<small>' + hint + '</small>' : '') + '</div>' +
      '<label class="switch"><input type="checkbox" data-toggle="' + key + '"' +
        (SETTINGS[key] ? ' checked' : '') + '><i></i></label>' +
      '</div>';
  };

  $('#settingsForm').innerHTML =
    '<div class="set-group">' +
      '<div class="set-title">显示</div>' +
      '<div class="set-row">' +
        '<div class="set-label">界面字号<small>多人共用一台设备时可以调大</small></div>' +
        seg('font', [['s', '小'], ['m', '中'], ['l', '大']], SETTINGS.font) +
      '</div>' +
      sw('anim', '动效', '关闭后浮层与提示立即出现') +
      sw('denseText', '紧凑排版', '收窄叙事行距，一屏看更多') +
    '</div>' +
    '<div class="set-group">' +
      '<div class="set-title">反馈</div>' +
      sw('haptics', '震动反馈', '需要设备支持') +
      sw('autoScroll', '自动置底', '新叙事出现时自动滚到最新一段') +
    '</div>' +
    '<div class="set-group">' +
      '<div class="set-title">存储</div>' +
      '<div class="set-row">' +
        '<div class="set-label">本机存档目录<small>由服务端回传，只读</small></div>' +
        '<span class="set-value">' + esc(SAVE_DIR || '—') + '</span>' +
      '</div>' +
    '</div>';

  $$('#settingsForm [data-set]').forEach(function (b) {
    b.addEventListener('click', function () {
      SETTINGS[b.dataset.set] = b.dataset.val;
      applySettings();
      renderSettings();
    });
  });
  $$('#settingsForm [data-toggle]').forEach(function (c) {
    c.addEventListener('change', function () {
      SETTINGS[c.dataset.toggle] = c.checked;
      applySettings();
    });
  });
}

/* ═══════════════ 浮层互斥状态机 ═══════════════
   每个浮层是一对「遮罩 + 内容」。用栈管理当前打开的层：
   openSheet 压栈、closeTopSheet 弹栈，syncSheets 把栈状态同步到 DOM。
   栈而非单值，是为了「抽屉上再压一层关于」这种逐层场景：
   Esc 先收最上层，收完才轮到下面那层。 */

var SHEETS = {
  drawer: ['scrim', 'drawer'],
  aux: ['auxScrim', 'auxSheet']
};
var SHEET_STACK = [];

function syncSheets() {
  Object.keys(SHEETS).forEach(function (name) {
    var pair = SHEETS[name];
    var on = SHEET_STACK.indexOf(name) >= 0;
    $('#' + pair[0]).hidden = !on;
    $('#' + pair[1]).hidden = !on;
  });
  document.body.classList.toggle('is-locked', SHEET_STACK.length > 0);
}

function openSheet(name) {
  if (!SHEETS[name] || SHEET_STACK.indexOf(name) >= 0) return;
  SHEET_STACK.push(name);
  syncSheets();
}

function closeTopSheet() {
  if (!SHEET_STACK.length) return false;
  SHEET_STACK.pop();
  syncSheets();
  return true;
}

function closeAllSheets() {
  SHEET_STACK = [];
  syncSheets();
}

/* ═══════════════ 通用辅助浮层（关于 / 帮助）═══════════════ */

var AUX_TITLE = { about: '关于', help: '帮助' };
var AUX_KIND = null;

function openAux(kind) {
  if (!AUX_TITLE[kind]) return;
  AUX_KIND = kind;
  renderAux();
  openSheet('aux');
}

function closeAux() {
  AUX_KIND = null;
  // 只摘掉 aux 这一层；若抽屉还在下面开着，它继续留着（逐层）。
  var i = SHEET_STACK.lastIndexOf('aux');
  if (i >= 0) { SHEET_STACK.splice(i, 1); syncSheets(); }
}

function renderAux() {
  $('#auxTitle').textContent = AUX_TITLE[AUX_KIND] || '';
  var body = $('#auxBody');
  if (AUX_KIND === 'about') {
    body.innerHTML = '<div class="doc">' +
      '<p>棱镜 · AI 导引者：纯文本的 AI 跑团应用。规则取自 MIT 授权的「棱镜 PRISM」。</p>' +
      '<p>本页是前端工程骨架，用于验证视图系统、浮层状态机、无障碍与状态渲染分层；内容均为示例。</p>' +
      '<div class="doc-kv"><b>存档目录</b><span>' + esc(SAVE_DIR || '—') + '</span></div>' +
      '<div class="doc-kv"><b>存档数量</b><span>' + SAVES.length + ' / ' + SAVES_TOTAL + '</span></div>' +
      '</div>';
  } else if (AUX_KIND === 'help') {
    body.innerHTML = '<div class="doc">' +
      '<h4>快捷键</h4>' +
      '<ul><li>1–5：选择当前场景的行动</li><li>Esc：逐层关闭浮层</li></ul>' +
      '<h4>六维属性</h4>' +
      '<p>' + ATTR_ORDER.map(function (k) { return ATTR_LABEL[k] + ' ' + k; }).join(' · ') + '</p>' +
      '</div>';
  }
}

/* ═══════════════ 底部工具条实测高度 ═══════════════
   --dock-h 用于提示条定位等。硬编码会随字号档位 / 安全区变化而失准，
   所以实测回写：ResizeObserver 盯着工具条，尺寸一变就更新。 */

function syncDockHeight() {
  var dock = $('.dock');
  if (!dock) return;
  var h = dock.getBoundingClientRect().height;
  if (h > 0) document.documentElement.style.setProperty('--dock-h', Math.round(h) + 'px');
}

function watchDockHeight() {
  syncDockHeight();
  var docks = $$('.dock');
  if (!docks.length || typeof ResizeObserver === 'undefined') {
    window.addEventListener('resize', syncDockHeight);
    return;
  }
  var ro = new ResizeObserver(syncDockHeight);
  docks.forEach(function (d) { ro.observe(d); });
  window.addEventListener('resize', syncDockHeight);
}

/* ═══════════════ 切片初始化登记表 ═══════════════
   每片在自己的文件里把自己的启动函数登记进来（见各文件的 onInit 调用）。
   core 只负责按登记顺序跑一遍，不直接认识任何一片。 */
var SLICE_INITS = [];
function onInit(fn) { SLICE_INITS.push(fn); }

/* ═══════════════ 事件绑定（跨片共用部分）═══════════════ */

function handleEscape() {
  if (!SHEET_STACK.length) return;
  var top = SHEET_STACK[SHEET_STACK.length - 1];
  if (top === 'aux') { closeAux(); return; }
  closeTopSheet();
}

function bindCore() {
  /* 主画面 */
  $('#btnMenu').addEventListener('click', function () { openSheet('drawer'); });
  $('#btnPlaySettings').addEventListener('click', function () {
    renderSettings();
    showView('v-settings');
  });

  /* 抽屉 */
  $('#btnDrawerClose').addEventListener('click', closeAllSheets);
  $('#scrim').addEventListener('click', closeAllSheets);
  $('#btnBackToLobby').addEventListener('click', function () {
    closeAllSheets();
    showView('v-lobby');
  });
  $$('.menu-item').forEach(function (b) {
    b.addEventListener('click', function () {
      var kind = b.dataset.menu;
      if (kind === 'settings') {
        closeAllSheets();
        renderSettings();
        showView('v-settings');
        return;
      }
      openAux(kind);
    });
  });

  /* 辅助浮层 */
  $('#btnAuxClose').addEventListener('click', closeAux);
  $('#auxScrim').addEventListener('click', closeAux);

  /* 设置视图 */
  $('#btnSettingsBack').addEventListener('click', function () { showView('v-lobby'); });
  $('#btnSettingsDone').addEventListener('click', function () { showView('v-lobby'); });

  /* 键盘：Esc 逐层关闭浮层；主画面里 1–5 选择行动。 */
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { handleEscape(); return; }
    if (VIEW !== 'v-play') return;
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    var idx = ['1', '2', '3', '4', '5'].indexOf(e.key);
    if (idx < 0) return;
    var btns = $$('#actList [data-act]');
    if (btns[idx]) { e.preventDefault(); btns[idx].click(); }
  });
}

/* ═══════════════ 启动 ═══════════════ */

async function boot() {
  loadSettings();
  applySettings();
  bindCore();
  SLICE_INITS.forEach(function (fn) { fn(); });   // 各片绑自己的事件
  showView('v-lobby');
  watchDockHeight();

  // 开始界面也要能列存档；失败时 api() 已提示，这里不阻塞后续流程。
  await loadSaves();          // 片 U1（js/lobby.js）
  renderSaveList();           // 片 U1（js/lobby.js）

  if (SID) {
    var want = SID;   // 捕获此刻的归属
    try {
      var view = await api('/api/session');
      // 请求飞在路上时，用户可能已从列表进了另一局。
      // 不校验的话，这个迟到的启动响应会把新会话的画面盖成旧的。
      if (SID !== want) return;
      applyState(view);
      showView('v-play');
    } catch (e) {
      if (SID === want) {
        SID = '';
        try { localStorage.removeItem(SID_KEY); } catch (e2) { /* 隐私模式 */ }
      }
    }
  }
}

document.addEventListener('DOMContentLoaded', boot);

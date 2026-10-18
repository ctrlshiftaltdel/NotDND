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

/* 视图切换：只改 .is-on，不用 hidden。
   切完顺手重测 --dock-h：各视图的 .dock 高度不同（见下「底部工具条实测
   高度」），不跟着切的话，切过去的一瞬间提示条还按旧视图的高度定位。 */
function showView(id) {
  VIEW = id;
  $$('.view').forEach(function (v) {
    v.classList.toggle('is-on', v.id === id);
  });
  syncDockHeight();
}

/* ── 跨片写动作「单一在途」守卫（UI-DESIGN §2.0）─────────
   任一时刻最多一个**写**请求（method !== GET）在途：建局 / 改名 / 删除 /
   移动 / 回合互斥。理由：写请求会在服务端改状态，并发提交等于玩家自己
   跟自己抢状态。守卫放在 api() 这一薄层**统一登记**——各片不再各自为政，
   读请求（GET）不受限。

   在途时新的写请求直接拒绝（抛出可捕获的错误），不发往网络；各片写入口
   先用 writeInFlight() 提前忽略并禁用（见各片接线），本地 busy 标志保留为
   **幂等双保险**。完成后 / 失败后一律恢复，无「点了没反应」。 */
var WRITE_INFLIGHT = false;    // 是否有写请求在途
var WRITE_WATCHERS = [];       // 统一在途状态变化的订阅者（各片据此刷新忙碌反馈）

function writeInFlight() { return WRITE_INFLIGHT; }

/* 订阅统一在途状态变化：回调收到当前是否在途。返回一个退订函数。
   订阅者抛错不影响守卫本身（守卫是安全底线，不能被 UI 拖垮）。 */
function onWriteBusy(fn) {
  if (typeof fn !== 'function' || WRITE_WATCHERS.indexOf(fn) >= 0) return function () {};
  WRITE_WATCHERS.push(fn);
  return function () {
    var i = WRITE_WATCHERS.indexOf(fn);
    if (i >= 0) WRITE_WATCHERS.splice(i, 1);
  };
}

function setWriteInFlight(on) {
  on = !!on;
  if (WRITE_INFLIGHT === on) return;
  WRITE_INFLIGHT = on;
  WRITE_WATCHERS.slice().forEach(function (fn) {
    try { fn(on); } catch (e) { /* 订阅者异常不影响守卫 */ }
  });
}

/* 只读快照，供测试 / 调试核对统一守卫状态（等价 window.__ERRS 的用途）。 */
function writeSnapshot() {
  return { inflight: WRITE_INFLIGHT, watchers: WRITE_WATCHERS.length };
}

/* ── 与后端通信 ───────────────────────────────────────── */
async function api(path, opts) {
  var o = Object.assign({ method: 'GET', headers: {} }, opts || {});
  var isWrite = String(o.method || 'GET').toUpperCase() !== 'GET';

  // 单一在途（§2.0）：写请求互斥，读请求不受限。
  if (isWrite && WRITE_INFLIGHT) {
    throw new Error('上一项操作还没完成，请稍候');
  }

  if (o.body !== undefined) {
    o.headers['Content-Type'] = 'application/json';
    o.body = JSON.stringify(o.body);
  }
  if (SID) o.headers['X-Session'] = SID;

  if (isWrite) setWriteInFlight(true);
  try {
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
  } finally {
    // 成功 / 失败 / 异常一律恢复：不把在途状态留死。
    if (isWrite) setWriteInFlight(false);
  }
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
  // 未绑定（没有世界 / 剧本）时，场景卡的空态是「这一局还没有选战役」；
  // 行动区的战役入口归片 U-CAMP（js/campaign.js）。
  var bound = !!(scenario.id || scenario.name);
  var crumb = [world.name, scenario.name].filter(Boolean).join(' · ');
  $('#sceneCard').innerHTML =
    (crumb ? '<div class="sc-crumb">' + esc(crumb) + '</div>' : '') +
    '<div class="sc-name">' + esc(scene.name || scene.id || '—') + '</div>' +
    (scene.read ? '<p class="sc-read">' + esc(scene.read) + '</p>' : '') +
    (bound ? '' : '<p class="sc-note">这一局还没有选战役</p>');
  // 视觉占位框架（U6，js/ph.js）：渲染后的**可选**装饰钩子——ph.js 不在
  // （或算不出资产位）时什么都不发生，本函数其余逻辑与选择器语义不变。
  if (typeof phDecorateSceneCard === 'function') phDecorateSceneCard();
}

/* ── 世界主题（战役级常量，UI-WORLD-THEMES.md §2.1 / §2.5 / §4.7）──
   唯一输入是会话快照里的**结构化字段** `world_type`：不从世界名、叙事文本、
   模型输出或 `tone_pack` 推断（§2.3 硬性禁止）。取值只认三个枚举，未知 /
   缺失一律落 `modern`——即基础令牌（现代中性基线），与「不设属性」同一套
   计算值（tokens.css 不写 `[data-world="modern"]`）。
   这里只是**只读投影**：不写回存档、不提供对局中切换（§2.3 第 4 条）。
   返回归一后的取值，供设置区只读展示复用。 */
var WORLD_DEFAULT = 'modern';
var WORLD_LABEL = { fantasy: '奇幻', modern: '现代', future: '未来' };

function normalizeWorld(value) {
  return (value === 'fantasy' || value === 'modern' || value === 'future')
    ? value : WORLD_DEFAULT;
}

function applyWorldTheme(s) {
  var world = normalizeWorld(s && s.world_type);
  // 显式写 modern（而不是删属性）：未知值不会余留上一局的主题值，
  // 且与「从未设置」的计算值完全一致（tokens.css §4.7 的 :root 基线）。
  document.documentElement.dataset.world = world;
  return world;
}

/* 当前生效的世界主题：以 html 上的属性为准，读不到就按缺省 modern。 */
function currentWorld() {
  return normalizeWorld(document.documentElement.dataset.world);
}

/* 单一状态入口：状态一变就整屏重画，而不是各处零散地改 DOM。
   跨片共用件 + 各片的 render 都挂在这条链路上。 */
function applyState(s) {
  ST = s || placeholderState();
  // 主题先于渲染落地：首帧就用对的令牌，避免「先现代后奇幻」的闪一下。
  applyWorldTheme(ST);
  renderTop();
  renderSceneCard();
  renderStream();      // 片 U2 / U5（js/turn.js）
  renderActions();     // 片 U2（js/turn.js）
  renderExits();       // 片 U3（js/atlas.js）
  // 片 U-CAMP（js/campaign.js）：未绑定的新局在行动区画战役入口。可选切片
  // （ph.js 同款钩子）：文件不在时什么都不发生，其余渲染不变。
  if (typeof renderCampaignEntry === 'function') renderCampaignEntry();
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
  var world = currentWorld();
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
    // 本局主题是**只读**事实（战役级常量，§2.3 第 4 条）：这里只显示，
    // 不给切换控件，也不写回存档——外观偏好（明暗）不能顶替它（§2.3 第 5 条）。
    '<div class="set-group">' +
      '<div class="set-title">本局</div>' +
      '<div class="set-row">' +
        '<div class="set-label">本局世界主题<small>由战役决定，对局中不可更改</small></div>' +
        '<span id="setWorldTheme" class="set-value" data-world-theme="' + world + '">' +
          WORLD_LABEL[world] + '</span>' +
      '</div>' +
    '</div>' +
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
   所以实测回写：ResizeObserver 盯着工具条，尺寸一变就更新。

   三个视图各有一条 .dock（大厅 / 主画面 / 设置），高度各不相同；而
   `.view:not(.is-on)` 是 display:none——量隐藏的那条只会得 0，所以
   必须按**当前可见视图**取 dock。只认文档里第一条（旧写法）的话，
   切到主画面后量到的是大厅那条（display:none → 0，被 h > 0 守卫挡掉），
   --dock-h 就一直停在大厅的旧值上，提示条会盖住主画面的输入区。 */

function currentDock() {
  var view = $('.view.is-on');
  var dock = view ? view.querySelector('.dock') : null;
  if (dock && dock.getBoundingClientRect().height > 0) return dock;
  // 没有 .is-on（或它还没布局出高度）时，退回第一条**量得出高度**的
  // .dock：隐藏视图的高度是 0，天然被排除，不会把旧值写回去。
  var docks = $$('.dock');
  for (var i = 0; i < docks.length; i++) {
    if (docks[i].getBoundingClientRect().height > 0) return docks[i];
  }
  return null;
}

function syncDockHeight() {
  var dock = currentDock();
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

  window.__WRITE = writeSnapshot;   // 跨片写动作单一在途守卫的只读快照（测试 / 调试）
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

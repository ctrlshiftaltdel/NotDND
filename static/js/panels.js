/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U4：状态与记录面板（队伍 / 世界 / 记录）
   底栏三个 tab 打开 L2 辅助层（`#auxSheet`），展示：
     · 队伍 —— `party` / `pressure` / `combat` / `active_unit_id`（§1.3.3）；
     · 世界 —— ATLAS 位置与出口（`GET /api/atlas/exits`，§1.3.4）；
     · 记录 —— `/api/log` + `/api/session` 的 `rules.log`（§1.3.5）。
   出口「前往」与主画面同端点、同一动作（危险档同一确认口径，§2.6）；
   本片只改本文件与 css/panels.css，跨片共用件（esc / toast / api / openAux /
   openSheet / ST / SID / onInit …）在 js/core.js。
   本片样式 css/panels.css 与其它片一致，由 static/index.html 声明 `<link>` 加载。
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* ══════════════ 面板注册表 ══════════════
   UI-DESIGN §1.3.7：`openAux(kind)` 应是一张「kind → 标题 + 渲染」的注册表。
   core.js 冻结在本单的禁改路径里，所以在加载期**扩展** core 的注册表：
   往 AUX_TITLE 补三个键、包一层 renderAux（其余 kind 原样交回 core）。
   于是 tab 复用 core 同一套「压栈 → 渲染 → Esc 逐层关」链路，不新造浮层。 */
var PANEL_TITLE = { party: '队伍', scene: '世界', log: '记录' };

var CORE_RENDER_AUX = renderAux;   // core 的 about / help 渲染
renderAux = function () {
  if (PANEL_TITLE[AUX_KIND]) { renderPanel(AUX_KIND); return; }
  CORE_RENDER_AUX();
};
Object.keys(PANEL_TITLE).forEach(function (k) { AUX_TITLE[k] = PANEL_TITLE[k]; });

/* ── 本片状态 ────────────────────────────────────────────
   atlas / log 都是**服务端数据**：拿到前为 null（渲染加载态），拿到后
   才渲染，避免「空数组」与「还没拉到」被画成同一种空态。 */
var PANELS = {
  atlas: null,        // { here, exits, text } 或 { error }
  log: null,          // /api/log 的条目（未拉取时为 null）
  atlasReqId: 0,      // 读请求单调序号：迟到的旧响应一律丢弃
  logReqId: 0,
  move: false,        // 移动写请求在途（本片单一在途）
  confirm: null       // 危险移动确认中的出口（null = 无）
};

/* ══════════════ 小工具 ══════════════ */

/* 行程档文案：BAND_NAMES 的中文（不自己造词）+ 名义分钟（§2.6 定稿口径：
   中文 + `minutes` +（不稳）），危险档单独标记；minutes 为 0（门内）时不显示
   时长。前端只显示、不参与判定，与服务端 `text` 的「· N 分钟」同口径。 */
function panelBand(exit) {
  var names = { short: '短程', medium: '中程', long: '远程', dangerous: '危险穿越' };
  var minutes = (exit && exit.minutes) || 0;
  return {
    name: names[exit && exit.band] || '门内',
    minutes: minutes,
    duration: minutes > 0 ? minutes + ' 分钟' : '',
    dangerous: (exit && exit.band) === 'dangerous'
  };
}

/* 只重画「当前正开着的」这个面板：异步数据回来时用。 */
function renderIfOpen(kind) {
  if (AUX_KIND === kind) renderPanel(kind);
}

/* 复制一段文本：优先异步剪贴板，不可用时退回隐藏 textarea（老浏览器 / 非安全上下文）。 */
function copyText(text) {
  var done = function () { toast('已复制这一条'); };
  var fallback = function () {
    var ta = document.createElement('textarea');
    ta.value = String(text == null ? '' : text);
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) { /* 复制不可用就算了 */ }
    document.body.removeChild(ta);
    done();
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(String(text == null ? '' : text)).then(done, fallback);
  } else {
    fallback();
  }
}

/* ══════════════ 渲染入口 ══════════════
   renderAux 的 wrapper 调到这里；只负责按 kind 分发与设标题。 */
function renderPanel(kind) {
  var title = $('#auxTitle');
  if (title) title.textContent = PANEL_TITLE[kind] || '';
  if (kind === 'party') renderPartyPanel();
  else if (kind === 'scene') renderScenePanel();
  else if (kind === 'log') renderLogPanel();
}

/* ══════════════ 队伍（§1.3.3）══════════════ */

function renderPartyPanel() {
  var box = $('#auxBody');
  if (!box) return;
  var party = (ST && ST.party) || [];
  var combat = (ST && ST.combat) || null;
  var active = (ST && ST.active_unit_id) || '';
  var html = '';

  // 战斗态：面板顶部一条战斗带，**不换视图**（战斗是主循环的一种场景）。
  if (combat && (combat.enemies || combat.round)) {
    html += combatBand(combat, active);
  }

  // 空态明说「待接线」，不画一个假角色卡（§1.3.3）。
  if (!party.length) {
    html += '<div class="stream-empty">还没有角色。等角色创建接线后会显示在这里。</div>';
  } else {
    html += party.map(function (u) { return unitCard(u, u && u.id === active); }).join('');
  }
  box.innerHTML = html;
  // 视觉占位框架（U6，js/ph.js）：可选装饰钩子（没有 id 的单位不加位）。
  if (typeof phDecoratePortraits === 'function') phDecoratePortraits(box, party);
}

function combatBand(combat, active) {
  var round = intOr(combat.round, 1);
  var enemies = (combat.enemies || []).length;
  var current = combatCurrent(combat) || active || '—';
  var items = [
    ['回合', String(round)],
    ['敌体', String(enemies)],
    ['当前行动者', current]
  ];
  return '<div class="combat-band" role="status">' +
    '<span class="combat-tag">战斗中</span>' +
    items.map(function (p) {
      return '<span class="combat-item"><b>' + esc(p[0]) + '</b>' + esc(p[1]) + '</span>';
    }).join('') +
    (combat.over ? '<span class="combat-item"><b>' + esc('状态') + '</b>' + esc(combatOverText(combat.over)) + '</span>' : '') +
    '</div>';
}

function combatCurrent(combat) {
  var order = combat.order || [];
  var idx = intOr(combat.turn_i, 0);
  var entry = order[idx];
  return entry && entry.name ? String(entry.name) : '';
}

function combatOverText(over) {
  return { victory: '已结束（胜）', defeat: '已结束（败）', fled: '已脱离' }[over] || '已结束';
}

function unitCard(u, isActive) {
  if (!u) return '';
  var attrs = u.attributes || {};
  var dims = ATTR_ORDER.map(function (k) {
    return '<div class="unit-dim"><b>' + esc(attrs[k] == null ? '—' : attrs[k]) + '</b>' +
      '<span>' + esc(ATTR_LABEL[k] + ' ' + k) + '</span></div>';
  }).join('');

  var flags = '';
  if (u.dead) flags += '<span class="unit-flag is-dead">死亡</span>';
  else if (u.downed) flags += '<span class="unit-flag is-downed">濒危</span>';

  var conditions = (u.conditions || []).filter(Boolean);
  var condText = conditions.length ? conditions.join(' · ') : '无';

  return '<article class="unit-card' + (u.dead ? ' is-dead' : (u.downed ? ' is-downed' : '')) + '">' +
    '<div class="unit-head">' +
      '<h3 class="unit-name">' + esc(u.name || u.id || '未命名') +
        (u.level ? '<span class="unit-level">Lv.' + esc(u.level) + '</span>' : '') +
        (isActive ? '<span class="unit-badge">出场</span>' : '') +
      '</h3>' +
      (flags ? '<div class="unit-flags">' + flags + '</div>' : '') +
    '</div>' +
    '<div class="unit-meta">' +
      '<span>活力 ' + esc(intOr(u.vitality, 0)) + '/' + esc(intOr(u.max_vitality, 0)) + '</span>' +
      '<span>韧性 ' + esc(intOr(u.poise, 0)) + '/' + esc(intOr(u.max_poise, 0)) + '</span>' +
      '<span>应力 ' + esc(intOr(u.stress, 0)) + '</span>' +
    '</div>' +
    '<div class="unit-dims">' + dims + '</div>' +
    '<div class="unit-cond"><b>状态</b><span>' + esc(condText) + '</span></div>' +
    '</article>';
}

/* ══════════════ 世界（§1.3.4）══════════════ */

function renderScenePanel() {
  var box = $('#auxBody');
  if (!box) return;

  // 危险移动的确认：同一块 L2 内容里换一屏，不另造浮层。
  if (PANELS.confirm) { renderWorldConfirm(); return; }

  if (!SID) {
    box.innerHTML = '<div class="stream-empty">还没有会话。进入一局后，这里会显示当前位置与出口。</div>';
    return;
  }
  var a = PANELS.atlas;
  if (!a) {
    box.innerHTML = '<div class="stream-empty">正在读取位置与出口…</div>';
    return;
  }
  if (a.error) {
    box.innerHTML = '<div class="alert"><p>' + esc(a.error) + '</p>' +
      '<div><button type="button" class="btn" data-world-retry="1">重试</button></div></div>';
    var retry = box.querySelector('[data-world-retry]');
    if (retry) retry.addEventListener('click', function () { loadAtlas(); });
    return;
  }

  var here = a.here || {};
  var meta = [here.place_id, here.kind, here.world_key].filter(Boolean);
  var html = '<div class="world-here">' +
    '<div class="world-name">' + esc(here.name || '—') + '</div>' +
    (meta.length ? '<div class="world-meta">' + meta.map(esc).join(' · ') + '</div>' : '') +
    '</div>';

  html += '<div class="sec-title">出口</div>';
  var exits = a.exits || [];
  if (exits.length) {
    html += '<div class="exit-list">' + exits.map(function (x, i) {
      var b = panelBand(x);
      var hint = b.name + (b.duration ? ' · ' + b.duration : '') + (x.unstable ? ' · 不稳' : '');
      return '<button type="button" class="btn act-btn world-exit' + (b.dangerous ? ' is-danger' : '') +
        '" data-world-exit="' + i + '">' +
        '<span class="act-label">' + esc(x.name || x.via || '') + '</span>' +
        '<span class="act-hint">' + esc(hint) + '</span>' +
        '</button>';
    }).join('') + '</div>';
  } else {
    // 空态：照抄服务端原文（口径同源，前端不自己编）。
    html += '<div class="stream-empty world-empty">' + esc(a.text || '这里没有已知出口。') + '</div>';
  }

  // text 整段可折叠（给需要复制 / 朗读的场景）。
  if (a.text) {
    html += '<details class="world-text"><summary>地点原文</summary>' +
      '<pre class="world-text-body">' + esc(a.text) + '</pre></details>';
  }

  box.innerHTML = html;
  // 视觉占位框架（U6，js/ph.js）：当前位置的地点位（ATLAS 的 place_id）。
  if (typeof phDecoratePlace === 'function') phDecoratePlace(box, here);
  Array.prototype.slice.call(box.querySelectorAll('[data-world-exit]')).forEach(function (btn) {
    btn.addEventListener('click', function () { activateWorldExit(intOr(btn.dataset.worldExit, -1)); });
  });
}

function activateWorldExit(i) {
  var a = PANELS.atlas || {};
  var x = (a.exits || [])[i];
  if (!x || !x.via) return;
  if (PANELS.move || writeInFlight()) return;   // 单一在途：本片 / 统一层有写在途
  buzz(12);

  // 与主画面「前往」同一动作、同一确认：主画面（U3）已接后端时，按
  // (via, name) 在主列表里定位同一条，直接复用它——危险档的 L2 确认、
  // 单一在途都在那一片，避免出现第二个移动入口。
  if (delegateToMainExit(x)) { closeAux(); return; }

  // 主画面尚未接线时（U3 未合入）：本片自己走同一端点，保持面板可用。
  if (panelBand(x).dangerous) { PANELS.confirm = x; renderIfOpen('scene'); return; }
  doWorldMove(x);
}

/* 复用主画面出口动作：仅当 U3 的 ATLAS 状态存在时才认（否则 master 上的
   pickExit 只吃占位 id，签名不同，不能乱调）。按 (via, name) 唯一定位到
   同一条才复用，避免指错目的地。找到返回 true。 */
function delegateToMainExit(x) {
  if (typeof ATLAS === 'undefined' || !ATLAS || typeof pickExit !== 'function') return false;
  var list = ATLAS.exits || [];
  var hit = -1;
  for (var i = 0; i < list.length; i++) {
    if (!list[i] || list[i].via !== x.via || list[i].name !== x.name) continue;
    if (hit >= 0) return false;   // 不唯一：交回本片自己按 via 走同一端点
    hit = i;
  }
  if (hit < 0) return false;
  pickExit(hit);
  return true;
}

function renderWorldConfirm() {
  var box = $('#auxBody');
  var x = PANELS.confirm || {};
  var b = panelBand(x);
  box.innerHTML = '<div class="world-confirm">' +
    '<p class="confirm-line">前往「' + esc(x.name || x.via || '未知地点') + '」？</p>' +
    '<p class="confirm-sub">' + esc(b.name + (b.duration ? ' · ' + b.duration : '')) + '</p>' +
    '<div class="confirm-actions">' +
      '<button type="button" class="btn" data-world-cancel="1">取消</button>' +
      '<button type="button" class="btn btn-primary" data-world-go="1">继续穿越</button>' +
    '</div></div>';
  var cancel = box.querySelector('[data-world-cancel]');
  var go = box.querySelector('[data-world-go]');
  if (cancel) cancel.addEventListener('click', closeWorldConfirm);
  if (go) go.addEventListener('click', function () {
    var target = PANELS.confirm;
    closeWorldConfirm();
    if (target) doWorldMove(target);
  });
}

function closeWorldConfirm() {
  PANELS.confirm = null;
  renderIfOpen('scene');
}

async function loadAtlas() {
  if (!SID) { PANELS.atlas = null; renderIfOpen('scene'); return; }
  var my = ++PANELS.atlasReqId;
  try {
    var view = await api('/api/atlas/exits');
    if (my !== PANELS.atlasReqId) return;
    PANELS.atlas = {
      here: view.here || {},
      exits: Array.isArray(view.exits) ? view.exits : [],
      text: view.text || ''
    };
  } catch (e) {
    if (my !== PANELS.atlasReqId) return;
    PANELS.atlas = { error: e.message, here: {}, exits: [], text: '' };
  }
  renderIfOpen('scene');
}

async function doWorldMove(x) {
  if (PANELS.move || writeInFlight()) return;   // 统一守卫在途：不发起第二次写
  PANELS.move = true;
  renderIfOpen('scene');
  try {
    var view = await api('/api/atlas/move', { method: 'POST', body: { via: x.via } });
    PANELS.atlas = {
      here: view.here || {},
      exits: Array.isArray(view.exits) ? view.exits : [],
      text: view.text || ''
    };
    var name = (view.here && view.here.name) || x.name || '';
    if (name) toast('你到达了「' + name + '」');
    // 位置变了：规则层快照可能一起变，刷新整局视图（顶栏 / 规则层）。
    try { applyState(await api('/api/session')); } catch (e2) { /* 更新失败不影响已成功移动 */ }
  } catch (e) {
    toast(e.message);
  } finally {
    PANELS.move = false;
    renderIfOpen('scene');
  }
}

/* ══════════════ 记录（§1.3.5）══════════════ */

function renderLogPanel() {
  var box = $('#auxBody');
  if (!box) return;
  if (PANELS.log === null && SID) {
    box.innerHTML = '<div class="stream-empty">正在读取记录…</div>';
    return;
  }
  var entries = mergeLog(PANELS.log || [], (ST && ST.log) || []);
  if (!entries.length) {
    box.innerHTML = '<div class="stream-empty">还没有记录。开局后每一步都会追加在这里。</div>';
    return;
  }
  PANELS.rendered = entries;
  box.innerHTML = '<div class="stream log-stream">' + entries.map(logItem).join('') + '</div>' +
    '<p class="log-foot">「加载更早」暂不在首版（服务端目前只有正向 ?since=）。</p>';
  Array.prototype.slice.call(box.querySelectorAll('[data-copy]')).forEach(function (btn) {
    btn.addEventListener('click', function () {
      copyText((PANELS.rendered || [])[intOr(btn.dataset.copy, -1)] && (PANELS.rendered[intOr(btn.dataset.copy, -1)].text || ''));
    });
  });
}

/* 合并两个来源、按 seq 递增：
   `/api/log` 的条目带 seq（会话层，单调递增），`/api/session` 的 rules.log
   条目今天不带 seq（规则层）。有 seq 的按 seq 升序排在前面；没有 seq 的
   按数组原序接在后面。同一 (kind, text) 视为同一条事件（B2 落地后两层会
   出现同一回合的重复条目，这里去重）。 */
function mergeLog(sessionEntries, rulesEntries) {
  var seen = {};
  var out = [];
  var key = function (e) { return String(e.kind || '') + '\u0000' + String(e.text || ''); };
  sessionEntries.slice().sort(function (a, b) {
    return intOr(a.seq, 0) - intOr(b.seq, 0);
  }).forEach(function (e) {
    seen[key(e)] = true;
    out.push(e);
  });
  rulesEntries.forEach(function (e) {
    if (seen[key(e)]) return;
    out.push(e);
  });
  return out;
}

function logItem(e) {
  var kind = e.kind === 'act' ? 'act' : ((e.kind === 'note' || e.kind === 'system' || e.kind === 'atlas') ? 'note' : 'narrate');
  var src = e.speaker ? String(e.speaker) : String(e.kind || '');
  var idx = (PANELS.rendered || []).indexOf(e);
  return '<div class="msg msg-' + kind + ' log-item">' +
    '<div class="log-head">' +
      (e.seq != null ? '<span class="log-seq">#' + esc(e.seq) + '</span>' : '') +
      (src ? '<span class="log-src">' + esc(src) + '</span>' : '') +
      '<button type="button" class="log-copy" data-copy="' + idx + '" aria-label="复制这一条">复制</button>' +
    '</div>' +
    '<div class="log-text">' + esc(e.text || '') + '</div>' +
    '</div>';
}

async function loadLog() {
  if (!SID) { PANELS.log = []; renderIfOpen('log'); return; }
  var my = ++PANELS.logReqId;
  try {
    var d = await api('/api/log?since=0');
    if (my !== PANELS.logReqId) return;
    PANELS.log = Array.isArray(d.log) ? d.log : [];
  } catch (e) {
    if (my !== PANELS.logReqId) return;
    PANELS.log = [];
  }
  renderIfOpen('log');
}

function intOr(v, d) {
  var n = parseInt(v, 10);
  return isNaN(n) ? d : n;
}

/* ══════════════ 事件绑定 ══════════════ */

function selectTab(t) {
  $$('.tab').forEach(function (x) {
    var on = x === t;
    x.classList.toggle('is-on', on);
    x.setAttribute('aria-selected', on ? 'true' : 'false');
  });
}

function bindPanels() {
  $$('.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      var kind = t.dataset.panel;
      if (!PANEL_TITLE[kind]) return;
      selectTab(t);
      PANELS.confirm = null;         // 重新打开面板时不留上一次的确认残影
      if (kind === 'scene') PANELS.atlas = null;   // 每次打开都取新位置与出口
      if (kind === 'log') PANELS.log = null;       // 每次打开都取新记录
      openAux(kind);                 // core：压栈 + 渲染（renderAux 的 wrapper 分发到本片）
      if (kind === 'scene') loadAtlas();
      else if (kind === 'log') loadLog();
    });
  });

  // 关掉辅助层（关闭按钮 / 点遮罩）时清掉本片的确认状态，避免下次打开残影。
  ['#btnAuxClose', '#auxScrim'].forEach(function (sel) {
    var el = $(sel);
    if (el) el.addEventListener('click', function () { PANELS.confirm = null; });
  });

  // 危险确认开着时吞掉 Esc：先关确认这一「层」，不直接把整个面板收掉
  // （capture 阶段先于 core 的冒泡 Esc，保证逐层语义）。
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !PANELS.confirm) return;
    if (AUX_KIND !== 'scene') return;
    e.preventDefault();
    e.stopPropagation();
    closeWorldConfirm();
  }, true);
}

onInit(bindPanels);

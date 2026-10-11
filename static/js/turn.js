/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U2：跑团主循环（输入区 + 叙事流 + 行动区）
   一个回合走完：输入 → 判定卡 → 叙事 → 兜底 → 收尾（UI-DESIGN.md §2.3）。

   · 输入区固定在工具条里（`#turnComposer`），Enter 发送 / Shift+Enter 换行，
     长度上限 2000 字符本地拦住（与后端 GUIDE_TEXT_LIMIT 同值）；
   · `POST /api/guide/turn` 有**两种 200**——`text/event-stream`（增量渲染）
     与整段 JSON（`淡出` / `跳过这段` / 离线兜底，§2.3），两种都要吃；
   · 加载 / 刷新时经 `GET /api/log?since=<seq>` 恢复既有的回合流（B2 已落流）；
   · 玩家输入与导引者叙事由服务端落进 `Session.log`，前端**只读**两份真相：
     流里的增量与 `?since=` 的增量按 (kind, text) 认领同一条，不会出现两遍。

   本片只改本文件与 css/stream.css、index.html 的结构挂点；共用件
   （esc / toast / api / onInit / SID / ST / scrollToLatest …）在 js/core.js。
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* ── 本片状态 ────────────────────────────────────────────
   log 是**渲染用的有序条目表**：服务端条目带 seq，回合进行中先落的条目
   先不带 seq 且标着 `pending`，收尾时被同 (kind, text) 的服务端条目认领。 */
var TURN = {
  sid: '',            // 流数据归属的会话：换一局就整条丢弃（不能串味）
  log: [],
  lastSeq: 0,         // /api/log 的正向游标（只取比它更新的条目）
  loaded: false,      // 是否成功恢复过一次（决定「正在读取」与空态）
  fetching: false,    // 读请求在途（也用来挡住 render → fetch → render 自递归）
  fetchId: 0,         // 读请求单调序号：迟到的旧响应一律丢弃
  sessionId: 0,       // 收尾时的 /api/session 请求序号（同上）
  busy: false,        // 回合在途（§2.0 单一在途；同时登记进 core 的统一守卫）
  error: '',          // 就地错误行（服务端 error 原文）
  retryable: false,   // 就地错误可重试 → 给「重试」入口
  inputError: '',     // 输入侧就地提示（超长）
  loadError: '',      // 恢复回合流失败（流位置的就地提示 + 重试）
  note: '',           // 流末尾的如实说明（中途断 / 未持久化）
  cooldownUntil: 0,   // 429 后本地禁用发送的时刻（§1.3.10：3 秒）
  cooldownTimer: null,
  lastText: '',       // 失败重试用：上一回合的正文与行动
  lastActionId: '',
  usage: null,        // usage 事件只喂调试面板，**不渲染给玩家**（§2.3）
  sessionError: '',   // 会话加载失败（§2.2 / §2.8）：错误卡 + 输入区/行动区禁用
  sessionRetry: null, // 错误卡「重试」要做的事（由发起加载的那一片给）
  guide: { chat: false, tts: false, configured: false },   // 导引者能力探测（§1.3.8）
  rendered: ''        // 已渲染的签名：状态没变就不重画（避免 live 区重播整段）
};

var TURN_TEXT_LIMIT = 2000;      // 与 notdnd_web.GUIDE_TEXT_LIMIT 同值（§1.3.2）
var TURN_COOLDOWN_MS = 3000;     // 429「太频繁」后本地禁用发送的时间（§1.3.10）

/* 五档中文（§2.4 / §3.5.3）：档位**必须有文字**，颜色只是辅助——
   色盲用户靠文字就能分辨（§5.4 硬要求）。 */
var TURN_OUTCOME_ZH = {
  triumph: '凯旋', success: '成功', narrow: '险成', failure: '挫败', catastrophe: '灾难'
};
/* 资源池中文名（data/system/resources.json 的 name）。前端只显示，
   **不写「生命值 −3」这类自造词**（§2.4）。 */
var TURN_POOL_ZH = { focus: '专注', tempo: '气势', strain: '负担', resolve: '决意' };

/* 只读快照，供测试 / 调试核对本片状态（等价 window.__ERRS 的用途）。 */
function turnSnapshot() {
  return {
    mode: streamServerMode() ? 'server' : 'placeholder',
    entries: TURN.log.length,
    kinds: TURN.log.map(function (e) { return e.kind; }),
    pending: TURN.log.filter(function (e) { return !!e.pending; }).length,
    lastSeq: TURN.lastSeq,
    loaded: TURN.loaded,
    busy: TURN.busy,
    error: TURN.error,
    inputError: TURN.inputError,
    note: TURN.note,
    loadError: TURN.loadError,
    sessionError: TURN.sessionError,
    guide: { chat: TURN.guide.chat, tts: TURN.guide.tts, configured: TURN.guide.configured },
    cooldown: TURN.cooldownUntil > Date.now(),
    text: turnText().length
  };
}

/* 小工具（本片自带，不借别的片——片之间只共用 core）。 */
function turnInt(v, d) {
  var n = parseInt(v, 10);
  return isNaN(n) ? d : n;
}
function turnEl(id) { return document.getElementById(id); }
function turnSigned(n) { n = turnInt(n, 0); return (n > 0 ? '+' : '−') + Math.abs(n); }

/* ══════════════ 叙事流 ══════════════
   两条来源、一套渲染：
     · 有真实会话快照（ST.sid 存在）→ 服务端回合流（/api/log + SSE 增量）；
     · 没有（演示态 / 旧会话）→ 占位数据 ST.log（保持既有行为）。
   口径来自 UI-DESIGN.md §1.3.2：目标态以 /api/log?since= 为准做增量拉取。 */

function streamServerMode() {
  return !!(SID && ST && ST.sid);
}

function resetTurnStream() {
  TURN.sid = SID;
  TURN.log = [];
  TURN.lastSeq = 0;
  TURN.loaded = false;
  TURN.loadError = '';
  TURN.note = '';
  TURN.rendered = '';
}

/* 渲染签名：状态没变就不重画 `#stream`——`#stream` 是 aria-live 区，
   整块重画会让读屏软件把整段叙事再播一遍（§5.3）。 */
function streamSig() {
  if (!streamServerMode() || TURN.sessionError) {
    return 'demo:' + TURN.sessionError + ':' + ((ST && ST.log) || []).length;
  }
  return 'server:' + TURN.log.length + '|' + TURN.note + '|' + TURN.loadError +
    '|' + (TURN.loaded ? 1 : 0);
}

/* kind → 视觉（§3.5.1 的完整映射表；trace 单列，见 streamItemHtml）。 */
function streamKind(e) {
  var k = String((e && e.kind) || '');
  if (k === 'act') return 'act';
  if (k === 'check') return 'check';
  if (k === 'result') return 'result';
  if (k === 'trace') return 'trace';
  if (k === 'narrate' || k === 'narrative') return 'narrate';
  return 'note';                 // system / atlas / note：居中虚线
}

function streamItemHtml(e) {
  var kind = streamKind(e);
  if (kind === 'act') {
    return '<div class="msg msg-act"><span class="msg-text">' + esc(e.text) + '</span></div>';
  }
  if (kind === 'check') {
    return '<div class="msg msg-check"><span class="msg-text">' + esc(e.text) + '</span></div>';
  }
  if (kind === 'result') return resultCardHtml(e);
  if (kind === 'trace') {
    return '<div class="msg msg-narrate">' + traceBlockHtml(e.text) + '</div>';
  }
  if (kind === 'narrate') {
    // 秘密门（prism_guide.redact_narration）的删除对玩家**无痕**：服务端给的
    // 就是涂掉之后的字，这里既不提示「此处有内容被隐藏」，也不留占位——
    // 「告诉玩家删过东西」本身就是泄密（§2.3 唯一允许静默删减的地方）。
    return '<div class="msg msg-narrate"><span class="msg-text">' + esc(e.text) + '</span>' +
      (e.trace ? traceBlockHtml(e.trace) : '') + '</div>';
  }
  return '<div class="msg msg-note"><span class="msg-text">' + esc(e.text) + '</span></div>';
}

/* 痕迹揭开（§3.5.2）：它是「这一回合的收获」，**要**显式呈现——与秘密门的
   删除不同。不加「你发现了」这类自造前缀，正文就是服务端给的原文。 */
function traceBlockHtml(text) {
  if (!text) return '';
  return '<div class="msg-trace">' +
    '<div class="trace-head"><svg class="i i-sm" aria-hidden="true"><use href="#i-scroll"/></svg>痕迹</div>' +
    '<div class="trace-text">' + esc(text) + '</div>' +
  '</div>';
}

/* 判定卡（§2.4 / §3.5.3）：档位徽标 + 骰值（notation）+ 代价块 + 资源变化。
   规则层日志里的 `result` 条目只有正文（没有结构化载荷），按纯文本画。 */
function resultCardHtml(e) {
  var r = e && e.result;
  if (!r) {
    return '<div class="msg msg-result"><span class="msg-text">' + esc(e && e.text) + '</span></div>';
  }
  var outcome = String(r.outcome || '');
  var status = String(r.status || '');
  var zh = TURN_OUTCOME_ZH[outcome] || (status === 'already_done' ? '已完成'
    : (status === 'exhausted' ? '已用尽' : '结算'));
  var tone = (outcome === 'triumph' || outcome === 'success') ? 'success'
    : (outcome === 'narrow' ? 'accent' : (outcome ? 'danger' : ''));
  // 凯旋 / 灾难用实心徽标，其余描边（§2.4 的档位视觉）。
  var solid = (outcome === 'triumph' || outcome === 'catastrophe') ? 'true' : 'false';
  var action = (r.action && r.action.label) ? String(r.action.label) : '';

  var head = '<div class="res-head">' +
    '<span class="res-badge" data-tone="' + tone + '" data-solid="' + solid + '">' + esc(zh) + '</span>' +
    (action ? '<span class="res-action">' + esc(action) + '</span>' : '') +
    (r.auto ? '<span class="res-flag">无需掷骰</span>' : '') +
    (r.exhausted ? '<span class="res-flag is-danger">已用尽</span>' : '') +
    (outcome === 'narrow' ? '<span class="res-flag">附一个代价</span>' : '') +
  '</div>';

  // 骰值：服务端没给明细（detail 为空）时就只有结论，一行也不编（§2.4）。
  var notation = '';
  if (r.roll && r.roll.detail) {
    var df = (r.action && r.action.df != null) ? turnInt(r.action.df, 0) : null;
    var notationLabel = r.roll.notation || '';
    notation = '<div class="res-notation">' + esc(notationLabel) + ' = ' +
      esc(turnInt(r.roll.total, 0)) + (df == null ? '' : ' vs DF ' + esc(df)) + '</div>';
  }

  var tier = r.tier_text ? '<div class="res-tier">' + esc(r.tier_text) + '</div>' : '';
  var cost = costBlockHtml(r.cost);
  var delta = unitDeltaHtml(r.unit);
  var granted = grantedHtml(r.granted);
  var message = r.message ? '<div class="res-tier">' + esc(r.message) + '</div>' : '';

  return '<div class="msg msg-result" data-outcome="' + esc(outcome) + '">' +
    head + notation + message + tier + cost + delta + granted + '</div>';
}

/* 代价块（§2.4）：--danger 细边，逐项列 key → 值；资源池名字只用
   data/system/resources.json 的中文名，活力 / 韧性来自规则层的字段。 */
function costBlockHtml(cost) {
  if (!cost) return '';
  var raw = cost.cost || {};
  var rows = [];
  if (cost.damage && turnInt(cost.damage.lost, 0)) {
    rows.push(['活力', '−' + Math.abs(turnInt(cost.damage.lost, 0))]);
    if (cost.damage.downed) rows.push(['状态', '濒危']);
    if (cost.damage.dead) rows.push(['状态', '死亡']);
  }
  ['focus', 'strain'].forEach(function (pool) {
    var ch = cost[pool];
    if (ch && turnInt(ch.changed, 0)) rows.push([TURN_POOL_ZH[pool] || pool, turnSigned(ch.changed)]);
  });
  (raw.conditions || []).forEach(function (c) { rows.push(['状态', String(c)]); });
  if (raw.catastrophe_text) rows.push(['灾难', String(raw.catastrophe_text)]);
  if (!rows.length) return '';
  return '<div class="res-cost"><div class="res-cost-title">代价</div>' +
    '<dl class="res-rows">' + rows.map(function (p) {
      return '<dt>' + esc(p[0]) + '</dt><dd>' + esc(p[1]) + '</dd>';
    }).join('') + '</dl></div>';
}

/* 资源变化行（§2.4 / §3.5.3）：`result.unit` 是**结算之后**的白名单视图，
   与画面上这一回合之前的快照（ST.party）比一遍，只报真正变了的项。 */
function unitDeltaHtml(unit) {
  if (!unit || !ST || !ST.party) return '';
  var before = null;
  (ST.party || []).forEach(function (u) { if (u && u.id === unit.id) before = u; });
  if (!before) return '';
  var rows = [];
  var push = function (label, a, b) {
    a = turnInt(a, 0); b = turnInt(b, 0);
    if (a !== b) rows.push([label, turnSigned(b - a)]);
  };
  push('活力', before.vitality, unit.vitality);
  push('韧性', before.poise, unit.poise);
  var poolsA = before.resources || {};
  var poolsB = unit.resources || {};
  Object.keys(TURN_POOL_ZH).forEach(function (pool) {
    push(TURN_POOL_ZH[pool], poolsA[pool], poolsB[pool]);
  });
  if (!rows.length) return '';
  return '<div class="res-delta">' + rows.map(function (p) {
    return '<span class="res-delta-item">' + esc(p[0]) + ' <b>' + esc(p[1]) + '</b></span>';
  }).join('') + '</div>';
}

function grantedHtml(granted) {
  if (!granted || !granted.length) return '';
  var names = granted.map(function (g) {
    return String((g && g.name) || g || '');
  }).filter(Boolean);
  if (!names.length) return '';
  return '<div class="res-granted">获得 ' + esc(names.join(' · ')) + '</div>';
}

/* 流末尾的如实说明（§2.8）：中途断流 / 未持久化 / 恢复失败。 */
function streamFooterHtml() {
  var html = '';
  if (TURN.loadError) {
    html += '<div class="msg msg-note stream-note">' + esc(TURN.loadError) +
      ' <button type="button" class="btn turn-retry" data-stream-retry="1">重试</button></div>';
  }
  if (TURN.note) {
    html += '<div class="msg msg-note stream-note">' + esc(TURN.note) + '</div>';
  }
  return html;
}

function bindStreamFooter() {
  $$('#stream [data-stream-retry]').forEach(function (b) {
    b.addEventListener('click', function () {
      TURN.loadError = '';
      TURN.rendered = '';
      renderStream();
    });
  });
}

function appendStreamFooter(box) {
  $$('#stream .stream-note').forEach(function (n) {
    if (n.parentNode) n.parentNode.removeChild(n);
  });
  var html = streamFooterHtml();
  if (html) box.insertAdjacentHTML('beforeend', html);
  bindStreamFooter();
}

/* 回合进行中的**增量**渲染：只把新条目推进 DOM，已渲染的条目不动。
   顺序不重排——服务端保证 `result` 先于 `narration`（§2.3），前端照序追加。 */
function addStreamEntry(entry, scroll) {
  TURN.log.push(entry);
  var box = turnEl('stream');
  if (!box || TURN.rendered === '' || box.querySelector('.stream-empty, .boot')) {
    renderStream();                          // 首条或容器还没渲染：整块来一次
    return entry;
  }
  appendStreamFooter(box);                    // 先把旧的末尾说明摘掉
  box.insertAdjacentHTML('beforeend', streamItemHtml(entry));
  appendStreamFooter(box);
  TURN.rendered = streamSig();
  if (scroll !== false) scrollToLatest();
  return entry;
}

/* 撤回本地乐观条目（服务端没有落这一回合时用）。 */
function dropStreamEntry(entry) {
  var i = TURN.log.indexOf(entry);
  if (i >= 0) TURN.log.splice(i, 1);
  TURN.rendered = '';
  renderStream();
}

function renderStream() {
  var box = turnEl('stream');
  if (!box) return;
  if (TURN.sessionError) {
    // 会话都没加载出来：既不拉流、也不画任何一局的内容（§2.2 禁演示数据）。
    TURN.log = [];
    box.innerHTML = '';
    TURN.rendered = streamSig();
    return;
  }
  if (streamServerMode()) {
    if (TURN.sid !== SID) resetTurnStream();   // 换了一局：上一局的流不能留下
    clearSessionError();                       // 有真实会话快照：错误态到此为止
    if (streamSig() !== TURN.rendered || !box.childNodes.length) renderServerStream();
  } else {
    renderPlaceholderStream();
  }
  maybeFetchStream();
}

/* 演示态（没有真实会话快照）：沿用占位数据，行为与 U2 之前一致。 */
function renderPlaceholderStream() {
  var box = turnEl('stream');
  var log = (ST && ST.log) || [];
  box.innerHTML = log.length
    ? log.map(streamItemHtml).join('')
    : '<div class="stream-empty">还没有叙事。开局后每一步都会追加在这里。</div>';
  TURN.rendered = streamSig();
}

function renderServerStream() {
  var box = turnEl('stream');
  var html;
  if (!TURN.loaded && !TURN.log.length && !TURN.loadError) {
    // 首屏恢复中的加载态：不盖全屏遮罩，只在流的位置说清楚（§3.5.6）。
    html = '<div class="boot">正在读取叙事…</div>';
  } else if (!TURN.log.length && !TURN.loadError) {
    html = '<div class="stream-empty">还没有叙事。开局后每一步都会追加在这里。</div>';
  } else {
    html = TURN.log.map(streamItemHtml).join('') + streamFooterHtml();
  }
  box.innerHTML = html;
  TURN.rendered = streamSig();
  bindStreamFooter();
}

/* 每次渲染顺手补齐一次增量：服务端可能又落了条目（如移动的 ATLAS 说明）。
   递归是安全的——拉取期间的渲染不会再触发一次（fetching 仍为真）。 */
function maybeFetchStream() {
  if (TURN.fetching || TURN.busy || !streamServerMode()) return;
  loadStreamLog();
}

/* 恢复 / 增量补齐回合流：`GET /api/log?since=<seq>`（B2 已落流）。
   `since` 是**正向**游标：只取比 lastSeq 更新的条目，重进会话不必重传全量。 */
async function loadStreamLog() {
  if (TURN.fetching || !streamServerMode()) return;
  var my = ++TURN.fetchId;
  var sid = SID;
  TURN.fetching = true;
  try {
    try {
      var d = await api('/api/log?since=' + TURN.lastSeq);
      if (my !== TURN.fetchId || sid !== SID) return;   // 迟到的旧响应 / 已换局
      mergeServerEntries((d && d.log) || []);
      TURN.loaded = true;
      TURN.loadError = '';
    } catch (e) {
      if (my !== TURN.fetchId || sid !== SID) return;
      // 一条都没拿到时如实说「没读到」，并留一个重试；已有内容时不打断阅读。
      if (!TURN.log.length) TURN.loadError = '没能读到这一局的叙事：' + e.message;
    }
    renderStream();
  } finally {
    TURN.fetching = false;
  }
}

/* 服务端条目并入本地流：
   1. 游标去重（seq <= lastSeq 的已经收过）；
   2. 同 (kind, text) 的**本地乐观条目**被认领（补上 seq），不新增第二条；
   3. `trace` 不是独立条目，而是挂到最近一条叙事上的痕迹块（§3.5.2）。 */
function mergeServerEntries(entries) {
  (entries || []).forEach(function (raw) {
    if (!raw || typeof raw !== 'object') return;
    var seq = turnInt(raw.seq, 0);
    if (seq && seq <= TURN.lastSeq) return;
    if (seq) TURN.lastSeq = seq;
    if (raw.kind === 'trace') { attachTrace(String(raw.text || '')); return; }
    var entry = {
      seq: seq || null,
      kind: raw.kind,
      text: raw.text,
      speaker: raw.speaker || '',
      pending: false
    };
    for (var i = TURN.log.length - 1; i >= 0; i--) {
      var e = TURN.log[i];
      if (e.pending && e.kind === entry.kind && String(e.text || '') === String(entry.text || '')) {
        e.seq = entry.seq;
        e.pending = false;
        return;
      }
    }
    TURN.log.push(entry);
  });
}

/* 痕迹挂到最近一条叙事条目上；确实没有叙事条目时才自己成一条（照样是痕迹块）。 */
function attachTrace(text) {
  if (!text) return;
  for (var i = TURN.log.length - 1; i >= 0; i--) {
    if (streamKind(TURN.log[i]) !== 'narrate') continue;
    if (!TURN.log[i].trace) { TURN.log[i].trace = text; return; }
    if (TURN.log[i].trace === text) return;      // 同一条痕迹（SSE 已带过）：不重复
    break;
  }
  TURN.log.push({ seq: null, kind: 'trace', text: text, pending: false });
}

/* ══════════════ 行动区 ══════════════
   点一下 = 带 `action_id` 的一回合（§2.3）。已完成 / 机会耗尽的行动就地置灰——
   去重表在服务端快照的 `done_actions` / `action_fails` 里，
   **前端不自己维护一张耗尽表**（§2.4）。 */
function actionList() {
  var scene = (ST && ST.scene) || {};
  var list = (scene.actions && scene.actions.length) ? scene.actions
    : ((ST && ST.actions) || []);
  return Array.isArray(list) ? list : [];
}

function actionLabel(id) {
  var label = '';
  actionList().forEach(function (a) { if (a && a.id === id) label = String(a.label || ''); });
  return label || String(id || '');
}

function actionExhausted(a) {
  var maxFail = turnInt(a && a.max_fail, 0);
  if (!maxFail || !ST || !ST.action_fails) return false;
  return turnInt(ST.action_fails[a.id], 0) >= maxFail;
}

function renderActions() {
  var box = turnEl('actList');
  if (!box) return;
  var list = actionList();
  if (!list.length) {
    box.innerHTML = '<div class="stream-empty">本场景没有可选行动。</div>';
    return;
  }
  box.innerHTML = list.map(function (a, i) {
    var done = !!(ST && ST.done_actions && ST.done_actions[a.id]);
    var spent = done || actionExhausted(a);
    return '<button class="btn act-btn" data-act="' + esc(a.id) + '"' +
      (spent ? ' disabled' : '') + '>' +
      '<span class="act-key">' + (i + 1) + '</span>' +
      '<span class="act-label">' + esc(a.label) + '</span>' +
      (a.hint ? '<span class="act-hint">' + esc(a.hint) + '</span>' : '') +
      (done ? '<span class="act-hint act-spent">已完成</span>'
        : (spent ? '<span class="act-hint act-spent">已用尽</span>' : '')) +
      '</button>';
  }).join('');

  $$('#actList [data-act]').forEach(function (b) {
    if (b.disabled) return;
    b.addEventListener('click', function () { pickAction(b.dataset.act); });
  });
}

/* 行动按钮也是回合入口：带 `action_id` 走同一回合管线。输入区里已经写了话
   就一起带上——服务端以正文为准记 `act`（`_turn_act_text`），两边同口径。 */
function pickAction(id) {
  if (turnBlocked()) return;
  buzz(12);
  runTurn({ text: turnText(), action_id: id });
}

/* ══════════════ 输入区 ══════════════ */

function turnText() {
  var ta = turnEl('turnInput');
  return ta ? String(ta.value || '') : '';
}

function setComposerText(v) {
  var ta = turnEl('turnInput');
  if (!ta) return;
  ta.value = String(v == null ? '' : v);
  growComposer();
  renderTurnCount();
}

/* §1.3.2：桌面 Enter 发送、Shift+Enter 换行；手机用发送按钮，Enter 不劫持。
   判据用**指针精度**而不是屏宽——平板外接键盘时也该能回车发送。 */
function composerEnterSends() {
  if (!window.matchMedia) return true;
  try { return !window.matchMedia('(pointer: coarse)').matches; } catch (e) { return true; }
}

/* 多行自适应：高度跟着内容长，上限交给 CSS 的 max-height（超过就内部滚动）。 */
function growComposer() {
  var ta = turnEl('turnInput');
  if (!ta) return;
  ta.style.height = 'auto';
  var h = ta.scrollHeight;
  if (h > 0) ta.style.height = h + 'px';
}

function renderTurnCount() {
  var el = turnEl('turnCount');
  if (!el) return;
  var left = TURN_TEXT_LIMIT - turnText().length;
  el.textContent = left >= 0 ? ('还可写 ' + left + ' 字') : ('超出 ' + (-left) + ' 字');
  el.classList.toggle('is-over', left < 0);
}

function renderTurnError() {
  var box = turnEl('turnError');
  if (!box) return;
  var msg = TURN.inputError || TURN.error;
  if (!msg) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  box.innerHTML = '<span class="turn-error-text">' + esc(msg) + '</span>' +
    (TURN.error && TURN.retryable
      ? '<button type="button" class="btn turn-retry" data-turn-retry="1">重试</button>' : '');
  var retry = box.querySelector('[data-turn-retry]');
  if (retry) retry.addEventListener('click', function () { retryTurn(); });
}

/* ── 工具条实测高度（与 core 的 --dock-h 同口径）───────────
   core 的 syncDockHeight() 只量**第一个** `.dock`——大厅那条。切到主画面后
   它是 display:none（量到 0，core 的 `h > 0` 守卫于是**保留旧值**），这个变量
   就一直停在大厅的高度上。本片的输入区让主画面工具条明显变高，而提示条
   （`.toast`）用 --dock-h 定位；不跟着更新，提示条会盖住输入区。
   这里在主画面上自己量一次：只写这一个 CSS 变量、量法与 core 一致（0 不写）。
   包装 showView 的写法与 panels.js 包装 renderAux 同理——core.js 是禁改路径，
   片只能从外面挂，不在 core 里加钩子。 */
var CORE_SHOW_VIEW = showView;
showView = function (id) {
  CORE_SHOW_VIEW(id);
  syncPlayDock();
};

function syncPlayDock() {
  if (VIEW !== 'v-play') return;
  var dock = document.querySelector('#v-play .dock');
  if (!dock) return;
  var h = dock.getBoundingClientRect().height;
  if (h > 0) document.documentElement.style.setProperty('--dock-h', Math.round(h) + 'px');
}

/* 输入区三态（§2.0 / §3.5.5）：进行中（aria-busy + 按钮三点 + disabled）/
   成功（清空，正文进流）/ 失败（就地提示 + 可重试）。
   回合中输入框**保持可编辑**，但一律不许二次提交（§1.3.2）；
   会话加载失败时整块禁用（§2.8：这一态下没有可发的回合）。 */
function renderComposer() {
  var box = turnEl('turnComposer');
  var ta = turnEl('turnInput');
  var btn = turnEl('turnSend');
  if (!box || !ta || !btn) return;
  var busy = TURN.busy;
  var cooling = TURN.cooldownUntil > Date.now();
  var dead = !!TURN.sessionError;
  box.setAttribute('aria-busy', busy ? 'true' : 'false');
  box.classList.toggle('is-disabled', dead);
  btn.disabled = busy || cooling || dead;
  ta.disabled = dead;
  btn.setAttribute('aria-busy', busy ? 'true' : 'false');
  ta.setAttribute('aria-invalid', TURN.inputError ? 'true' : 'false');
  renderTurnCount();
  renderTurnError();
  growComposer();
  syncPlayDock();            // 输入区一长高，提示条就得跟着让位
}

/* 429（太频繁）：提示条 + 发送按钮禁 3 秒（§1.3.10），到期自动恢复。 */
function startCooldown() {
  TURN.cooldownUntil = Date.now() + TURN_COOLDOWN_MS;
  if (TURN.cooldownTimer) clearTimeout(TURN.cooldownTimer);
  TURN.cooldownTimer = setTimeout(function () {
    TURN.cooldownUntil = 0;
    TURN.cooldownTimer = null;
    renderComposer();
  }, TURN_COOLDOWN_MS + 50);
  renderComposer();
}

/* ══════════════ 导引者状态（§1.3.8 / §2.8 第一行）══════════════
   数据是 `GET /api/guide/status` 的三个布尔（无需会话）：`chat` 要
   BASE_URL + MODEL + API_KEY，`tts` 只要 BASE_URL + API_KEY，`configured`
   是二者之或（见 prism_guide.status）。**这是能力探测，不是故障告警**：
   离线可玩是设计假设，所以只在顶栏角落与抽屉里安静地说，不弹提示、
   不盖横幅（§1.2 决定 4）。 */
function renderGuideStatus() {
  var dot = turnEl('guideDot');
  var tag = turnEl('guideTag');
  var note = turnEl('guideNote');
  var g = TURN.guide;
  var state = 'off';                    // 灰 = 未配置
  var label = '导引者未配置';
  var noteText = '导引者未配置：本地规则照常，叙事用兜底句';   // §1.3.8 原文
  if (g.chat && g.tts) {
    state = 'on';                       // 亮 = 文字与语音都在
    label = '导引者已配置：文字与语音都可用';
    noteText = '';                      // 全能力时抽屉不写字（设计单只点名两个非满态）
  } else if (g.configured) {
    // 半配置：今天的 status() 只会给出「语音有、文字无」（缺 MODEL）这一种；
    // 「文字有、语音无」在契约上存在（§1.3.8），文案一并留着，按实际布尔选。
    state = 'partial';
    label = g.chat ? '导引者：文字有，语音无' : '导引者：语音有，文字无';
    noteText = label;
  }
  if (dot) {
    dot.setAttribute('data-state', state);
    dot.setAttribute('aria-label', '导引者状态：' + label);
  }
  if (tag) tag.hidden = !!g.configured;          //「本地模式」只在未配置时出现
  if (note) {
    note.hidden = !noteText;
    note.textContent = noteText;
  }
}

async function loadGuideStatus() {
  try {
    var d = await api('/api/guide/status');
    TURN.guide = {
      chat: !!(d && d.chat),
      tts: !!(d && d.tts),
      configured: !!(d && d.configured)
    };
  } catch (e) {
    // 探测拿不到就按「未配置」显示：本地规则照常可玩，说法也不夸大。
    TURN.guide = { chat: false, tts: false, configured: false };
  }
  renderGuideStatus();
}

/* ══════════════ 会话加载失败态（§2.2 / §2.8 最后一行）══════════════
   会话拉不出来时**不**退回演示数据（placeholderState）——那会让玩家以为
   「这一局还在」，是目前明确禁止的一种误导。目标态：主画面上一张错误卡
   （标题 + 服务端原文原因 + 返回列表 / 重试），输入区与行动区一并禁用。
   会话的加载由大厅片（js/lobby.js）发起，错误卡与禁用由本片渲染——
   本片是主画面回合态的持有者（输入区 / 行动区 / 叙事流都在这里）。 */
function showSessionError(message, retry) {
  TURN.sessionError = String(message || '该会话暂不可用');
  TURN.sessionRetry = (typeof retry === 'function') ? retry : null;
  // 不复用**上一局**的画面：留着一张别的局的场景卡 / 叙事流，比空着更容易误导。
  var card = turnEl('sceneCard');
  if (card) card.innerHTML = '';
  TURN.log = [];
  TURN.rendered = '';
  disableActZone(true);
  renderSessionError();
  renderStream();          // 这一态下只留空位，不拉流、不画演示数据
  renderComposer();
}

function clearSessionError() {
  if (!TURN.sessionError) return;
  TURN.sessionError = '';
  TURN.sessionRetry = null;
  disableActZone(false);
  renderSessionError();
  renderComposer();
}

/* 错误卡：--danger 左细边 + 一行原因 + 两个动作（§3.5.6 的就地错误卡口径，
   原因文本用服务端 `error` 原文，不自己编话术）。 */
function renderSessionError() {
  var box = turnEl('playError');
  if (!box) return;
  if (!TURN.sessionError) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  box.innerHTML =
    '<p class="play-error-title">该会话暂不可用</p>' +
    '<p class="play-error-reason">' + esc(TURN.sessionError) + '</p>' +
    '<div class="play-error-actions">' +
      '<button type="button" class="btn" data-play-back="1">返回列表</button>' +
      '<button type="button" class="btn btn-primary" data-play-retry="1">重试</button>' +
    '</div>';
  var back = box.querySelector('[data-play-back]');
  var retry = box.querySelector('[data-play-retry]');
  if (back) back.addEventListener('click', function () {
    clearSessionError();
    showView('v-lobby');
    // 大厅片的列表刷新：片之间只在运行时按需取用，加载期互不依赖。
    if (typeof refreshSaves === 'function') refreshSaves();
  });
  if (retry) retry.addEventListener('click', function () {
    if (TURN.busy || writeInFlight()) return;
    var again = TURN.sessionRetry;
    clearSessionError();
    if (again) again();
  });
}

/* 行动区（含「前往」出口）整体禁用：不去改别的片的绑定，只把区块内的按钮
   一律 `disabled` 并加一个类把视觉一起收掉；`1–5` 快捷键走的是这些按钮的
   `click()`，禁用的按钮不会被触发（§2.8 的「行动区禁用」）。 */
function disableActZone(on) {
  var zone = turnEl('actZone');
  if (!zone) return;
  zone.classList.toggle('is-disabled', !!on);
  $$('#actZone button').forEach(function (b) { b.disabled = !!on; });
}

/* ══════════════ 一个回合 ══════════════ */

/* 回合入口的统一闸门：本片在途 / core 的统一写守卫（§2.0）/ 会话加载失败态
   （§2.8：这一态下没有可发的回合）。三处入口共用，避免各写一遍。 */
function turnBlocked() {
  return TURN.busy || writeInFlight() || !!TURN.sessionError;
}

/* 提交前先本地校验（§1.3.2）：空输入不发、超长**不发请求**就地提示。 */
function submitTurn() {
  if (turnBlocked()) return;
  var text = turnText();
  if (!text.trim()) return;
  if (text.length > TURN_TEXT_LIMIT) {
    TURN.inputError = '这句话太长';
    renderComposer();
    var ta = turnEl('turnInput');
    if (ta) { try { ta.focus(); } catch (e) { /* 忽略 */ } }
    return;
  }
  runTurn({ text: text, action_id: '' });
}

function retryTurn() {
  if (turnBlocked()) return;
  TURN.error = '';
  TURN.retryable = false;
  renderComposer();
  runTurn({ text: TURN.lastText, action_id: TURN.lastActionId });
}

async function runTurn(opts) {
  var text = String((opts && opts.text) || '');
  var actionId = String((opts && opts.action_id) || '');
  var seen = text.trim();

  TURN.error = '';
  TURN.retryable = false;
  TURN.inputError = '';
  TURN.note = '';
  TURN.lastText = text;
  TURN.lastActionId = actionId;
  TURN.busy = true;
  setWriteInFlight(true);     // 回合是写请求：登记进 core 的统一在途守卫（§2.0）

  // 「我说的话」立刻可见（乐观）。服务端同一回合也会把它落进日志，收尾时
  // 按 (kind, text) 认领这条，不会出现两条（见 mergeServerEntries）。
  var local = (seen || actionId)
    ? addStreamEntry({ seq: null, kind: 'act', text: seen || actionLabel(actionId), pending: true }, false)
    : null;
  renderComposer();

  var outcome;
  try {
    outcome = await postTurn(text, actionId);
  } catch (e) {
    outcome = { ok: false, message: '这一回合没能开始，请重试', retryable: true };
  } finally {
    TURN.busy = false;
    setWriteInFlight(false);
  }

  if (!outcome.ok) {
    failTurn(outcome, local, text);
    return;
  }
  setComposerText('');        // 成功：正文已经在流里了
  if (!outcome.complete) {
    // §2.8：SSE 中途断流（没有 done）——已收到的部分保留，如实说一句，
    // 并**必须**释放输入，否则玩家会卡在禁用态。
    TURN.note = '这一回合没说完';
  }
  renderComposer();
  renderStream();
  await afterTurn();
}

/* 开一个回合：`POST /api/guide/turn`。**两种 200 都要吃**（§2.3）：
   `text/event-stream` 边收边渲染；`淡出` / `跳过这段` / 离线兜底回整段 JSON。
   这里不走 core 的 api()——SSE 要边收边渲染，而 api() 会把响应体整个读成 JSON；
   代价是 4xx 的处置在下面按 core 的口径复写一遍（会话失效照旧清 SID）。 */
async function postTurn(text, actionId) {
  var headers = { 'Content-Type': 'application/json' };
  if (SID) headers['X-Session'] = SID;
  var res;
  try {
    res = await fetch('/api/guide/turn', {
      method: 'POST', headers: headers,
      body: JSON.stringify({ text: text, action_id: actionId })
    });
  } catch (e) {
    return { ok: false, message: '无法连接服务，请确认后端仍在运行', retryable: true };
  }
  var ctype = String(res.headers.get('content-type') || '').toLowerCase();
  if (!res.ok) return await turnFailure(res);
  if (ctype.indexOf('text/event-stream') >= 0 && res.body && res.body.getReader) {
    return { ok: true, complete: await readTurnStream(res) };
  }
  var raw = '';
  try { raw = await res.text(); } catch (e) { /* 读体失败：按整段处理 */ }
  return parseBufferedTurn(raw);
}

/* 4xx / 429 / 5xx：状态行之前服务端一律回固定 JSON `{"error": …}`（§5.2），
   前端**照抄它的原文**，不自己编话术（§1.3.10 的错误码口径）。 */
async function turnFailure(res) {
  var data = null;
  try { data = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
  var message = String((data && data.error) || ('请求失败（' + res.status + '）'));
  var out = { ok: false, message: message, status: res.status, retryable: res.status >= 500 };
  if (res.status === 429) {
    out.tone = 'toast';          // §2.8：速率限制走提示条 + 按钮禁 3 秒
    out.cooldown = true;
    out.retryable = false;
  } else if (res.status === 409) {
    out.tone = 'toast';          // §2.8：战斗还没结束
  } else if (res.status === 400 && /会话/.test(message)) {
    out.tone = 'toast';          // §2.8：清 SID + 回大厅（与 core.api() 同口径）
    out.sessionGone = true;
  }
  return out;
}

/* 整段响应（JSON 或一次到齐的 SSE）：喂给同一个 applyTurnEvent，
   于是「淡出 / 跳过 / 离线兜底」与流式走**完全相同**的渲染与收尾。 */
function parseBufferedTurn(raw) {
  var text = String(raw || '').trim();
  if (text && text.charAt(0) === '{') {
    var data = null;
    try { data = JSON.parse(text); } catch (e) { data = null; }
    if (data && data.narration) applyTurnEvent('narration', data.narration);
    return { ok: true, complete: true };
  }
  var done = false;
  text.split(/\r?\n\r?\n/).forEach(function (block) {
    if (block.trim() && handleSseBlock(block)) done = true;
  });
  return { ok: true, complete: done };
}

/* 边收边渲染。返回是否收到了 `done`——没收到就是中途断流（§2.8）。 */
async function readTurnStream(res) {
  var reader = res.body.getReader();
  var decoder = new TextDecoder('utf-8');
  var buf = '';
  var done = false;
  for (;;) {
    var chunk;
    try {
      chunk = await reader.read();
    } catch (e) {
      return done;                     // 读流中断：已收到的部分保留
    }
    if (chunk.done) return done;
    buf += decoder.decode(chunk.value, { stream: true });
    var blocks = buf.split(/\n\n/);
    buf = blocks.pop();                // 最后一块可能被截断：留到下一次
    for (var i = 0; i < blocks.length; i++) {
      if (handleSseBlock(blocks[i])) done = true;
    }
  }
}

/* 一个 SSE 块：`event:` + 一行 JSON `data:`（见 notdnd_web.py 的 _sse）。
   解析失败只丢这一块，不把整回合报废——已到的条目照常留在流里。 */
function handleSseBlock(block) {
  var name = '';
  var data = '';
  String(block).split('\n').forEach(function (line) {
    line = line.replace(/\r$/, '');
    if (line.indexOf('event:') === 0) name = line.slice(6).trim();
    else if (line.indexOf('data:') === 0) data += line.slice(5).trim();
  });
  if (!name) return false;
  var payload = null;
  try { payload = JSON.parse(data || '{}'); } catch (e) { return false; }
  return applyTurnEvent(name, payload || {});
}

/* 事件 → 界面（§2.3 的顺序就是到达顺序，**不重排**）：
   result（判定卡）→ narration（叙事 / 兜底）→ usage（不渲染）→ done（收尾）。 */
function applyTurnEvent(name, payload) {
  if (name === 'result') {
    // 判定卡只有流里有（规则层结果不落 Session.log），所以它不带 pending：
    // 不会有人来认领它。
    addStreamEntry({ seq: null, kind: 'result', text: '', result: payload }, false);
    return false;
  }
  if (name === 'narration') {
    addStreamEntry({
      seq: null, kind: 'narrate', text: payload.text || '', pending: true,
      beats: payload.beats || null,                    // 切拍与朗读属 U5（js/voice.js）
      trace: payload.trace || ''
    }, true);
    return false;
  }
  if (name === 'fallback') {
    addStreamEntry({ seq: null, kind: 'narrate', text: payload.text || '', pending: true }, true);
    return false;
  }
  if (name === 'usage') {
    TURN.usage = payload;        // 只喂调试面板 / 日志，不渲染给玩家（§2.3）
    return false;
  }
  if (name === 'done') {
    if (payload.status === 'error') {
      // 服务端明说这一回合没持久化（`done` 是唯一的收尾口径），如实转告。
      TURN.note = '这一回合没能写进存档（服务端保存失败）';
    }
    return true;
  }
  return false;
}

function failTurn(outcome, local, text) {
  if (local) dropStreamEntry(local);          // 服务端没落这一回合：撤回乐观条目
  if (text && !turnText()) setComposerText(text);   // 把话还回输入框，不丢玩家打的字
  if (outcome.sessionGone) {
    SID = '';
    try { localStorage.removeItem(SID_KEY); } catch (e) { /* 隐私模式 */ }
    closeAllSheets();
    showView('v-lobby');
    toast(outcome.message);
  } else if (outcome.tone === 'toast') {
    toast(outcome.message);
    if (outcome.cooldown) startCooldown();
  } else {
    TURN.error = outcome.message;             // 就地提示，输入区保持可用
    TURN.retryable = !!outcome.retryable;
  }
  TURN.rendered = '';
  renderComposer();
  renderStream();
}

/* 收尾（§2.3 的 done）：先按游标补齐这一回合的权威条目（act / narrate / trace
   都带 seq），再重取整局视图——数值可能变了（活力 / 压力 / 行动是否耗尽）。 */
async function afterTurn() {
  await loadStreamLog();
  await refreshTurnSession();
}

async function refreshTurnSession() {
  if (!SID) return;
  var my = ++TURN.sessionId;
  try {
    var view = await api('/api/session');
    if (my !== TURN.sessionId) return;
    applyState(view);          // 顶栏 / 场景卡 / 行动区跟着更新；叙事流不重播
  } catch (e) {
    /* 刷新失败不影响已经收到的这一回合：下一次成功操作或重进会话会再拉。 */
  }
}

/* ══════════════ 本片事件绑定 ══════════════ */

function bindTurn() {
  var ta = turnEl('turnInput');
  var btn = turnEl('turnSend');

  if (ta) {
    ta.addEventListener('input', function () {
      if (TURN.inputError) TURN.inputError = '';
      renderComposer();
    });
    ta.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' || e.shiftKey) return;                // Shift+Enter 换行
      if (e.isComposing || e.keyCode === 229) return;             // 输入法候选没上屏：别抢回车
      if (!composerEnterSends()) return;                          // 手机：回车不劫持
      e.preventDefault();
      submitTurn();
    });
  }
  if (btn) btn.addEventListener('click', function () { submitTurn(); });

  // 视口变了（横竖屏、桌面窗口缩放、手机键盘弹起）要重量一次工具条高度。
  window.addEventListener('resize', syncPlayDock);

  renderComposer();
  renderSessionError();
  renderGuideStatus();
  loadGuideStatus();      // 能力探测：一次只读请求，拿不到就按「未配置」显示
  window.__TURN = turnSnapshot;
}

onInit(bindTurn);

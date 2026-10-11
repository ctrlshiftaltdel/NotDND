/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U2 / A4：跑团主循环（输入区 + 叙事流 + 行动区）
   一个回合走完：输入 → 判定卡 → 叙事 → 兜底 → 收尾（UI-DESIGN.md §2.3）。

   · 输入区固定在工具条里（`#turnComposer`），Enter 发送 / Shift+Enter 换行，
     长度上限 2000 字符本地拦住（与后端 GUIDE_TEXT_LIMIT 同值）；
   · 两个入口都走**行动管线** `POST /api/action/propose`（A4，ACTION-DESIGN §9）：
     输入框的一句话是 `mode: "nl"`，行动按钮是 `mode: "action"`（**不需要 AI**）；
     R0/R1 直通提交，R2+ 先弹 L2 确认（§6.4），澄清式提案只问一个问题（§6.3）；
   · `POST /api/action/commit` 有**两种 200**——`text/event-stream`（增量渲染
     `result` → `narration`|`fallback` → `usage` → `done`，§9.3）与整段 JSON，
     两种都要吃；
   · `淡出` / `跳过这段` 两个整句口令仍走旧的 `POST /api/guide/turn`（§5.10）：
     它们是「这一回合怎么讲」的口令，不是一条行动；
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
  rendered: '',        // 已渲染的签名：状态没变就不重画（避免 live 区重播整段）
  /* ── M8-A4：即时行动的状态（§9 管线）── */
  risk: {},           // `GET /api/action/catalog`：action_id → 风险级（只画角标）
  catalogKey: '',     // catalog 归属的会话 + 行动 id 集合：变了才重取（失败也记，免得反复打）
  catalogId: 0,       // catalog 请求单调序号：迟到的旧响应丢弃
  pending: null,      // 待确认的提案：{proposal, base_seq, risk, costs_preview, key, verb, local}
  clarify: null,      // 待回答的澄清：{clarify_id, question, options, text}
  lastRequest: null,  // 失败重试用：上一个入口请求（{kind, text, action_id, clarify_id, answer}）
  confirming: false   // 确认令牌请求在途（防连点）
};

var TURN_TEXT_LIMIT = 2000;      // 与 notdnd_web.GUIDE_TEXT_LIMIT 同值（§1.3.2）
var TURN_COOLDOWN_MS = 3000;     // 429「太频繁」后本地禁用发送的时间（§1.3.10）

/* 整句口令（§5.10）：与 notdnd_web.GUIDE_FADE_WORD / GUIDE_SKIP_WORD 同文。
   判据是**整句相等**（先 strip），不是关键字包含。 */
var TURN_FADE_WORD = '淡出';
var TURN_SKIP_WORD = '跳过这段';

/* 服务端固定短语（§9.5）：用于把 409 分成「可重试」与「已经提交过」两类处置。
   与 notdnd_web 的常量同文——两边改一处要一起改。 */
var TURN_MSG_STALE_SEQ = '状态已变，请重试';
var TURN_MSG_NEED_CONFIRM = '需要确认';
var TURN_MSG_ALREADY_COMMITTED = '该提案已提交';

/* NL 提案离线（§9.2 的 `type: "offline"` 是 200、**不是错误**）：这句话没变成
   行动，得说清楚它去哪了、以及还能做什么（措辞沿用 §1.3.8 的「未配置」）。 */
var TURN_OFFLINE_NOTE = '导引者未配置：这句话没法变成行动。本地规则照常——点下面的行动按钮照样可以做事。';

/* 风险级的中文短名（ACTION-DESIGN §6.1 的「含义」栏收成短名）：只在按钮角标与
   确认框里出现，**不参与任何判定**；是否真确认以服务端的 `needs_confirm` 为准。 */
var TURN_RISK_ZH = {
  R0: '无副作用', R1: '可逆', R2: '消耗资源', R3: '高风险', R4: '世界级'
};
/* 行动结果的状态（§5.1 的 status 枚举）：文字与判定卡的档位表同一套词。 */
var TURN_STATUS_ZH = {
  success: '成功', partial: '险成', failure: '挫败', blocked: '受阻'
};
/* 时间单位（prism_core.TIME_COST_UNITS 的中文名）。 */
var TURN_TIME_UNIT_ZH = {
  round: '轮', turn: '回合', minute: '分钟', hour: '小时', watch: '更', day: '天'
};

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
    text: turnText().length,
    pending: !!TURN.pending,
    pendingRisk: TURN.pending ? String(TURN.pending.risk || '') : '',
    clarify: !!TURN.clarify,
    risk: Object.assign({}, TURN.risk)
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
  // 换了一局：上一局在办的提案 / 澄清与风险角标都不能留下（串味）。
  TURN.pending = null;
  TURN.clarify = null;
  TURN.risk = {};
  TURN.catalogKey = '';
  closeActionSheet();
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
    // 正文块本身可能被 U5 按 `beats` 切成拍，见 narrateBodyHtml。
    return '<div class="msg msg-narrate">' + narrateBodyHtml(e) +
      (e.trace ? traceBlockHtml(e.trace) : '') + '</div>';
  }
  return '<div class="msg msg-note"><span class="msg-text">' + esc(e.text) + '</span></div>';
}

/* 叙事条目的正文块。含 `beats` 的条目交给 js/voice.js 的**可选**全局
   `voiceNarrateHtml(entry)` 按拍切段（片 U5 的唯一衔接点）：一拍 = 一个段落，
   对白拍带竖线 / 说话人名 / 「朗读」按钮。

   它不存在（没加载 voice.js）、返回空、或自己抛了异常时，走下面这行 U2 的
   整段渲染——默认渲染与既有选择器语义（`.msg-text`）一字不变。该函数只**读**
   条目数据，不改 TURN.log。 */
function narrateBodyHtml(e) {
  if (e && e.beats && e.beats.length && typeof voiceNarrateHtml === 'function') {
    try {
      var html = voiceNarrateHtml(e);
      if (typeof html === 'string' && html) return html;
    } catch (err) { /* 切拍失败不拖垮这一条叙事：退回整段渲染 */ }
  }
  return '<span class="msg-text">' + esc(e && e.text) + '</span>';
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
   规则层日志里的 `result` 条目只有正文（没有结构化载荷），按纯文本画。

   两种 `result` 载荷共用这一张卡：旧 `perform_action` 结果（`outcome` 五档）与
   M8 行动结果契约（ACTION-DESIGN §5.1，带 `committed` / `costs`）——后者没有
   `outcome`，交给 actionResultHtml（见下）。 */
function resultCardHtml(e) {
  var r = e && e.result;
  if (!r) {
    return '<div class="msg msg-result"><span class="msg-text">' + esc(e && e.text) + '</span></div>';
  }
  if (typeof r.committed === 'boolean' && Array.isArray(r.costs)) {
    return actionResultHtml(e, r);
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

/* ── 行动结果卡（M8-A4，ACTION-DESIGN §5.1）──────────────
   管线提交后的 `result` 是**已提交的事实**：状态徽标（成功 / 险成 / 挫败 /
   受阻）+ 行动名 + 代价块 + 变化块。只画结果里真有的结构化条目，不解释、
   不补话术；`discovered_facts` 只记「发现了什么」（id + 可见范围），正文归
   叙事，所以这里不渲染它（§5.1 的字段说明）。 */
function actionResultHtml(e, r) {
  var status = String(r.status || '');
  var zh = TURN_STATUS_ZH[status] || '结算';
  var tone = status === 'success' ? 'success'
    : (status === 'partial' ? 'accent' : 'danger');
  var action = (e && e.action) ? String(e.action) : '';

  var head = '<div class="res-head">' +
    '<span class="res-badge" data-tone="' + tone + '" data-solid="false">' + esc(zh) + '</span>' +
    (action ? '<span class="res-action">' + esc(action) + '</span>' : '') +
    (status === 'partial' ? '<span class="res-flag">附一个代价</span>' : '') +
    (r.committed === false ? '<span class="res-flag is-danger">未提交</span>' : '') +
  '</div>';

  var cost = actionRowsBlock('res-cost', '代价', actionCostRows(r.costs));
  var effects = actionRowsBlock('res-effects', '变化', actionEffectRows(r.effects));

  return '<div class="msg msg-result" data-outcome="' + esc(status) + '">' +
    head + cost + effects + '</div>';
}

/* 代价 / 变化共用一个「细边 + 逐项列」的块；空清单不画空块（§2.4 的口径）。 */
function actionRowsBlock(cls, title, rows) {
  if (!rows || !rows.length) return '';
  return '<div class="' + cls + '">' +
    '<div class="res-cost-title">' + esc(title) + '</div>' +
    '<dl class="res-rows">' + rows.map(function (p) {
      return '<dt>' + esc(p[0]) + '</dt><dd>' + esc(p[1]) + '</dd>';
    }).join('') + '</dl></div>';
}

/* `costs[]` → 行：时间 / 资源池 / 活力用既有中文名（§2.4 不写自造词），
   其余 kind 照原样列出来（不猜、不合并）。 */
function actionCostRows(costs) {
  var rows = [];
  (costs || []).forEach(function (c) {
    if (!c || typeof c !== 'object') return;
    var kind = String(c.kind || '');
    var value = turnInt(c.value, 0);
    if (kind === 'time') {
      var unit = TURN_TIME_UNIT_ZH[String(c.unit || '')] || String(c.unit || '');
      if (value > 0) rows.push(['时间', value + (unit ? ' ' + unit : '')]);
    } else if (kind === 'resource') {
      var pool = String(c.pool || '');
      rows.push([TURN_POOL_ZH[pool] || pool || '资源', '−' + Math.abs(value)]);
    } else if (kind === 'vitality') {
      rows.push(['活力', '−' + Math.abs(value)]);
    } else if (kind) {
      rows.push([kind, value > 0 ? '−' + Math.abs(value) : String(value)]);
    }
  });
  return rows;
}

/* `effects[]` → 行：状态（condition）与奖励（reward）用中文标签，其余 kind
   照原样列出来。 */
function actionEffectRows(effects) {
  var rows = [];
  (effects || []).forEach(function (f) {
    if (!f || typeof f !== 'object') return;
    var kind = String(f.kind || '');
    var add = String(f.add == null ? '' : f.add);
    if (kind === 'condition') { if (add) rows.push(['状态', add]); return; }
    if (kind === 'reward') { if (add) rows.push(['获得', add]); return; }
    if (add) rows.push([kind || '变化', add]);
  });
  return rows;
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
   3. `trace` 不是独立条目，而是挂到最近一条叙事上的痕迹块（§3.5.2）；
   4. 叙事条目带 `beats` 时**原样透传**（M4-B5）：刷新 / 重进会话后恢复的历史
      叙事与即时渲染同口径——按拍切段、保留朗读入口。 */
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
    // 服务端无拍时不写这个键，这里同样不补空字段（条目形状与 B2 一致）。
    if (raw.beats && raw.beats.length) entry.beats = raw.beats;
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
   点一下 = 一条**显式行动**（`mode: "action"`，§9.2）——不需要 AI（§7.1）。
   已完成 / 机会耗尽的行动就地置灰——去重表在服务端快照的 `done_actions` /
   `action_fails` 里，**前端不自己维护一张耗尽表**（§2.4）。
   风险级只用来提前提示「需确认」（§6.4）：是否真确认以 propose 回的
   `needs_confirm` 为准（服务端在场景行动表上读 `risk`）。 */
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

/* 风险角标：R2–R4 会先弹确认（§6.4），R0/R1 直通——角标只说前者。
   行动表没写风险级时服务端按最保守的 R2 处理（`_action_risk`），所以那一格
   也标「需确认」；行动单还没回来（map 里没这个 id）时什么都不说。 */
function actionNeedsConfirm(id) {
  var key = String(id || '');
  if (!(key in TURN.risk)) return false;
  var risk = String(TURN.risk[key] || '');
  if (!risk) return true;
  return risk === 'R2' || risk === 'R3' || risk === 'R4';
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
      (actionNeedsConfirm(a.id) ? '<span class="act-hint act-risk">需确认</span>' : '') +
      (done ? '<span class="act-hint act-spent">已完成</span>'
        : (spent ? '<span class="act-hint act-spent">已用尽</span>' : '')) +
      '</button>';
  }).join('');

  $$('#actList [data-act]').forEach(function (b) {
    if (b.disabled) return;
    b.addEventListener('click', function () { pickAction(b.dataset.act); });
  });
  maybeLoadCatalog();
}

/* 行动单（§9.1 `GET /api/action/catalog`）：只取 id / label / 风险级。
   列表本身仍画规则快照（它带已完成 / 已用尽），catalog 只补「需确认」角标；
   拿不到就**不画角标**、也不挡任何操作——离线时 catalog 同样可用（§7.1）。 */
function maybeLoadCatalog() {
  if (!SID || TURN.sessionError) return;
  var ids = actionList().map(function (a) { return String((a && a.id) || ''); })
    .filter(Boolean);
  if (!ids.length) return;
  var key = SID + ':' + ids.join(',');
  if (key === TURN.catalogKey) return;
  loadActionCatalog(key);
}

async function loadActionCatalog(key) {
  var my = ++TURN.catalogId;
  var sid = SID;
  TURN.catalogKey = key;         // 先记下：失败也不反复重打（角标只是提示）
  try {
    var d = await api('/api/action/catalog');
    if (my !== TURN.catalogId || sid !== SID) return;   // 迟到的旧响应 / 已换局
    var map = {};
    ((d && d.actions) || []).forEach(function (a) {
      if (a && a.id) map[String(a.id)] = String(a.risk || '');
    });
    TURN.risk = map;
    renderActions();             // 角标可能出现：行动区重画一次
  } catch (e) {
    /* 拿不到行动单不改变任何可用性：没有角标，按钮照旧能点。 */
  }
}

/* 行动按钮也是回合入口：整条走行动管线（`mode: "action"`，§9.2）。
   输入框里已经写了的话**不**并入这次提交——显式行动就是这条行动本身
   （§7.1：显式行动不需要 AI，也不经过解析段）；输入框内容原样留着。 */
function pickAction(id) {
  if (turnBlocked()) return;
  buzz(12);
  runAction({ kind: 'action', action_id: id });
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
  if (text.length > TURN_TEXT_LIMIT) { turnInputTooLong(); return; }
  // 两个整句口令仍走旧的导引者回合（§5.10）：它们是「这一回合怎么讲」的口令，
  // 不是一条行动——行动管线里没有对应语义（拒绝会把它说成「做不了这件事」）。
  if (isTurnCommand(text)) { runTurn({ text: text, action_id: '' }); return; }
  runAction({ kind: 'nl', text: text });
}

/* 超长：不发请求，就地提示并把焦点送回输入框（§1.3.2）。 */
function turnInputTooLong() {
  TURN.inputError = '这句话太长';
  renderComposer();
  var ta = turnEl('turnInput');
  if (ta) { try { ta.focus(); } catch (e) { /* 忽略 */ } }
}

/* 失败重试：把**上一个入口请求**原样再来一遍。行动管线会重新提案（拿新的
   `base_seq` 与新的 `proposal_id`），所以过期 / 需要确认这类 409 重试是安全的。 */
function retryTurn() {
  if (turnBlocked()) return;
  TURN.error = '';
  TURN.retryable = false;
  renderComposer();
  var req = TURN.lastRequest;
  if (!req) {
    runTurn({ text: TURN.lastText, action_id: TURN.lastActionId });
    return;
  }
  if (req.kind === 'turn') runTurn({ text: req.text, action_id: req.action_id || '' });
  else runAction(req);
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
  TURN.lastRequest = { kind: 'turn', text: text, action_id: actionId };
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
    var entry = { seq: null, kind: 'result', text: '', result: payload };
    // 行动结果契约（§5.1）自己不带行动名：从**在办提案**的 `intent.verb` 取
    // 标签（与日志同口径：场景行动表里的 label）。
    if (payload && typeof payload.committed === 'boolean' && TURN.pending) {
      entry.action = actionLabel(TURN.pending.verb);
    }
    addStreamEntry(entry, false);
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

/* ══════════════ 即时行动：/api/action/*（M8-A4，ACTION-DESIGN §9）══════════════
   两个入口（输入框的一句话 / 行动按钮）都从这里走：
     提案 `propose` →（R2+ 确认 `confirm` / 一次性澄清回答）→ 提交 `commit`。
   提交是 SSE：`result` → `narration`|`fallback` → `usage` → `done`（§9.3），
   前端**不重排**（§2.3 的顺序就是到达顺序）。 */

/* 整句口令的判据（§5.10）：与 notdnd_web 一样是 strip 后**整句相等**。 */
function isTurnCommand(text) {
  var t = String(text || '').trim();
  return t === TURN_FADE_WORD || t === TURN_SKIP_WORD;
}

/* 日志里的「我说的话」正文：与 notdnd_web._turn_act_text 同口径——
   显式行动用场景行动表的 label；NL 提案用 `intent.verb` 的 label（拿不到就用
   verb 本身）。乐观条目与随后 `?since=` 拉回的权威条目按 (kind, text) 认领，
   所以两边必须同口径，否则会看到两条。 */
function actTextForVerb(verb) {
  var id = String(verb || '');
  return id ? actionLabel(id) : '';
}

/* 提案请求体（§9.2）：`mode: "action"` 只带 `action_id`（不需要 AI）；
   `mode: "nl"` 带原话，回答澄清时再带 `clarify_id` + `answer`。 */
function actionRequestBody(req) {
  var kind = String((req && req.kind) || 'nl');
  if (kind === 'action') {
    return { mode: 'action', action_id: String(req.action_id || ''), params: {} };
  }
  var body = { mode: 'nl', text: String((req && req.text) || '') };
  var clarifyId = String((req && req.clarify_id) || '');
  if (clarifyId) {
    body.clarify_id = clarifyId;
    body.answer = String((req && req.answer) || '');
  }
  return body;
}

/* 幂等键（§9.4）：`^[A-Za-z0-9_-]{8,64}$`。同一次提交的重试**沿用同一把键**
   （服务端据此返回首次结果、不重复结算）；换了提案才换新键。 */
function actionIdemKey() {
  return 'k' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}

/* 行动管线的写请求（JSON 回执）。core.api() 只把错误文本抛出来、拿不到状态码，
   而 §9.5 的错误要按码分处置（429 冷却 / 409 可重试 / 400 会话失效），所以这里
   按 core.api() 的口径复写一遍：同 X-Session、同会话失效清理、同单一在途守卫。 */
async function actionPost(path, body, extraHeaders) {
  if (writeInFlight()) {
    return { ok: false, blocked: true, message: '上一项操作还没完成，请稍候' };
  }
  var headers = { 'Content-Type': 'application/json' };
  if (SID) headers['X-Session'] = SID;
  Object.keys(extraHeaders || {}).forEach(function (k) { headers[k] = extraHeaders[k]; });
  setWriteInFlight(true);
  try {
    var res;
    try {
      res = await fetch(path, { method: 'POST', headers: headers,
                                body: JSON.stringify(body || {}) });
    } catch (e) {
      return { ok: false, message: '无法连接服务，请确认后端仍在运行', retryable: true };
    }
    var data = null;
    try { data = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
    if (!res.ok) return actionFailure(res.status, data);
    return { ok: true, data: data || {} };
  } finally {
    setWriteInFlight(false);      // 成功 / 失败 / 异常一律恢复，不留死在途
  }
}

/* 提交（§9.3）：这是**流式**的写请求，在途守卫要**跨整条流**持有，
   所以不走 core.api()——与旧回合同一手法（见 postTurn 的注释）。 */
async function actionCommit(pending, confirmToken) {
  if (writeInFlight()) {
    return { ok: false, blocked: true, message: '上一项操作还没完成，请稍候' };
  }
  var headers = { 'Content-Type': 'application/json',
                  'Idempotency-Key': String(pending.key || '') };
  if (SID) headers['X-Session'] = SID;
  var body = { proposal: pending.proposal, base_seq: pending.base_seq };
  if (confirmToken) body.confirm_token = confirmToken;
  setWriteInFlight(true);
  try {
    var res;
    try {
      res = await fetch('/api/action/commit', { method: 'POST', headers: headers,
                                                body: JSON.stringify(body) });
    } catch (e) {
      return { ok: false, message: '无法连接服务，请确认后端仍在运行', retryable: true };
    }
    var ctype = String(res.headers.get('content-type') || '').toLowerCase();
    if (!res.ok) {
      var data = null;
      try { data = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
      return actionFailure(res.status, data);
    }
    if (ctype.indexOf('text/event-stream') >= 0 && res.body && res.body.getReader) {
      return { ok: true, complete: await readTurnStream(res) };
    }
    var raw = '';
    try { raw = await res.text(); } catch (e) { /* 读体失败：按整段处理 */ }
    return parseBufferedTurn(raw);
  } finally {
    setWriteInFlight(false);
  }
}

/* 状态行之前的失败（§9.5）：固定短语原文照抄，只按码分处置。 */
function actionFailure(status, data) {
  var message = String((data && data.error) || ('请求失败（' + status + '）'));
  var out = { ok: false, message: message, status: status, retryable: status >= 500 };
  if (status === 429) {
    out.tone = 'toast';                       // §2.8：速率限制走提示条 + 禁 3 秒
    out.cooldown = true;
    out.retryable = false;
  } else if (status === 400 && /会话/.test(message)) {
    out.tone = 'toast';                       // 会话失效：清 SID + 回大厅（同 core.api()）
    out.sessionGone = true;
  } else if (status === 409 && message === TURN_MSG_STALE_SEQ) {
    out.retryable = true;                     // §7.4：重读状态再提一次（重试 = 重新提案）
  } else if (status === 409 && message === TURN_MSG_NEED_CONFIRM) {
    out.retryable = true;                     // 确认令牌过期：重试会重新走确认
  } else if (status === 409 && message === TURN_MSG_ALREADY_COMMITTED) {
    out.refresh = true;                       // 服务端说这提案提交过了：别再提，补齐权威条目
  }
  return out;
}

/* ── 入口：一次即时行动（提案 →（确认 / 澄清）→ 提交）── */
async function runAction(req) {
  if (turnBlocked()) return;
  var kind = String((req && req.kind) || 'nl');
  var text = String((req && req.text) || '');
  var actionId = String((req && req.action_id) || '');
  if (kind === 'action') {
    if (!actionId) return;
  } else if (!text.trim()) {
    return;
  }
  if (text.length > TURN_TEXT_LIMIT) { turnInputTooLong(); return; }

  TURN.error = '';
  TURN.retryable = false;
  TURN.inputError = '';
  TURN.note = '';
  TURN.pending = null;
  TURN.clarify = null;
  TURN.confirming = false;
  TURN.lastRequest = { kind: kind, text: text, action_id: actionId,
                       clarify_id: String((req && req.clarify_id) || ''),
                       answer: String((req && req.answer) || '') };
  TURN.busy = true;               // 提案 →（确认 / 澄清）→ 提交：整段都算在途（§2.0）
  closeActionSheet();
  renderComposer();

  var out = await actionPost('/api/action/propose', actionRequestBody(TURN.lastRequest));
  if (!out.ok) { failAction(out, text); return; }

  var d = out.data || {};
  if (d.type === 'proposal') { adoptProposal(d); return; }
  if (d.type === 'clarify') { openClarify(d.clarify, text); return; }
  if (d.type === 'offline') { offlineAction(text); return; }
  failAction({ message: '这一回合没能开始，请重试', retryable: true }, text);
}

/* 提案到手（§9.2）：记下原样带回的提案 + 并发基线，R0/R1 直通提交、
   R2+（`needs_confirm`）先弹 L2 确认——是否确认**以服务端为准**。 */
function adoptProposal(d) {
  var proposal = (d && d.proposal) || {};
  var intent = proposal.intent || {};
  TURN.pending = {
    proposal: proposal,
    base_seq: (d && d.base_seq),
    risk: String((d && d.risk) || ''),
    costs_preview: Array.isArray(d && d.costs_preview) ? d.costs_preview.slice() : [],
    needs_confirm: !!(d && d.needs_confirm),
    key: '',
    local: null,
    verb: String(intent.verb || ''),
    desired: String(intent.desired || '')
  };
  if (TURN.pending.needs_confirm) { openActionConfirm(); return; }
  finishCommit('');
}

/* 提交这一段（§9.3）：先把「我说的话」乐观地放进流（服务端同一回合会落进
   日志，收尾按 (kind, text) 认领），再发流式请求。 */
async function finishCommit(confirmToken) {
  var p = TURN.pending;
  if (!p) { endAction(); return; }
  closeActionSheet();
  if (!p.key) p.key = actionIdemKey();
  var act = actTextForVerb(p.verb);
  if (act) {
    p.local = addStreamEntry({ seq: null, kind: 'act', text: act, pending: true }, false);
  }
  renderStream();

  var out = await actionCommit(p, confirmToken);
  if (!out.ok) { failAction(out, String((TURN.lastRequest || {}).text || ''), p); return; }
  // 行动按钮入口不清输入框：这次提交用的不是输入框里的那句话（见 pickAction）。
  if (String((TURN.lastRequest || {}).kind || '') !== 'action') setComposerText('');
  if (!out.complete) {
    // §2.8：SSE 中途断流（没有 done）——已收到的部分保留，如实说一句。
    TURN.note = '这一回合没说完';
  }
  endAction();
  renderStream();
  await afterTurn();
}

/* 行动结束（成功 / 失败 / 取消）：关掉模态、放开在途、重画三块。 */
function endAction() {
  TURN.busy = false;
  TURN.pending = null;
  TURN.clarify = null;
  TURN.confirming = false;
  closeActionSheet();
  TURN.rendered = '';
  renderComposer();
  renderActions();
  renderStream();
}

/* 失败：撤回乐观条目、把话还回输入框；按处置分「提示条」与「就地错误行」。 */
function failAction(out, text, pending) {
  var p = pending || TURN.pending;
  if (p && p.local) dropStreamEntry(p.local);
  var message = String((out && out.message) || '这一回合没能开始，请重试');
  if (out && out.sessionGone) {
    SID = '';
    try { localStorage.removeItem(SID_KEY); } catch (e) { /* 隐私模式 */ }
    closeAllSheets();
    showView('v-lobby');
    toast(message);
  } else if (out && out.tone === 'toast') {
    toast(message);
    if (out.cooldown) startCooldown();
  } else {
    TURN.error = message;
    TURN.retryable = !!(out && out.retryable);
    if (text && !turnText()) setComposerText(text);   // 不丢玩家打的字
  }
  if (out && out.refresh) {
    // 服务端说这提案已经提交过：不重提，把权威条目补齐（结果已在世界里）。
    loadStreamLog();
    refreshTurnSession();
  }
  endAction();
}

/* 离线（§9.2 的 `type: "offline"`，200、不是错误）：这句话没变成行动。
   显式行动**不受影响**（§7.1）——提示里直接说清楚下一步做什么。 */
function offlineAction(text) {
  TURN.busy = false;
  TURN.pending = null;
  TURN.clarify = null;
  TURN.error = TURN_OFFLINE_NOTE;
  TURN.retryable = false;
  if (text && !turnText()) setComposerText(text);
  renderComposer();
  renderActions();
}

/* ── R2+ 的确认（§6.4）：L2 模态，点名这一步 + 风险级 + 时间成本 ── */
function openActionConfirm() {
  var p = TURN.pending;
  if (!p) return;
  var els = actionSheetEls();
  if (!els.dlg || !els.scrim || !els.body) {
    // 没有挂点就**不提交**（安全侧）：宁可让玩家重新点一次。
    failAction({ message: '这一回合没能开始，请重试', retryable: true }, '');
    return;
  }
  els.dlg.dataset.kind = 'confirm';
  els.dlg.dataset.risk = p.risk;
  turnEl('actionSheetTitle').textContent = '需要确认';
  els.body.innerHTML =
    '<p class="confirm-line">' + esc(p.desired || p.verb || '') + '</p>' +
    '<p class="confirm-sub">' + esc(confirmSubText(p)) + '</p>' +
    '<div class="confirm-actions">' +
      '<button type="button" class="btn" data-action-cancel="1">取消</button>' +
      '<button type="button" class="btn btn-primary" data-action-go="1">确认执行</button>' +
    '</div>';
  showActionSheet(els, '[data-action-go]');
  var cancel = els.body.querySelector('[data-action-cancel]');
  var go = els.body.querySelector('[data-action-go]');
  if (cancel) cancel.addEventListener('click', cancelAction);
  if (go) go.addEventListener('click', function () {
    if (TURN.confirming) return;
    TURN.confirming = true;
    go.disabled = true;              // 防连点：确认只发一次请求
    confirmPending();
  });
}

/* 确认框的副行：风险级中文短名 + 已知的时间成本（§6.4 的 `costs_preview`）。 */
function confirmSubText(p) {
  var parts = [];
  var risk = String((p && p.risk) || '');
  if (risk) {
    parts.push(TURN_RISK_ZH[risk] ? TURN_RISK_ZH[risk] + '（' + risk + '）' : risk);
  }
  ((p && p.costs_preview) || []).forEach(function (c) {
    if (!c || String(c.kind || '') !== 'time') return;
    var unit = TURN_TIME_UNIT_ZH[String(c.unit || '')] || String(c.unit || '');
    parts.push('耗时 ' + turnInt(c.value, 0) + (unit ? ' ' + unit : ''));
  });
  return parts.join(' · ');
}

/* 确认（§9.1 `POST /api/action/confirm`）：拿确认令牌，再带令牌提交。 */
async function confirmPending() {
  var p = TURN.pending;
  if (!p) { endAction(); return; }
  var out = await actionPost('/api/action/confirm',
                             { proposal_id: (p.proposal || {}).proposal_id,
                               decision: 'confirm' });
  if (!out.ok) { failAction(out, '', p); return; }
  var token = String(((out.data || {}).confirm_token) || '');
  if (!token) {
    failAction({ message: '这一回合没能开始，请重试', retryable: true }, '', p);
    return;
  }
  await finishCommit(token);
}

/* ── 一次性澄清（§6.3）：只问**一个**问题，选项由服务端给 ── */
function openClarify(clarify, text) {
  var c = (clarify && typeof clarify === 'object') ? clarify : {};
  var options = Array.isArray(c.options) ? c.options : [];
  var question = String(c.question || '');
  var els = actionSheetEls();
  if (!String(c.clarify_id || '') || !question || !options.length
      || !els.dlg || !els.scrim || !els.body) {
    failAction({ message: '这一回合没能开始，请重试', retryable: true }, text);
    return;
  }
  TURN.clarify = { clarify_id: String(c.clarify_id), question: question,
                   options: options, text: String(text || '') };
  els.dlg.dataset.kind = 'clarify';
  delete els.dlg.dataset.risk;
  turnEl('actionSheetTitle').textContent = '一个问题';
  els.body.innerHTML =
    '<p class="confirm-line">' + esc(question) + '</p>' +
    '<div class="clarify-options">' + options.map(function (o, i) {
      return '<button type="button" class="btn clarify-option" data-clarify-option="' + i + '">' +
        esc((o && (o.label || o.id)) || '') + '</button>';
    }).join('') + '</div>' +
    '<div class="confirm-actions">' +
      '<button type="button" class="btn" data-action-cancel="1">取消</button>' +
    '</div>';
  showActionSheet(els, '[data-clarify-option]');
  $$('#actionSheetBody [data-clarify-option]').forEach(function (b) {
    b.addEventListener('click', function () {
      var idx = turnInt(b.dataset.clarifyOption, -1);
      var opts = (TURN.clarify && TURN.clarify.options) || [];
      answerClarify(opts[idx]);
    });
  });
  var cancel = els.body.querySelector('[data-action-cancel]');
  if (cancel) cancel.addEventListener('click', cancelAction);
}

/* 回答澄清：带上 `clarify_id` **重新提案**（§6.3）——服务端把答案并入草案，
   仍定不下来会降级为拒绝，前端不再追问第二条。 */
function answerClarify(option) {
  var c = TURN.clarify;
  if (!c || !option) return;
  var value = String(option.id || option.label || '');
  if (!value) return;
  closeActionSheet();
  TURN.busy = false;          // 交回入口：下一次行动走正常闸门（runAction 会重新登记）
  runAction({ kind: 'nl', text: c.text, clarify_id: c.clarify_id, answer: value });
}

/* 取消 = **不提交**。R2+ 的确认令牌 300 秒自然过期（§6.4 的 `decision: "cancel"`
   只丢令牌、不改世界状态），所以这里不额外发一次写请求。 */
function cancelAction() {
  var text = String((TURN.lastRequest || {}).text || '');
  var wasClarify = !!TURN.clarify;
  closeActionSheet();
  TURN.busy = false;
  TURN.pending = null;
  TURN.clarify = null;
  TURN.confirming = false;
  // 澄清取消：把原话还回输入框（这一回合什么都没发生）。
  if (wasClarify && text && !turnText()) setComposerText(text);
  renderComposer();
  renderActions();
}

function actionSheetEls() {
  return { scrim: turnEl('actionScrim'), dlg: turnEl('actionSheet'),
           body: turnEl('actionSheetBody') };
}

function showActionSheet(els, focusSel) {
  els.scrim.hidden = false;
  els.dlg.hidden = false;
  var target = focusSel ? els.body.querySelector(focusSel) : null;
  if (target) { try { target.focus(); } catch (e) { /* 忽略 */ } }
}

function closeActionSheet() {
  var els = actionSheetEls();
  if (els.scrim) els.scrim.hidden = true;
  if (els.dlg) els.dlg.hidden = true;
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

  // 确认 / 澄清的 L2 模态是本片自成一体的浮层（同 U1 / U3 的确认框口径）：
  // core 的 Esc 只认它自己的浮层栈，不认识这个框，所以这里自己收。
  document.addEventListener('keydown', function (e) {
    var dlg = turnEl('actionSheet');
    if (e.key === 'Escape' && dlg && !dlg.hidden) {
      e.preventDefault();
      e.stopPropagation();
      cancelAction();
    }
  });
  var scrim = turnEl('actionScrim');
  if (scrim) scrim.addEventListener('click', cancelAction);

  // 视口变了（横竖屏、桌面窗口缩放、手机键盘弹起）要重量一次工具条高度。
  window.addEventListener('resize', syncPlayDock);

  renderComposer();
  renderSessionError();
  renderGuideStatus();
  loadGuideStatus();      // 能力探测：一次只读请求，拿不到就按「未配置」显示
  window.__TURN = turnSnapshot;
}

onInit(bindTurn);

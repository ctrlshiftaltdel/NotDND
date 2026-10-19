/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 W-WORLD：世界工坊（Issue #398，M12-UI）
   依据 WORLD-DESIGN.md §9.1（API 面）/ §10.3。四块内容画在既有
   #auxSheet 的 #auxBody 里，注册表扩展手法同片 U4（js/panels.js）：

     · 生成世界 —— 表单（世界类型 / 规模 / 引擎 / 描述 / overrides）
       → POST /api/world/generate → 预览卡（结构摘要 + overrides diff
       + issues；error 阻断就地展示）→ POST /api/world/commit 确认入库；
       入库后可导出（P6 v1 文件级），导入走文件选择 → /api/world/import。
     · 建造与委托 —— GET/POST /api/world/project：工程列表（状态全态、
       blocked_reason）+ 发起工程表单 + 按状态显隐的操作（排队 / 开工 /
       暂停 / 复工 / 撤销）；GET/POST /api/world/directive：指令列表 +
       创建（预算上限）+ 暂停 / 复工 / 撤销 + 六项可解释展示。
     · 账本 —— GET /api/world/ledger 只读：balances / prices / goods /
       最近 Entry / 工程状态（含「这项工程为什么停」的 blocked_reason）。

   边界：
     · 鉴权按后端既有语义透传：core 的 api() 统一带 X-Session；
       房主令牌属多人轨，本片不新增头（W3/W8 裁定，前端只透传）。
     · generate 的 error 阻断要展示 issues 列表，core 的 api() 只回
       error 文案——所以只有这一个请求走本片自己的薄 fetch（同样带
       X-Session）；其余一律走 core 的 api()（写动作受统一在途守卫）。
     · 不改 core.js 的加载顺序；只在加载期扩 AUX 注册表（panels 先例）。
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* ══════════════ 注册进 core 的辅助层（panels.js 同款）══════════════ */
var WORLD_TITLE = '世界工坊';

var WORLD_CORE_RENDER_AUX = renderAux;   // panels.js 包过一层后的渲染
renderAux = function () {
  if (AUX_KIND === 'world') { renderWorldPanel(); return; }
  WORLD_CORE_RENDER_AUX();
};
AUX_TITLE.world = WORLD_TITLE;
/* panels 的 tab 点击 handler 只认自己的 PANEL_TITLE：登记一个键让
   「工坊」这个 tab 能走进 openAux（渲染由本片的 wrapper 分发）。 */
PANEL_TITLE.world = WORLD_TITLE;

/* ── 本片状态 ──────────────────────────────────────────── */
/* form.* 是「用户填的值」的账本：面板每次全量重建前先把屏上的值收回
   状态，重建时再回填（Issue #404——生成失败 / 刷新列表 / 切分区都不丢
   输入）。成功后要清空的字段同时清 DOM 与状态，语义与从前一致。 */
var W = {
  view: 'gen',          // gen | build | dir | ledger
  gen: {
    busy: false,        // 生成 / 提交在途
    error: '',          // 就地错误（服务端固定短语）
    opError: '',        // 提交 / 导出 / 导入失败原文
    issues: null,       // error 阻断的校验问题列表（422）
    preview: null,      // 生成预览卡
    committed: null,    // commit / import 成功回执 {instance_id, world_name}
    form: {
      type: 'fantasy', scale: 'region', engine: 'tactics',
      seed: '', desc: '', ovr: ''
    }
  },
  build: {
    reqId: 0, loading: false,
    list: null,         // GET /api/world/project 的 projects 摘要
    error: '',          // 列表读取失败（404 no_world 等）
    detail: null,       // {project, readiness, entries}（ledger?project_id=）
    opError: '',        // 操作失败的原文
    form: {
      name: '', place: '', kind: '',
      coin: '0', labor: '0', wage: '0', goods: ''
    }
  },
  dir: {
    reqId: 0, loading: false,
    list: null, error: '',
    explain: null,      // 六项可解释（directive_explain）
    opError: '',
    form: {
      settle: '', text: '', metric: 'grain_days', op: 'gte',
      target: '90', good: '', cap: '', coin: ''
    }
  },
  ledger: {
    reqId: 0, loading: false,
    data: null, error: ''
  }
};

/* 只读快照：测试 / 调试用（同 __CAMPAIGN / __TURN 的用途）。 */
function worldSnapshot() {
  return {
    view: W.view,
    gen: { busy: W.gen.busy, error: W.gen.error,
           hasPreview: !!W.gen.preview, hasIssues: !!W.gen.issues,
           committed: !!W.gen.committed },
    build: { loading: W.build.loading, error: W.build.error,
             count: W.build.list ? W.build.list.length : -1 },
    dir: { loading: W.dir.loading, error: W.dir.error,
           count: W.dir.list ? W.dir.list.length : -1 },
    ledger: { loading: W.ledger.loading, error: W.ledger.error,
              loaded: !!W.ledger.data }
  };
}

/* ── 标签映射（展示名来自后端枚举的中文口径，不编造状态）── */
var W_PROJECT_STATUS = {
  draft: '草稿', queued: '排队中', running: '施工中', blocked: '受阻',
  paused: '已暂停', completed: '已完工', failed: '已失败', revoked: '已撤销'
};
var W_DIRECTIVE_STATUS = {
  active: '生效中', paused: '已暂停', blocked: '受阻', revoked: '已撤销'
};
var W_SCALE = { sketch: '速写', region: '区域', domain: '领地' };
var W_ENGINE = { tactics: '战术引擎', build: '构建引擎' };
var W_WORLD_TYPE = { fantasy: '奇幻', modern: '现代', future: '未来' };
var W_BLOCKED_REASON = {
  funds: '资金不足', supply: '材料断供', disruption: '扰动中断',
  hard_ask_cap_half: '触及预算上限一半，已停下回问'
};
var W_READINESS = {
  materials: '材料不足', funds: '资金不足', labor: '劳动力不足',
  place: '地点不是可建造设施', map: '所在地图未经校验'
};

function wStatusLabel(status, map) {
  var key = String(status == null ? '' : status);
  return map[key] || key;
}
/* 状态的语义色（全部落在令牌上：终态 / 受阻坏，运行 / 完工好，其余中性）。 */
function wStatusClass(status) {
  if (status === 'blocked' || status === 'failed' || status === 'revoked') return ' is-bad';
  if (status === 'running' || status === 'completed' || status === 'active') return ' is-good';
  if (status === 'paused') return ' is-warn';
  return '';
}
function wReasonText(reason) {
  var key = String(reason || '');
  return W_BLOCKED_REASON[key] || (key ? key : '');
}
function wInt(v) {
  var n = parseInt(v, 10);
  return isNaN(n) ? 0 : n;
}

/* ══════════════ 表单值的回收与回填（Issue #404）══════════════ */

/* 分区 → 状态键 → 控件选择器。选择器只认本片的 data-*，并且限定在
   该分区的表单卡里（`[data-wd-op]` 在指令列表的按钮上也出现，不能裸查）。 */
var W_FORM_FIELDS = {
  gen: { scope: '[data-wgen]', fields: {
         type: '[data-wgen-type]', scale: '[data-wgen-scale]',
         engine: '[data-wgen-engine]', seed: '[data-wgen-seed]',
         desc: '[data-wgen-desc]', ovr: '[data-wgen-ovr]' } },
  build: { scope: '[data-wbuild]', fields: {
           name: '[data-wp-name]', place: '[data-wp-place]',
           kind: '[data-wp-kind]', coin: '[data-wp-coin]',
           labor: '[data-wp-labor]', wage: '[data-wp-wage]',
           goods: '[data-wp-goods]' } },
  dir: { scope: '[data-wdir]', fields: {
         settle: '[data-wd-settle]', text: '[data-wd-text]',
         metric: '[data-wd-metric]', op: '[data-wd-op]',
         target: '[data-wd-target]', good: '[data-wd-good]',
         cap: '[data-wd-cap]', coin: '[data-wd-coin]' } }
};

/* 上一次真正画到 #auxBody 的分区：回收按它取，不受 W.view 先改影响。 */
var wMountedView = '';

/* 把屏上的表单值收回状态；控件不在屏上（换了分区）就保持原值。 */
function wCaptureForms(view) {
  var spec = W_FORM_FIELDS[view];
  var box = W[view];
  if (!spec || !box || !box.form) return;
  Object.keys(spec.fields).forEach(function (k) {
    var el = document.querySelector('#auxBody ' + spec.scope + ' ' +
                                   spec.fields[k]);
    if (el) box.form[k] = String(el.value == null ? '' : el.value);
  });
}

/* 回填出口：属性值 / textarea 内容都经 esc()，不引入裸 HTML。 */
function wVal(v) {
  return ' value="' + esc(v == null ? '' : v) + '"';
}
/* 成功后要清空的字段：状态与屏上一起清（重渲染后旧节点已脱离文档，
   只清状态会被下一次回收又捡回屏上的旧值）。 */
function wClearField(view, key) {
  var spec = W_FORM_FIELDS[view];
  if (!spec) return;
  W[view].form[key] = '';
  var el = document.querySelector('#auxBody ' + spec.scope + ' ' +
                                 spec.fields[key]);
  if (el) el.value = '';
}
function wOption(k, label, selected) {
  return '<option value="' + esc(k) + '"' +
    (String(k) === String(selected == null ? '' : selected) ? ' selected' : '') +
    '>' + label + '</option>';
}

/* ══════════════ 面板渲染入口 ══════════════ */

function renderWorldPanel() {
  var box = $('#auxBody');
  if (!box) return;
  wCaptureForms(wMountedView);   // 先收回屏上的输入，再全量重建（Issue #404）
  var title = $('#auxTitle');
  if (title) title.textContent = WORLD_TITLE;
  var html = '<div class="wband" data-wwork>' +
    '<b>世界工坊</b>' +
    '<small>生成世界 · 建造与委托 · 账本（只读）</small></div>' +
    '<div class="wseg" role="tablist" aria-label="工坊分区">' +
    [['gen', '生成'], ['build', '工程'], ['dir', '委托'], ['ledger', '账本']]
      .map(function (p) {
        return '<button type="button" class="' + (W.view === p[0] ? 'is-on' : '') +
          '" data-wview="' + p[0] + '" aria-selected="' +
          (W.view === p[0] ? 'true' : 'false') + '">' + p[1] + '</button>';
      }).join('') + '</div>';
  if (W.view === 'gen') html += wGenHtml();
  else if (W.view === 'build') html += wBuildHtml();
  else if (W.view === 'dir') html += wDirHtml();
  else html += wLedgerHtml();
  box.innerHTML = html;
  wMountedView = W.view;
}

/* ══════════════ 一、生成世界 ══════════════ */

function wGenHtml() {
  var g = W.gen;
  var f = g.form;
  var html = '<div class="wcard" data-wgen><div class="wcard-title">生成一个世界</div>' +
    '<div class="wcard-body">' +
    '<div class="wgrid">' +
    '<div class="wfield"><label for="wgenType">世界类型</label>' +
    '<select id="wgenType" data-wgen-type>' +
    ['fantasy', 'modern', 'future'].map(function (k) {
      return wOption(k, W_WORLD_TYPE[k], f.type);
    }).join('') + '</select></div>' +
    '<div class="wfield"><label for="wgenScale">规模档</label>' +
    '<select id="wgenScale" data-wgen-scale>' +
    ['sketch', 'region', 'domain'].map(function (k) {
      return wOption(k, W_SCALE[k], f.scale);
    }).join('') + '</select></div>' +
    '<div class="wfield"><label for="wgenEngine">剧本引擎</label>' +
    '<select id="wgenEngine" data-wgen-engine>' +
    ['tactics', 'build'].map(function (k) {
      return wOption(k, W_ENGINE[k], f.engine);
    }).join('') + '</select></div>' +
    '<div class="wfield"><label for="wgenSeed">种子提示（可空）</label>' +
    '<input type="text" id="wgenSeed" data-wgen-seed placeholder="留空则新掷"' +
    wVal(f.seed) + '></div>' +
    '</div>' +
    '<div class="wfield"><label for="wgenDesc">描述（一句话想要怎样的世界）</label>' +
    '<textarea id="wgenDesc" data-wgen-desc rows="2" ' +
    'placeholder="例：一座被灰烬覆盖的港口城，商人与打捞者共存">' +
    esc(f.desc) + '</textarea></div>' +
    '<div class="wfield"><label for="wgenOvr">overrides（JSON，可空）</label>' +
    '<textarea id="wgenOvr" data-wgen-ovr rows="2" ' +
    'placeholder=\'{"region_upserts":[{"name":"黑锚区","summary":"…"}]}\'>' +
    esc(f.ovr) + '</textarea></div>' +
    '<div class="wacts">' +
    '<button type="button" class="btn btn-primary" data-wgen-run' +
    (g.busy ? ' aria-busy="true" disabled' : '') + '>生成预览</button>' +
    // 导入（P6 v1 文件级）：label 触发隐藏的文件选择，change 委托在 #auxBody。
    '<label class="btn" data-wgen-import>导入 JSON' +
    '<input type="file" data-wgen-file accept="application/json,.json" hidden></label>' +
    '</div>' +
    (g.error ? '<p class="werror" role="alert">' + esc(g.error) + '</p>' : '') +
    (g.issues ? wIssuesHtml(g.issues, '生成物校验未通过（error 阻断）') : '') +
    '</div></div>';

  if (g.preview) html += wPreviewHtml(g.preview);
  html += wCommittedHtml();
  return html;
}

/* issues 列表（error 阻断与预览的 warn 共用形状）。 */
function wIssuesHtml(issues, heading) {
  var list = (issues || []).filter(Boolean);
  return '<div class="wcard"><div class="wcard-title">' + esc(heading || '校验问题') +
    '</div><div class="wcard-body">' +
    (list.length
      ? '<ul class="wissues">' + list.map(function (i) {
          return '<li' + (i && i.level === 'error' ? ' class="is-error"' : '') + '>' +
            '<span class="code">' + esc((i && i.code) || '?') + '</span>' +
            esc((i && i.hint) || '') + '</li>';
        }).join('') + '</ul>'
      : '<p class="wrow"><span class="wv">没有校验问题。</span></p>') +
    '</div></div>';
}

function wPreviewHtml(p) {
  var world = p.world || {};
  var est = p.estimate || {};
  var rows = [
    ['世界名', world.name || world.key || '—'],
    ['世界类型', W_WORLD_TYPE[world.world_type] || world.world_type || '—'],
    ['规模档', W_SCALE[p.scale] || p.scale || '—'],
    ['体量估算', [est.regions != null ? est.regions + ' 区域' : '',
                  est.key_places != null ? est.key_places + ' 地点' : '',
                  est.npcs != null ? est.npcs + ' 名 NPCs' : '',
                  est.factions != null ? est.factions + ' 势力' : '']
      .filter(Boolean).join(' · ') || '—'],
    ['经济运行时', est.economy_on ? '开' : '关'],
    ['生成会话', p.session_id || '—'],
    ['种子', String(p.seed == null ? '—' : p.seed)]
  ];
  if (p.degraded) rows.push(['降级', '部分文本走了兜底（模型不可用或超预算）']);
  var html = '<div class="wcard" data-wgen-preview><div class="wcard-title">预览卡</div>' +
    '<div class="wcard-body">' +
    rows.map(function (r) {
      return '<div class="wrow"><span class="wk">' + esc(r[0]) + '</span>' +
        '<span class="wv' + (r[0] === '生成会话' || r[0] === '种子' ? ' is-mono' : '') + '">' +
        esc(r[1]) + '</span></div>';
    }).join('');
  var regions = p.regions || [];
  html += '<div class="wrow"><span class="wk">区域</span><span class="wv">' +
    (regions.length
      ? regions.map(function (r) { return esc(r.name || r.id || '?'); }).join('、')
      : '—') + '</span></div>';
  var factions = p.factions || [];
  if (factions.length) {
    html += '<div class="wrow"><span class="wk">势力</span><span class="wv">' +
      factions.map(function (f) { return esc(f.name || f.id || '?'); }).join('、') +
      '</span></div>';
  }
  var laws = p.laws || [];
  if (laws.length) {
    html += '<div class="wrow"><span class="wk">法律</span><span class="wv">' +
      laws.map(function (l) { return esc(l.name || l.id || '?'); }).join('、') +
      '</span></div>';
  }
  if (p.goods_count != null) {
    html += '<div class="wrow"><span class="wk">货物</span><span class="wv">' +
      esc(String(p.goods_count) + ' 种') + '</span></div>';
  }
  (p.spec_notes || []).forEach(function (n) {
    html += '<p class="wrow"><span class="wk">口径</span><span class="wv">' +
      esc(n) + '</span></p>';
  });
  var diff = (p.overrides || {}).diff || [];
  if (diff.length) {
    html += '<div class="wdiff">' + diff.map(function (d) {
      return '<div class="wdiff-line" data-op="' + esc(d.op || '') + '">' +
        '<span class="op">' + esc(d.op === 'add' ? '+ 新增'
          : d.op === 'remove' ? '− 移除' : '~ 修改') + '</span>' +
        esc(d.path || '') +
        (d.after !== undefined ? ' → ' + esc(JSON.stringify(d.after)) : '') +
        '</div>';
    }).join('') + '</div>';
  }
  html += '<div class="wacts">' +
    '<button type="button" class="btn btn-primary" data-wgen-commit' +
    (W.gen.busy ? ' aria-busy="true" disabled' : '') + '>确认入库</button>' +
    '</div>' +
    (W.gen.opError ? '<p class="werror" role="alert">' + esc(W.gen.opError) + '</p>' : '') +
    '</div></div>';
  if (p.issues && p.issues.length) html += wIssuesHtml(p.issues, '非阻断问题（warn）');
  return html;
}

/* commit / import 的回执：实例号 + 导出 / 再导入口。 */
function wCommittedHtml() {
  var c = W.gen.committed;
  if (!c) return '';
  return '<div class="wcard" data-wgen-committed><div class="wcard-title">已入库</div>' +
    '<div class="wcard-body">' +
    '<div class="wrow"><span class="wk">世界</span><span class="wv">' +
    esc(c.world_name || '—') + '</span></div>' +
    '<div class="wrow"><span class="wk">实例号</span><span class="wv is-mono">' +
    esc(c.instance_id || '—') + '</span></div>' +
    '<div class="wacts">' +
    '<button type="button" class="btn" data-wgen-export' +
    (W.gen.busy || !c.instance_id ? ' disabled' : '') + '>导出 JSON</button>' +
    '</div>' +
    (W.gen.opError ? '<p class="werror" role="alert">' + esc(W.gen.opError) + '</p>' : '') +
    '</div></div>';
}

/* 生成预览：唯一一个走本片薄 fetch 的请求（要拿到 422 的 issues）。 */
async function wGenRun() {
  if (W.gen.busy) return;
  var descEl = document.querySelector('[data-wgen-desc]');
  var typeEl = document.querySelector('[data-wgen-type]');
  var scaleEl = document.querySelector('[data-wgen-scale]');
  var engineEl = document.querySelector('[data-wgen-engine]');
  var seedEl = document.querySelector('[data-wgen-seed]');
  var ovrEl = document.querySelector('[data-wgen-ovr]');
  var body = {
    world_type: typeEl ? typeEl.value : 'modern',
    scale: scaleEl ? scaleEl.value : 'region',
    engine: engineEl ? engineEl.value : 'tactics',
    description: descEl ? String(descEl.value || '').trim() : ''
  };
  if (seedEl && String(seedEl.value || '').trim()) {
    body.seed_hint = String(seedEl.value).trim();
  }
  if (ovrEl && String(ovrEl.value || '').trim()) {
    var ovr;
    try {
      ovr = JSON.parse(String(ovrEl.value));
    } catch (e) {
      W.gen.error = 'overrides 不是合法 JSON：' + String((e && e.message) || e);
      W.gen.issues = null;
      renderWorldPanel();
      return;
    }
    if (!ovr || typeof ovr !== 'object' || Array.isArray(ovr)) {
      W.gen.error = 'overrides 必须是一个 JSON 对象';
      W.gen.issues = null;
      renderWorldPanel();
      return;
    }
    body.overrides = ovr;
  }
  W.gen.busy = true;
  W.gen.error = '';
  W.gen.issues = null;
  W.gen.preview = null;
  W.gen.committed = null;
  renderWorldPanel();
  var headers = { 'Content-Type': 'application/json' };
  if (SID) headers['X-Session'] = SID;
  var res;
  try {
    res = await fetch('/api/world/generate', {
      method: 'POST', headers: headers, body: JSON.stringify(body)
    });
  } catch (e) {
    W.gen.busy = false;
    W.gen.error = '无法连接服务，请确认后端仍在运行';
    renderWorldPanel();
    return;
  }
  var data = null;
  try { data = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
  W.gen.busy = false;
  if (!res.ok) {
    if (data && Array.isArray(data.issues)) {
      W.gen.issues = data.issues;                 // error 阻断：就地展示
      W.gen.error = String(data.error || ('生成失败（' + res.status + '）'));
    } else {
      W.gen.error = (data && data.error) || ('生成失败（' + res.status + '）');
    }
    renderWorldPanel();
    return;
  }
  W.gen.preview = data;
  renderWorldPanel();
}

/* 确认入库：一次性会话；成功后给实例号 + 导出入口。 */
async function wGenCommit() {
  var p = W.gen.preview;
  if (!p || !p.session_id || W.gen.busy) return;
  W.gen.busy = true;
  W.gen.opError = '';
  renderWorldPanel();
  try {
    var d = await api('/api/world/commit', {
      method: 'POST', body: { session_id: p.session_id }
    });
    W.gen.committed = d;
    W.gen.preview = null;      // 会话一次性：预览已消费
    buzz(12);
    toast('已入库：' + (d.world_name || d.instance_id || ''));
  } catch (e) {
    W.gen.opError = String((e && e.message) || e);
  }
  W.gen.busy = false;
  renderWorldPanel();
}

/* 导出（P6 v1 文件级）：响应体就是实例 JSON，就地转成下载。 */
async function wGenExport() {
  var c = W.gen.committed;
  if (!c || !c.instance_id || W.gen.busy) return;
  W.gen.busy = true;
  W.gen.opError = '';
  renderWorldPanel();
  try {
    var inst = await api('/api/world/export', {
      method: 'POST', body: { instance_id: c.instance_id }
    });
    var blob = new Blob([JSON.stringify(inst, null, 1)],
                        { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'world-' + c.instance_id + '.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
    toast('已导出 world-' + c.instance_id + '.json');
  } catch (e) {
    W.gen.opError = String((e && e.message) || e);
  }
  W.gen.busy = false;
  renderWorldPanel();
}

/* 导入（P6 v1）：文件 → JSON → POST /api/world/import。 */
async function wGenImport(file) {
  if (!file || W.gen.busy) return;
  W.gen.busy = true;
  W.gen.error = '';
  W.gen.issues = null;
  W.gen.opError = '';
  renderWorldPanel();
  var text = '';
  try {
    text = await file.text();
  } catch (e) {
    W.gen.busy = false;
    W.gen.error = '读文件失败：' + String((e && e.message) || e);
    renderWorldPanel();
    return;
  }
  var inst;
  try {
    inst = JSON.parse(text);
  } catch (e) {
    W.gen.busy = false;
    W.gen.error = '不是合法的 JSON 文件';
    renderWorldPanel();
    return;
  }
  try {
    var d = await api('/api/world/import', { method: 'POST', body: { instance: inst } });
    W.gen.committed = d;
    buzz(12);
    toast('已导入：' + (d.world_name || d.instance_id || ''));
  } catch (e) {
    W.gen.opError = String((e && e.message) || e);
  }
  W.gen.busy = false;
  renderWorldPanel();
}

/* ══════════════ 二、建造与委托（工程）══════════════ */

function wBuildHtml() {
  var b = W.build;
  var f = b.form;
  var html = '<div class="wcard" data-wbuild><div class="wcard-title">发起工程</div>' +
    '<div class="wcard-body">' +
    '<div class="wgrid">' +
    '<div class="wfield"><label for="wpName">工程名</label>' +
    '<input type="text" id="wpName" data-wp-name placeholder="例：东墙粮仓"' +
    wVal(f.name) + '></div>' +
    '<div class="wfield"><label for="wpPlace">地点（place_id）</label>' +
    '<input type="text" id="wpPlace" data-wp-place placeholder="例：p1"' +
    wVal(f.place) + '></div>' +
    '<div class="wfield"><label for="wpKind">设施类型（blueprint.kind）</label>' +
    '<input type="text" id="wpKind" data-wp-kind placeholder="例：granary"' +
    wVal(f.kind) + '></div>' +
    '<div class="wfield"><label for="wpCoin">资金需求（coin）</label>' +
    '<input type="number" id="wpCoin" data-wp-coin min="0" step="1"' +
    wVal(f.coin) + '></div>' +
    '<div class="wfield"><label for="wpLabor">工期（labor_days，人日）</label>' +
    '<input type="number" id="wpLabor" data-wp-labor min="0" step="1"' +
    wVal(f.labor) + '></div>' +
    '<div class="wfield"><label for="wpWage">日工资（wage_per_day，可空）</label>' +
    '<input type="number" id="wpWage" data-wp-wage min="0" step="1"' +
    wVal(f.wage) + '></div>' +
    '</div>' +
    '<div class="wfield"><label for="wpGoods">材料需求（JSON：{"good_id": 数量}，可空）</label>' +
    '<input type="text" id="wpGoods" data-wp-goods placeholder=\'{"grain": 10}\'' +
    wVal(f.goods) + '></div>' +
    '<div class="wacts">' +
    '<button type="button" class="btn btn-primary" data-wp-create>创建工程</button>' +
    '<button type="button" class="btn" data-wp-reload>刷新列表</button>' +
    '</div>' +
    (b.opError ? '<p class="werror" role="alert">' + esc(b.opError) + '</p>' : '') +
    '</div></div>';

  html += '<div class="wcard"><div class="wcard-title">工程列表</div><div class="wcard-body">';
  if (b.loading && !b.list) {
    html += '<div class="stream-empty">正在读取工程…</div>';
  } else if (b.error) {
    html += '<div class="alert"><p>' + esc(b.error) + '</p></div>';
  } else if (!b.list || !b.list.length) {
    html += '<div class="stream-empty">还没有工程。发起一个，或等委托指令派生。</div>';
  } else {
    html += b.list.map(wProjectCard).join('');
  }
  html += '</div></div>';
  if (b.detail) html += wProjectDetailHtml(b.detail);
  return html;
}

function wProjectCard(p) {
  var pid = String(p.project_id || '');
  var status = String(p.status || '');
  var acts = '';
  var op = function (name, label, primary) {
    return '<button type="button" class="btn' + (primary ? ' btn-primary' : '') +
      '" data-wp-op="' + name + '" data-wpid="' + esc(pid) + '">' + label + '</button>';
  };
  if (status === 'draft') acts = op('queue', '排队', true);
  else if (status === 'queued') acts = op('start', '开工', true) + op('pause', '暂停') + op('revoke', '撤销');
  else if (status === 'running') acts = op('pause', '暂停', true) + op('revoke', '撤销');
  else if (status === 'paused') acts = op('resume', '复工', true) + op('revoke', '撤销');
  else if (status === 'blocked') acts = op('revoke', '撤销');
  /* completed / failed / revoked 是终态：无操作（按钮按状态显隐）。 */
  var reason = wReasonText(p.blocked_reason);
  return '<div class="wproj" data-wproject="' + esc(pid) + '">' +
    '<div class="wproj-head"><b>' + esc(p.name || pid) + '</b>' +
    '<span class="wstatus' + wStatusClass(status) + '">' +
    esc(wStatusLabel(status, W_PROJECT_STATUS)) + '</span></div>' +
    '<div class="wproj-meta">' +
    esc([pid, p.place_id ? '地点 ' + p.place_id : '',
         p.source ? '来源 ' + p.source : ''].filter(Boolean).join(' · ')) +
    '</div>' +
    (reason ? '<div class="wblocked">为什么停：' + esc(reason) + '</div>' : '') +
    (acts ? '<div class="wacts">' + acts + '</div>' : '') +
    '<div class="wacts"><button type="button" class="btn" data-wp-detail="' +
    esc(pid) + '">为什么停 / 进度</button></div>' +
    '</div>';
}

/* 「这项工程为什么停」：ledger?project_id= 的完整状态 + 缺项 + 近账。 */
function wProjectDetailHtml(d) {
  var p = d.project || {};
  var ready = d.readiness || [];
  var html = '<div class="wcard" data-wp-detail-card><div class="wcard-title">工程详情 · ' +
    esc(p.name || p.project_id || '') + '</div><div class="wcard-body">' +
    '<div class="wrow"><span class="wk">状态</span><span class="wv">' +
    esc(wStatusLabel(p.status, W_PROJECT_STATUS)) +
    (p.blocked_reason ? '（' + esc(wReasonText(p.blocked_reason)) + '）' : '') +
    '</span></div>';
  var req = p.requirements || {};
  html += '<div class="wrow"><span class="wk">需求</span><span class="wv is-mono">' +
    esc(JSON.stringify({ goods: req.goods || {}, coin: req.coin || 0,
                         labor_days: req.labor_days || 0 })) + '</span></div>';
  var prog = p.progress || {};
  html += '<div class="wrow"><span class="wk">进度</span><span class="wv is-mono">' +
    esc(JSON.stringify({ materials_in: prog.materials_in || {},
                         labor_done_days: prog.labor_done_days || 0,
                         spend_coin: prog.spend_coin || 0 })) + '</span></div>';
  html += '<div class="wrow"><span class="wk">开工缺项</span><span class="wv">' +
    (ready.length
      ? ready.map(function (r) { return esc(W_READINESS[r] || r); }).join('、')
      : '五条件齐（可开工）') + '</span></div>';
  var entries = d.entries || [];
  html += '<table class="wtable"><thead><tr><th>近账</th><th>账户</th>' +
    '<th class="num">变动</th></tr></thead><tbody>' +
    (entries.length ? entries.slice().reverse().map(function (e) {
      var delta = wInt(e.delta);
      return '<tr><td>' + esc(e.kind || '') + '</td><td>' + esc(e.account || '') +
        '</td><td class="num' + (delta >= 0 ? ' is-plus' : ' is-minus') + '">' +
        esc((delta >= 0 ? '+' : '') + delta) + '</td></tr>';
    }).join('') : '<tr><td colspan="3">没有名下账目。</td></tr>') +
    '</tbody></table>' +
    '<div class="wacts"><button type="button" class="btn" data-wp-detail-close>收起</button></div>' +
    '</div></div>';
  return html;
}

function wLoadProjects() {
  if (W.build.loading) return;
  var my = ++W.build.reqId;
  W.build.loading = true;
  W.build.error = '';
  renderWorldPanel();
  api('/api/world/project').then(function (d) {
    if (my !== W.build.reqId) return null;
    var list = Array.isArray(d.projects) ? d.projects : [];
    // 摘要端点不带 blocked_reason；「这项工程为什么停」从账本观察面并回来
    // （账本读不到不影响列表本身——同一门槛下两者同生共死，这里只兜底）。
    return api('/api/world/ledger').then(function (led) {
      var byid = {};
      (led && led.projects || []).forEach(function (p) {
        byid[p.project_id] = p;
      });
      list.forEach(function (p) {
        if (byid[p.project_id]) {
          p.blocked_reason = byid[p.project_id].blocked_reason || '';
        }
      });
      return list;
    }, function () { return list; });
  }).then(function (list) {
    if (my !== W.build.reqId || list === null) return;
    W.build.list = list || [];
  }).catch(function (e) {
    if (my !== W.build.reqId) return;
    W.build.list = [];
    W.build.error = String((e && e.message) || e);
  }).then(function () {
    if (my !== W.build.reqId) return;
    W.build.loading = false;
    renderWorldPanel();
  });
}

async function wProjectCreate() {
  var b = W.build;
  if (b.loading || writeInFlight()) return;
  var nameEl = document.querySelector('[data-wp-name]');
  var placeEl = document.querySelector('[data-wp-place]');
  var kindEl = document.querySelector('[data-wp-kind]');
  var coinEl = document.querySelector('[data-wp-coin]');
  var laborEl = document.querySelector('[data-wp-labor]');
  var wageEl = document.querySelector('[data-wp-wage]');
  var goodsEl = document.querySelector('[data-wp-goods]');
  var name = nameEl ? String(nameEl.value || '').trim() : '';
  var place = placeEl ? String(placeEl.value || '').trim() : '';
  var kind = kindEl ? String(kindEl.value || '').trim() : '';
  if (!name || !place || !kind) {
    b.opError = '工程名 / 地点 / 设施类型都要填';
    renderWorldPanel();
    return;
  }
  var goods = {};
  if (goodsEl && String(goodsEl.value || '').trim()) {
    try {
      goods = JSON.parse(String(goodsEl.value));
    } catch (e) {
      b.opError = '材料需求不是合法 JSON';
      renderWorldPanel();
      return;
    }
    if (!goods || typeof goods !== 'object' || Array.isArray(goods)) {
      b.opError = '材料需求必须是 {"good_id": 数量}';
      renderWorldPanel();
      return;
    }
  }
  var body = {
    op: 'create',
    project_id: 'p-ui-' + Date.now().toString(36),
    name: name, place_id: place,
    blueprint: { kind: kind },
    requirements: { goods: goods,
                    coin: wInt(coinEl && coinEl.value),
                    labor_days: wInt(laborEl && laborEl.value) },
    wage_per_day: wInt(wageEl && wageEl.value)
  };
  b.loading = true;
  b.opError = '';
  renderWorldPanel();
  try {
    var d = await api('/api/world/project', { method: 'POST', body: body });
    toast('已创建工程：' + (d.project && d.project.name ? d.project.name : d.project_id));
    wClearField('build', 'name');   // 成功后清空工程名（其余字段保留，语义不变）
  } catch (e) {
    b.opError = String((e && e.message) || e);
  }
  b.loading = false;
  wLoadProjects();
}

async function wProjectOp(op, pid) {
  var b = W.build;
  if (b.loading || writeInFlight()) return;
  b.loading = true;
  b.opError = '';
  renderWorldPanel();
  try {
    await api('/api/world/project', { method: 'POST',
                                      body: { op: op, project_id: pid } });
    buzz(12);
    toast('已' + ({ queue: '排队', start: '开工', pause: '暂停',
                    resume: '复工', revoke: '撤销' }[op] || op));
  } catch (e) {
    b.opError = String((e && e.message) || e);
  }
  b.loading = false;
  wLoadProjects();
}

async function wProjectDetail(pid) {
  var b = W.build;
  if (b.loading) return;
  b.loading = true;
  b.opError = '';
  renderWorldPanel();
  try {
    b.detail = await api('/api/world/ledger?project_id=' +
                         encodeURIComponent(pid));
  } catch (e) {
    b.opError = String((e && e.message) || e);
  }
  b.loading = false;
  renderWorldPanel();
}

/* ══════════════ 三、委托指令 ══════════════ */

function wDirHtml() {
  var d = W.dir;
  var f = d.form;
  var html = '<div class="wcard" data-wdir><div class="wcard-title">下达委托指令</div>' +
    '<div class="wcard-body">' +
    '<div class="wgrid">' +
    '<div class="wfield"><label for="wdSettle">据点（settlement_id）</label>' +
    '<input type="text" id="wdSettle" data-wd-settle placeholder="例：s1"' +
    wVal(f.settle) + '></div>' +
    '<div class="wfield"><label for="wdText">指令内容（一句话）</label>' +
    '<input type="text" id="wdText" data-wd-text placeholder="例：把粮仓存粮维持到 90 天"' +
    wVal(f.text) + '></div>' +
    '<div class="wfield"><label for="wdMetric">目标指标</label>' +
    '<select id="wdMetric" data-wd-metric>' +
    wOption('grain_days', '存粮天数（grain_days）', f.metric) +
    wOption('good_stock', '货物库存（good_stock）', f.metric) +
    '</select></div>' +
    '<div class="wfield"><label for="wdOp">目标条件</label>' +
    '<select id="wdOp" data-wd-op>' +
    wOption('gte', '不低于（≥）', f.op) +
    wOption('lte', '不超过（≤）', f.op) +
    wOption('eq', '恰好等于（=）', f.op) +
    '</select></div>' +
    '<div class="wfield"><label for="wdTarget">目标值</label>' +
    '<input type="number" id="wdTarget" data-wd-target step="1"' +
    wVal(f.target) + '></div>' +
    '<div class="wfield"><label for="wdGood">货物（good_stock 指标必填）</label>' +
    '<input type="text" id="wdGood" data-wd-good placeholder="例：grain"' +
    wVal(f.good) + '></div>' +
    '<div class="wfield"><label for="wdCap">预算硬上界（cap_total，可空）</label>' +
    '<input type="number" id="wdCap" data-wd-cap min="0" step="1" ' +
    'placeholder="自动花费不越此线"' + wVal(f.cap) + '></div>' +
    '<div class="wfield"><label for="wdCoin">每日coin预算（coin_per_day，可空）</label>' +
    '<input type="number" id="wdCoin" data-wd-coin min="0" step="1"' +
    wVal(f.coin) + '></div>' +
    '</div>' +
    '<div class="wacts">' +
    '<button type="button" class="btn btn-primary" data-wd-create>创建指令</button>' +
    '<button type="button" class="btn" data-wd-reload>刷新列表</button>' +
    '</div>' +
    (d.opError ? '<p class="werror" role="alert">' + esc(d.opError) + '</p>' : '') +
    '</div></div>';

  if (d.explain) {
    html += wDirExplainHtml(d.explain);
  }
  html += '<div class="wcard"><div class="wcard-title">指令列表</div><div class="wcard-body">';
  if (d.loading && !d.list) {
    html += '<div class="stream-empty">正在读取指令…</div>';
  } else if (d.error) {
    html += '<div class="alert"><p>' + esc(d.error) + '</p></div>';
  } else if (!d.list || !d.list.length) {
    html += '<div class="stream-empty">还没有指令。</div>';
  } else {
    html += d.list.map(wDirCard).join('');
  }
  html += '</div></div>';
  return html;
}

function wDirCard(x) {
  var sid = String(x.settlement_id || '');
  var did = String(x.directive_id || '');
  var status = String(x.status || '');
  var acts = '';
  var op = function (name, label, primary) {
    return '<button type="button" class="btn' + (primary ? ' btn-primary' : '') +
      '" data-wd-op="' + name + '" data-wdid="' + esc(did) +
      '" data-wsid="' + esc(sid) + '">' + label + '</button>';
  };
  if (status === 'active') acts = op('pause', '暂停', true) + op('revoke', '撤销');
  else if (status === 'paused' || status === 'blocked') {
    acts = op('resume', '复工', true) + op('revoke', '撤销');
  }
  /* revoked 是终态：无操作。 */
  return '<div class="wproj" data-wdirective="' + esc(did) + '">' +
    '<div class="wproj-head"><b>' + esc(did) + '</b>' +
    '<span class="wstatus' + wStatusClass(status) + '">' +
    esc(wStatusLabel(status, W_DIRECTIVE_STATUS)) + '</span></div>' +
    '<div class="wproj-meta">' + esc('据点 ' + (sid || '—')) + '</div>' +
    (x.blocked_reason ? '<div class="wblocked">为什么停：' +
      esc(wReasonText(x.blocked_reason)) + '</div>' : '') +
    (acts ? '<div class="wacts">' + acts + '</div>' : '') +
    '<div class="wacts"><button type="button" class="btn" data-wd-explain="' +
    esc(did) + '" data-wsid="' + esc(sid) + '">六项说明</button></div>' +
    '</div>';
}

/* §6.5 可解释六项：目标 / 预算 / 在建 / 阻塞原因 / 近 3 笔 / 预期结果。 */
function wDirExplainHtml(x) {
  var goal = x.goal || {};
  var budget = x.budget || {};
  var expected = x.expected || {};
  var goalText = (goal.metric === 'good_stock'
    ? (goal.good || '?') + ' 库存'
    : '存粮天数') + ' ' + (goal.op === 'gte' ? '≥'
    : goal.op === 'lte' ? '≤' : '=') + ' ' + goal.target;
  var html = '<div class="wcard" data-wdir-explain><div class="wcard-title">指令说明 · ' +
    esc(x.directive_id || '') + '</div><div class="wcard-body">' +
    '<div class="wrow"><span class="wk">目标</span><span class="wv">' +
    esc(goalText) + '</span></div>' +
    '<div class="wrow"><span class="wk">预算</span><span class="wv">' +
    esc('已花 ' + wInt(budget.spent_total) +
        (budget.cap_total != null ? ' / 上界 ' + wInt(budget.cap_total) : ' / 无上界') +
        (budget.remaining != null ? '（余 ' + wInt(budget.remaining) + '）' : '')) +
    '</span></div>' +
    '<div class="wrow"><span class="wk">在建</span><span class="wv">' +
    ((x.projects || []).length ? x.projects.map(esc).join('、') : '没有关联的在建工程') +
    '</span></div>' +
    '<div class="wrow"><span class="wk">阻塞原因</span><span class="wv">' +
    esc(x.blocked_reason ? wReasonText(x.blocked_reason) : '无') + '</span></div>' +
    '<div class="wrow"><span class="wk">最近决策</span><span class="wv">' +
    ((x.recent_decisions || []).length
      ? x.recent_decisions.map(function (e) {
          return esc(e.kind + ' ' + e.account + ' ' + (wInt(e.delta) >= 0 ? '+' : '') + wInt(e.delta));
        }).join('；')
      : '近窗没有名下账目') + '</span></div>' +
    '<div class="wrow"><span class="wk">预期结果</span><span class="wv">' +
    esc('当前 ' + (expected.current == null ? '—' : expected.current) +
        '，目标 ' + expected.target + '（' +
        (expected.met ? '已达成' : '未达成') + '）') + '</span></div>' +
    '<div class="wacts"><button type="button" class="btn" data-wd-explain-close>收起</button></div>' +
    '</div></div>';
  return html;
}

function wLoadDirectives() {
  if (W.dir.loading) return;
  var my = ++W.dir.reqId;
  W.dir.loading = true;
  W.dir.error = '';
  renderWorldPanel();
  api('/api/world/directive').then(function (d) {
    if (my !== W.dir.reqId) return;
    W.dir.list = Array.isArray(d.directives) ? d.directives : [];
  }).catch(function (e) {
    if (my !== W.dir.reqId) return;
    W.dir.list = [];
    W.dir.error = String((e && e.message) || e);
  }).then(function () {
    if (my !== W.dir.reqId) return;
    W.dir.loading = false;
    renderWorldPanel();
  });
}

async function wDirCreate() {
  var d = W.dir;
  if (d.loading || writeInFlight()) return;
  var settleEl = document.querySelector('[data-wd-settle]');
  var textEl = document.querySelector('[data-wd-text]');
  var metricEl = document.querySelector('[data-wd-metric]');
  var opEl = document.querySelector('[data-wd-op]');
  var targetEl = document.querySelector('[data-wd-target]');
  var goodEl = document.querySelector('[data-wd-good]');
  var capEl = document.querySelector('[data-wd-cap]');
  var coinEl = document.querySelector('[data-wd-coin]');
  var settle = settleEl ? String(settleEl.value || '').trim() : '';
  var text = textEl ? String(textEl.value || '').trim() : '';
  if (!settle || !text) {
    d.opError = '据点与指令内容都要填';
    renderWorldPanel();
    return;
  }
  var goal = { metric: metricEl ? metricEl.value : 'grain_days',
               op: opEl ? opEl.value : 'gte',
               target: wInt(targetEl && targetEl.value) };
  if (goal.metric === 'good_stock') {
    var good = goodEl ? String(goodEl.value || '').trim() : '';
    if (!good) {
      d.opError = 'good_stock 指标要填货物（good_id）';
      renderWorldPanel();
      return;
    }
    goal.good = good;
  }
  var budget = {};
  if (capEl && String(capEl.value || '') !== '') budget.cap_total = wInt(capEl.value);
  if (coinEl && String(coinEl.value || '') !== '') budget.coin_per_day = wInt(coinEl.value);
  d.loading = true;
  d.opError = '';
  renderWorldPanel();
  try {
    var r = await api('/api/world/directive', {
      method: 'POST',
      body: { op: 'create', settlement_id: settle,
              directive_id: 'd-ui-' + Date.now().toString(36),
              text: text, goal: goal, budget: budget,
              authorized_scope: ['procure'] }
    });
    toast('已下达指令：' + r.directive_id);
    wClearField('dir', 'text');     // 成功后清空指令内容（其余字段保留，语义不变）
  } catch (e) {
    d.opError = String((e && e.message) || e);
  }
  d.loading = false;
  wLoadDirectives();
}

async function wDirOp(op, sid, did) {
  var d = W.dir;
  if (d.loading || writeInFlight()) return;
  d.loading = true;
  d.opError = '';
  renderWorldPanel();
  try {
    await api('/api/world/directive', { method: 'POST',
      body: { op: op, settlement_id: sid, directive_id: did } });
    buzz(12);
    toast('已' + ({ pause: '暂停', resume: '复工', revoke: '撤销' }[op] || op));
    if (d.explain && d.explain.directive_id === did) d.explain = null;
  } catch (e) {
    d.opError = String((e && e.message) || e);
  }
  d.loading = false;
  wLoadDirectives();
}

async function wDirExplain(sid, did) {
  var d = W.dir;
  if (d.loading) return;
  d.loading = true;
  d.opError = '';
  renderWorldPanel();
  try {
    var r = await api('/api/world/directive?settlement_id=' +
                      encodeURIComponent(sid) + '&directive_id=' +
                      encodeURIComponent(did));
    d.explain = r.explain;
  } catch (e) {
    d.opError = String((e && e.message) || e);
  }
  d.loading = false;
  renderWorldPanel();
}

/* ══════════════ 四、账本（只读）══════════════ */

function wLedgerHtml() {
  var l = W.ledger;
  var html = '<div data-wledger>';
  if (l.loading && !l.data) {
    html += '<div class="wcard"><div class="wcard-body">' +
      '<div class="stream-empty">正在读取账本…</div></div></div>';
    return html + '</div>';
  }
  if (l.error) {
    html += '<div class="alert"><p>' + esc(l.error) + '</p></div></div>';
    return html;
  }
  var d = l.data;
  if (!d) {
    html += '<div class="wcard"><div class="wcard-body">' +
      '<div class="stream-empty">账本还没打开。切走再切回来即可读取。</div></div></div>';
    return html + '</div>';
  }
  var eco = d.economy || {};
  html += '<div class="wcard"><div class="wcard-title">这个月的粮账（第 ' +
    esc(eco.day == null ? '—' : eco.day) + ' 日）</div><div class="wcard-body">';
  var balances = eco.balances || {};
  html += '<table class="wtable"><thead><tr><th>账户</th><th class="num">余额</th></tr></thead><tbody>' +
    (Object.keys(balances).length
      ? Object.keys(balances).sort().map(function (k) {
          return '<tr><td>' + esc(k) + '</td><td class="num">' +
            esc(String(balances[k])) + '</td></tr>';
        }).join('')
      : '<tr><td colspan="2">没有账户。</td></tr>') + '</tbody></table>';
  var goods = eco.goods || {};
  if (Object.keys(goods).length) {
    html += '<table class="wtable"><thead><tr><th>货物</th><th class="num">库存</th>' +
      '<th class="num">市价</th></tr></thead><tbody>' +
      Object.keys(goods).sort().map(function (k) {
        var g = goods[k] || {};
        var price = (eco.prices || {})[k] || {};
        return '<tr><td>' + esc(g.name || k) + '</td><td class="num">' +
          esc(String(g.stock == null ? '—' : g.stock)) + '</td><td class="num">' +
          esc(price.price == null ? '—' : String(price.price)) + '</td></tr>';
      }).join('') + '</tbody></table>';
  }
  html += '</div></div>';

  var projects = d.projects || [];
  html += '<div class="wcard"><div class="wcard-title">工程状态</div><div class="wcard-body">' +
    (projects.length
      ? projects.map(function (p) {
          var reason = wReasonText(p.blocked_reason);
          return '<div class="wrow"><span class="wk">' + esc(p.name || p.project_id || '') +
            '</span><span class="wv">' +
            '<span class="wstatus' + wStatusClass(p.status) + '">' +
            esc(wStatusLabel(p.status, W_PROJECT_STATUS)) + '</span>' +
            (reason ? ' ' + esc(reason) : '') + '</span></div>';
        }).join('')
      : '<div class="stream-empty">没有工程。</div>') + '</div></div>';

  var entries = d.entries || [];
  html += '<div class="wcard"><div class="wcard-title">最近的账目（Entry）</div><div class="wcard-body">' +
    (entries.length
      ? '<table class="wtable"><thead><tr><th>#</th><th>类目</th><th>账户</th>' +
        '<th class="num">变动</th></tr></thead><tbody>' +
        entries.slice().reverse().map(function (e) {
          var delta = wInt(e.delta);
          return '<tr><td>' + esc(String(e.entry_id || '')) + '</td><td>' +
            esc(e.kind || '') + '</td><td>' + esc(e.account || '') +
            '</td><td class="num' + (delta >= 0 ? ' is-plus' : ' is-minus') + '">' +
            esc((delta >= 0 ? '+' : '') + delta) + '</td></tr>';
        }).join('') + '</tbody></table>'
      : '<div class="stream-empty">还没有账目。</div>') + '</div></div>';
  return html + '</div>';
}

function wLoadLedger() {
  if (W.ledger.loading) return;
  var my = ++W.ledger.reqId;
  W.ledger.loading = true;
  W.ledger.error = '';
  renderWorldPanel();
  api('/api/world/ledger').then(function (d) {
    if (my !== W.ledger.reqId) return;
    W.ledger.data = d;
  }).catch(function (e) {
    if (my !== W.ledger.reqId) return;
    W.ledger.data = null;
    W.ledger.error = String((e && e.message) || e);
  }).then(function () {
    if (my !== W.ledger.reqId) return;
    W.ledger.loading = false;
    renderWorldPanel();
  });
}

/* ══════════════ 事件绑定（委托在 #auxBody 上，一次绑好）══════════════ */

function bindWorld() {
  var tab = document.querySelector('.tab[data-panel="world"]');
  if (tab) {
    tab.addEventListener('click', function () {
      if (AUX_KIND !== 'world') return;   // 面板没开成（异常）就什么都不做
      // 每次打开工坊都取新数据（口径同 panels 的 scene / log：不陈设旧账）。
      W.build.list = null; W.build.detail = null; W.build.error = '';
      W.dir.list = null; W.dir.explain = null; W.dir.error = '';
      W.ledger.data = null; W.ledger.error = '';
      if (W.view === 'build') wLoadProjects();
      else if (W.view === 'dir') wLoadDirectives();
      else if (W.view === 'ledger') wLoadLedger();
      else renderWorldPanel();
    });
  }
  var host = $('#auxBody');
  if (host) {
    host.addEventListener('click', function (e) {
      var el = (e.target && e.target.closest) ? e.target.closest(
        '[data-wview],[data-wgen-run],[data-wgen-commit],[data-wgen-export],' +
        '[data-wp-create],[data-wp-reload],[data-wp-op],[data-wp-detail],' +
        '[data-wp-detail-close],[data-wd-create],[data-wd-reload],[data-wd-op],' +
        '[data-wd-explain],[data-wd-explain-close]') : null;
      if (!el) return;
      if (el.hasAttribute('data-wview')) {
        W.view = el.getAttribute('data-wview') || 'gen';
        if (W.view === 'build' && !W.build.list && !W.build.loading) wLoadProjects();
        else if (W.view === 'dir' && !W.dir.list && !W.dir.loading) wLoadDirectives();
        else if (W.view === 'ledger' && !W.ledger.data && !W.ledger.loading) wLoadLedger();
        renderWorldPanel();
      } else if (el.hasAttribute('data-wgen-run')) {
        wGenRun();
      } else if (el.hasAttribute('data-wgen-commit')) {
        wGenCommit();
      } else if (el.hasAttribute('data-wgen-export')) {
        wGenExport();
      } else if (el.hasAttribute('data-wp-create')) {
        wProjectCreate();
      } else if (el.hasAttribute('data-wp-reload')) {
        W.build.detail = null;
        wLoadProjects();
      } else if (el.hasAttribute('data-wp-op')) {
        wProjectOp(el.getAttribute('data-wp-op') || '',
                   el.getAttribute('data-wpid') || '');
      } else if (el.hasAttribute('data-wp-detail')) {
        wProjectDetail(el.getAttribute('data-wp-detail') || '');
      } else if (el.hasAttribute('data-wp-detail-close')) {
        W.build.detail = null;
        renderWorldPanel();
      } else if (el.hasAttribute('data-wd-create')) {
        wDirCreate();
      } else if (el.hasAttribute('data-wd-reload')) {
        W.dir.explain = null;
        wLoadDirectives();
      } else if (el.hasAttribute('data-wd-op')) {
        wDirOp(el.getAttribute('data-wd-op') || '',
               el.getAttribute('data-wsid') || '',
               el.getAttribute('data-wdid') || '');
      } else if (el.hasAttribute('data-wd-explain')) {
        wDirExplain(el.getAttribute('data-wsid') || '',
                    el.getAttribute('data-wd-explain') || '');
      } else if (el.hasAttribute('data-wd-explain-close')) {
        W.dir.explain = null;
        renderWorldPanel();
      }
    });
    host.addEventListener('change', function (e) {
      if (e.target && e.target.matches && e.target.matches('[data-wgen-file]')) {
        wGenImport(e.target.files && e.target.files[0]);
      }
    });
  }
  window.__WORLD = worldSnapshot;
}

onInit(bindWorld);

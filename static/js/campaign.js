/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U-CAMP：新局空态「战役入口」（Issue #222）
   依据 UI-DESIGN.md §2.1：还没有世界 / 剧本绑定时——
     · 场景卡显示「这一局还没有选战役」（js/core.js 的 renderSceneCard）；
     · 行动区显示可选的战役入口：先选世界，再选该世界下的剧本；
       点剧本先看预览（世界 / 剧本信息），确认后才走 POST /api/guide/bind。
   数据面是只读的 GET /api/campaigns（世界 → 该世界下的剧本；列表与绑定
   都按世界过滤，meta.world 必须逐字等于世界 key）。导引者离线同样可用：
   目录与绑定都不碰 AI 配置。

   边界（不重排视图结构）：
     · 只在「未绑定 **且** 行动区没有行动」时接管 #actList 的内容；
       一旦绑定或有了行动，立刻让位给正常的行动区渲染（renderActions）。
     · 不新增区块，全部内容画在既有的 #actList 里。
     · 展示名来自数据文件（world.name / meta.campaign），本片不编造名字。
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* ── 本片状态 ──────────────────────────────────────────── */
var CAMP = {
  catalog: null,     // GET /api/campaigns 的结果（页面会话内缓存）
  fetching: false,   // 目录请求在途
  loadError: '',     // 目录读取失败的原因（有值时不自动重试，等「重试」）
  world: '',         // 选中的世界 key（'' = 还没选）
  scenario: '',      // 打开预览的剧本 id（'' = 没在看预览）
  busy: false,       // 绑定请求在途
  error: ''          // 绑定失败的原文（服务端固定短语）
};

/* 只读快照：测试 / 调试用（同 window.__LOBBY / __TURN 的用途）。 */
function campaignSnapshot() {
  return {
    loaded: !!CAMP.catalog,
    fetching: CAMP.fetching,
    loadError: CAMP.loadError,
    world: CAMP.world,
    scenario: CAMP.scenario,
    busy: CAMP.busy,
    error: CAMP.error,
    worlds: CAMP.catalog ? CAMP.catalog.worlds.length : 0
  };
}

/* 「未绑定」= 会话视图里没有世界 / 剧本（服务端的 world / scenario 块为空）。
   「有行动」= 规则快照里已经有行动——两者任一成立，行动区就不归本片管。 */
function campUnbound() {
  return !(ST && ST.scenario && (ST.scenario.id || ST.scenario.name));
}

function campHasActions() {
  return typeof actionList === 'function' && actionList().length > 0;
}

function campFindWorld(key) {
  var worlds = (CAMP.catalog && CAMP.catalog.worlds) || [];
  for (var i = 0; i < worlds.length; i++) {
    if (worlds[i] && worlds[i].key === key) return worlds[i];
  }
  return null;
}

function campFindScenario(world, id) {
  var list = (world && world.scenarios) || [];
  for (var i = 0; i < list.length; i++) {
    if (list[i] && list[i].id === id) return list[i];
  }
  return null;
}

/* 引擎的展示名：数据里是 tactics / build；认得之外的值就照原样显示。 */
function campEngineLabel(engine) {
  var map = { tactics: '战术引擎', build: '构建引擎' };
  var key = String(engine == null ? '' : engine);
  return map[key] || key;
}

/* 目录惰性加载：一次成功就缓存；失败画错误 + 「重试」（不自动循环重打）。 */
async function campLoadCatalog() {
  if (CAMP.catalog || CAMP.fetching) return;
  CAMP.fetching = true;
  CAMP.loadError = '';
  try {
    var d = await api('/api/campaigns');
    CAMP.catalog = { worlds: (d && d.worlds) || [] };
  } catch (e) {
    CAMP.loadError = String((e && e.message) || e);
  } finally {
    CAMP.fetching = false;
    renderCampaignEntry();      // 目录到手 / 失败：行动区重画一次
  }
}

/* ── 行动区里的战役入口 ────────────────────────────────── */
function campaignHtml() {
  if (CAMP.loadError) {
    return '<div class="camp-entry" data-camp>' +
      '<p class="camp-hint">读取战役目录失败：' + esc(CAMP.loadError) + '</p>' +
      '<div class="camp-acts">' +
        '<button type="button" class="btn" data-camp-retry>重试</button>' +
      '</div></div>';
  }
  if (!CAMP.catalog) {
    return '<div class="camp-entry" data-camp>' +
      '<p class="camp-hint">正在读取战役目录…</p></div>';
  }
  var worlds = CAMP.catalog.worlds || [];
  if (!worlds.length) {
    return '<div class="camp-entry" data-camp>' +
      '<p class="camp-hint">还没有可选的战役。</p></div>';
  }
  var world = CAMP.world ? campFindWorld(CAMP.world) : null;
  var html = '<div class="camp-entry" data-camp>' +
    '<p class="camp-hint">先选一个世界，再选这个世界的剧本。</p>' +
    '<div class="camp-worlds">' + worlds.map(function (w) {
      var on = !!(world && w.key === world.key);
      return '<button type="button" class="btn camp-world' + (on ? ' is-on' : '') +
        '" data-camp-world="' + esc(w.key) + '" aria-pressed="' +
        (on ? 'true' : 'false') + '">' + esc(w.name || w.key) + '</button>';
    }).join('') + '</div>';
  if (world) {
    var scenarios = world.scenarios || [];
    html += '<div class="camp-scenarios">' + (scenarios.length
      ? scenarios.map(function (s) {
          var on = CAMP.scenario === s.id;
          return '<button type="button" class="btn camp-scenario' + (on ? ' is-on' : '') +
            '" data-camp-scenario="' + esc(s.id) + '">' +
            '<span class="camp-scenario-name">' + esc(s.campaign || s.id) + '</span>' +
            '<span class="camp-scenario-meta">' + esc(campEngineLabel(s.engine)) +
            '</span></button>';
        }).join('')
      : '<p class="camp-hint">这个世界还没有剧本。</p>') + '</div>';
    var chosen = CAMP.scenario ? campFindScenario(world, CAMP.scenario) : null;
    if (chosen) html += campPreviewHtml(world, chosen);
  }
  return html + '</div>';
}

function campPreviewHtml(world, scenario) {
  var rows = [
    ['世界', world.name || world.key],
    ['剧本', scenario.campaign || scenario.id],
    ['引擎', campEngineLabel(scenario.engine)]
  ];
  if (scenario.tone_pack) rows.push(['基调', String(scenario.tone_pack)]);
  if (scenario.party_level != null) rows.push(['推荐等级', String(scenario.party_level)]);
  if (scenario.party_size != null) rows.push(['人数', String(scenario.party_size)]);
  return '<div class="camp-preview" data-camp-preview>' +
    '<div class="camp-preview-title">确认这一场？</div>' +
    rows.map(function (r) {
      return '<div class="camp-row"><span class="camp-k">' + esc(r[0]) +
        '</span><span class="camp-v">' + esc(r[1]) + '</span></div>';
    }).join('') +
    '<div class="camp-acts">' +
      '<button type="button" class="btn btn-primary" data-camp-confirm' +
        (CAMP.busy ? ' aria-busy="true" disabled' : '') + '>确认绑定</button>' +
      '<button type="button" class="btn" data-camp-back' +
        (CAMP.busy ? ' disabled' : '') + '>返回</button>' +
    '</div>' +
    (CAMP.error
      ? '<p class="camp-error" role="alert">绑定失败：' + esc(CAMP.error) + '</p>'
      : '') +
    '</div>';
}

/* 空态时行动区画战役入口；其余情况让位给正常渲染。
   核心不变量：只有「未绑定 且 没有行动」才接管 #actList。 */
function renderCampaignEntry() {
  var host = $('#actList');
  if (!host) return;
  var picking = !!host.querySelector('[data-camp]');
  if (!campUnbound() || campHasActions()) {
    if (picking && typeof renderActions === 'function') renderActions();
    CAMP.world = ''; CAMP.scenario = ''; CAMP.error = '';
    return;
  }
  if (!CAMP.catalog && !CAMP.loadError && !CAMP.fetching) campLoadCatalog();
  host.innerHTML = campaignHtml();
}

/* 确认绑定：沿用既有的 POST /api/guide/bind（幂等；离线可用）。
   成功后就地更新 ST（与 GET /api/session 的 world / scenario 同形）再重画，
   renderCampaignEntry 会把行动区交还给正常渲染。 */
async function campBind() {
  if (CAMP.busy) return;
  var world = campFindWorld(CAMP.world);
  var scenario = world ? campFindScenario(world, CAMP.scenario) : null;
  if (!world || !scenario) return;
  CAMP.busy = true;
  CAMP.error = '';
  renderCampaignEntry();
  try {
    await api('/api/guide/bind', {
      method: 'POST',
      body: { world_key: world.key, scenario_id: scenario.id }
    });
    if (ST) {
      ST.world = { key: world.key, name: world.name || world.key };
      ST.scenario = { id: scenario.id, name: scenario.campaign || scenario.id };
    }
    CAMP.busy = false;
    buzz(12);
    toast('已绑定：' + (world.name || world.key) + ' · ' +
      (scenario.campaign || scenario.id));
    renderSceneCard();
    renderCampaignEntry();
  } catch (e) {
    CAMP.busy = false;
    CAMP.error = String((e && e.message) || e);
    // 两个标签页抢绑：这一局其实已经绑过了——把整局视图拉回来，让入口收起。
    if (CAMP.error === '已经绑定' && typeof refreshTurnSession === 'function') {
      refreshTurnSession();
    }
    renderCampaignEntry();
  }
}

/* ── 本片的事件绑定：委托在 #actList 上，一次绑好（内容随重画更换）── */
function bindCampaign() {
  var host = $('#actList');
  if (host) {
    host.addEventListener('click', function (e) {
      var el = (e.target && e.target.closest) ? e.target.closest(
        '[data-camp-world],[data-camp-scenario],[data-camp-confirm],' +
        '[data-camp-back],[data-camp-retry]') : null;
      if (!el) return;
      if (el.hasAttribute('data-camp-world')) {
        CAMP.world = el.getAttribute('data-camp-world') || '';
        CAMP.scenario = '';
        CAMP.error = '';
        renderCampaignEntry();
      } else if (el.hasAttribute('data-camp-scenario')) {
        CAMP.scenario = el.getAttribute('data-camp-scenario') || '';
        CAMP.error = '';
        renderCampaignEntry();
      } else if (el.hasAttribute('data-camp-confirm')) {
        campBind();
      } else if (el.hasAttribute('data-camp-back')) {
        CAMP.scenario = '';
        CAMP.error = '';
        renderCampaignEntry();
      } else if (el.hasAttribute('data-camp-retry')) {
        CAMP.loadError = '';
        renderCampaignEntry();      // catalog 仍为空 → 自动重新拉取
      }
    });
  }
  window.__CAMPAIGN = campaignSnapshot;
}

onInit(bindCampaign);

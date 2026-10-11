/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U5：对白拍与语音（UI-DESIGN.md §2.5 / §6 U5）

   · 叙事条目带 `beats` 时按拍切段：对白拍（`speaker` 非空）加左侧竖线与
     说话人名，旁白拍就是普通段落——**音色名（白桦 / 茉莉）不显示**，
     它是 TTS 的声线，不是角色名；
   · 对白拍旁有「朗读」按钮（小喇叭），只在 `tts=true` 时出现（能力探测由
     U2 的 `TURN.guide` 持有）；
   · 点按走 `POST /api/guide/speak`：体 `{ text }`，`text` 必须是那一拍的
     **全文**（服务端按全等匹配，传半句 / 改标点会得「没有可朗读的句子」）；
     响应是 24 kHz / 单声道 / s16le 的**分块流且没有 Content-Length**，
     所以用 AudioContext（sampleRate: 24000）+ AudioBufferSourceNode
     边收边播，**不用** `<audio src>`（浏览器不认 `audio/pcm16`）；
   · **不重试**（TTS 不重试是服务端口径）：`语音不可用` / 播放失败 →
     按钮变灰 + `toast` 一次，正文照常可见——语音是增益，不是通路；
   · 设置里的「朗读导引者对白」默认**关**；开启后只自动播**旁白拍**，
     对白拍一律手动点（多个角色同时说话自动播会互相打断）。

   与 U2 的衔接点只有一处：js/turn.js 渲染含 `beats` 的叙事条目时调用本文件
   提供的**可选全局** `voiceNarrateHtml(entry)`；它不存在 / 返回空 / 抛错时
   turn.js 走自己的整段渲染，默认渲染一字不变。本片只**读**条目数据。

   共用件（$ / $$ / esc / toast / SETTINGS / SID / TURN …）见 js/core.js。
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* ── 本片状态 ──────────────────────────────────────────── */
var VOICE = {
  ctx: null,         // AudioContext（sampleRate 24000，首次播放时才建）
  playingText: '',   // 正在播的那一拍**全文**：按钮状态与「停止」都认它
  queue: [],         // 自动朗读的待播队列（只排旁白拍）
  sources: [],       // 本次播放已调度的 AudioBufferSourceNode（停止时逐个停）
  nextTime: 0,       // 下一块音频的调度时刻（边收边播：不整段等下载完）
  timer: null,       // 收尾定时器（最后一块的结束时刻到了才算播完）
  token: 0,          // 播放代次：停止 / 换一段后，在途的旧读取一律作废
  failed: {},        // 失败过的拍（全文 → true）：按钮变灰，**不重试**（§2.5）
  seen: []           // 自动朗读去重：已排过队的**条目**（对象身份）
};

var VOICE_SAMPLE_RATE = 24000;   // 服务端音频规格：24 kHz / 单声道 / s16le（§2.5）

/* ══════════════ 设置项：朗读导引者对白 ══════════════
   UI-DESIGN §1.3.6 的目标态追加项：开 / 关，默认**关**（语音要花上游额度，
   手机静音场景常见）。「任何新增键都必须进 SETTINGS_DEF，loadSettings() 只
   接受类型一致的值」——core.js 是禁改路径，所以本片在**加载期**把键补进那份
   定义表（core 的 loadSettings() 在 DOMContentLoaded 的 boot 里才跑，晚于这里），
   再包一层 renderSettings() 把开关挂进设置页（与 U2 包 showView 同法）。 */
SETTINGS_DEF.readAloud = false;
if (typeof SETTINGS.readAloud !== 'boolean') SETTINGS.readAloud = false;

var CORE_RENDER_SETTINGS = renderSettings;
renderSettings = function () {
  CORE_RENDER_SETTINGS();
  voiceSettingsRow();
};

/* core 每次 renderSettings 都会重写 #settingsForm，所以这里每次跟着重挂——
   反复进出设置页既不丢这个开关，也不会挂出第二个。挂在「反馈」组的末尾
   （组骨架与另两组都不动）。 */
function voiceSettingsRow() {
  var form = $('#settingsForm');
  if (!form) return;
  var row = document.createElement('div');
  row.className = 'set-row';
  row.innerHTML =
    '<div class="set-label">朗读导引者对白<small>开启后只自动播旁白，对白仍需手动点</small></div>' +
    '<label class="switch"><input type="checkbox" id="setReadAloud" data-toggle="readAloud"' +
      (SETTINGS.readAloud ? ' checked' : '') + '><i></i></label>';
  var anchor = form.querySelector('[data-toggle="autoScroll"]');
  var holder = anchor && anchor.closest ? anchor.closest('.set-row') : null;
  if (holder && holder.parentNode) holder.parentNode.insertBefore(row, holder.nextSibling);
  else form.appendChild(row);
  var box = row.querySelector('#setReadAloud');
  if (box) box.addEventListener('change', function () {
    SETTINGS.readAloud = !!box.checked;
    applySettings();                 // core 的持久化口径（localStorage + <html data-*>）
    if (!SETTINGS.readAloud) voiceStop();   // 关掉开关就地停掉自动朗读
  });
}

/* 能力探测（U2 的 `GET /api/guide/status`）落定时再回贴一次按钮状态：
   `TURN.guide.tts` 决定「朗读」入口在不在，而探针可能晚于第一条带 beats 的
   叙事（首屏恢复与探测并行）。turn.js 是禁改路径，所以从外面包一层——
   与上面包 renderSettings 同法。 */
var CORE_LOAD_GUIDE_STATUS = loadGuideStatus;
loadGuideStatus = function () {
  var p = CORE_LOAD_GUIDE_STATUS.apply(this, arguments);
  if (p && typeof p.then === 'function') p.then(voiceRepaint, function () {});
  return p;
};

/* ══════════════ 按拍渲染（turn.js 的衔接点）══════════════ */

/* 服务端切拍用的**同一条**对白记号（prism_guide._BEAT_DIALOGUE_RE）：
   「名字（1–8 个汉字）＋冒号＋「…」」。拍上只有 `speaker`（NPC id）与音色，
   说话人名只在正文里，所以名字从这里回收——音色名不显示（§2.5）。 */
var VOICE_DIALOGUE_RE = /([\u4e00-\u9fff]{1,8})[：:]\s*「([^」]*)」/g;

function voiceMarks(text) {
  var s = String(text == null ? '' : text);
  var out = [];
  var m;
  VOICE_DIALOGUE_RE.lastIndex = 0;
  while ((m = VOICE_DIALOGUE_RE.exec(s)) !== null) {
    out.push({ name: m[1], line: m[2], start: m.index, end: m.index + m[0].length });
  }
  return out;
}

/* 返回一条叙事条目正文块的 HTML（不含痕迹块、不含外层 .msg）。
   返回空串 = 「本片不管这条」，turn.js 退回整段渲染——两种情况：
     · 条目没有 `beats`（U2 的条目、/api/log 恢复出来的条目）；
     · 任何一拍在正文里定位不到（契约外的载荷）：宁可整段照旧，也不丢字。 */
function voiceNarrateHtml(entry) {
  var beats = (entry && entry.beats) || [];
  if (!beats.length) return '';
  var text = String((entry && entry.text) || '');
  var marks = voiceMarks(text);
  var cursor = 0;          // 正文里已经画过的位置
  var markAt = 0;          // 对白记号的游标（拍与记号都按正文顺序走）
  var html = '';
  var i;

  // 先按设置排上自动朗读，再画拍：`voiceSpeak` 的同步部分只置播放状态，
  // 于是这一条自己就画成「正在播 + 停止」——不用等下一次重画才补上
  // （自动朗读的是旁白拍，它平时没有按钮，停止入口就靠这一笔画出来）。
  voiceAutoRead(entry);

  for (i = 0; i < beats.length; i++) {
    var raw = String((beats[i] && beats[i].text) || '');
    // 服务端把「正文 + 钩子」用 `\n\n` 拼起来再切拍（§5.9），所以拍的首尾
    // 空白不一定原样出现在正文里：定位用 trim 过的字，**发给服务端的仍是
    // 那一拍的全文**（data-vis-beat 存的是原文，服务端按全等匹配）。
    var line = raw.trim();
    var speaker = String((beats[i] && beats[i].speaker) || '');
    var at = line ? text.indexOf(line, cursor) : cursor;
    if (line && at < 0) return '';          // 定位不到：整段渲染，一字不丢
    if (!speaker) {                          // 旁白拍：普通段落
      html += voiceGapHtml(text.slice(cursor, at)) + voiceBeatHtml(raw, '');
      cursor = at + line.length;
      continue;
    }
    // 对白拍：名字与「」都在正文里，找到覆盖这句台词的记号，连带画进去
    var mark = null;
    var j;
    for (j = markAt; j < marks.length; j++) {
      if (marks[j].line === raw) { mark = marks[j]; markAt = j + 1; break; }
    }
    html += voiceGapHtml(text.slice(cursor, mark ? mark.start : at)) +
      voiceBeatHtml(raw, mark ? mark.name : '');
    cursor = mark ? mark.end : at + line.length;
  }
  html += voiceGapHtml(text.slice(cursor));
  return html;
}

/* 拍覆盖不到的正文——服务端切拍只取「正文 + 钩子」，`【裁决】` 那一段与两个
   段标题都不在拍里（§5.9 不朗读它）。U2 画的字这里一个都不能少，原样留成
   普通段落。 */
function voiceGapHtml(gap) {
  var t = String(gap == null ? '' : gap).trim();
  return t ? '<p class="msg-beat">' + esc(t) + '</p>' : '';
}

/* 一拍 = 一个段落。「朗读」入口只在对白拍 + tts=true 时画出来（§2.5）；
   全文按 `data-vis-beat` 存在这里，点按 / 状态回贴都从它读——用
   encodeURIComponent 是为了让**新行**原样活过属性值，服务端按全等匹配
   `last_beats` 里的全文，少一个换行就是「没有可朗读的句子」。 */
function voiceBeatHtml(line, name) {
  var text = String(line == null ? '' : line);
  return '<p class="msg-beat' + (name ? ' is-dialogue' : '') + '"' +
    ' data-vis-beat="' + esc(encodeURIComponent(text)) + '">' +
    voiceBeatInnerHtml(text, name) + '</p>';
}

/* 一拍的内部结构：名字行（名字 + 朗读 / 停止按钮）+ 台词。
   渲染与「状态回贴」共用这一个函数——播放状态变化时只换这一小块，
   正文的节点不动（`#stream` 是 aria-live 区，见 U2 的 streamSig 注释）。
   入口只在对白拍 + `tts=true` 时出现；正在播的**旁白**拍也会挂一个「停止」：
   自动朗读时它是唯一的停止入口。 */
function voiceBeatInnerHtml(text, name) {
  var playing = !!text && VOICE.playingText === text;
  var dead = !!VOICE.failed[text];
  var playable = !!name && voiceTts();
  var btn = '';
  if (playable || playing) {
    var label = dead ? '朗读这句话（语音不可用）' : (playing ? '停止朗读' : '朗读这句话');
    btn = '<button type="button" class="beat-say' +
      (playing ? ' is-playing' : '') + '"' +
      (dead ? ' disabled' : '') + ' aria-label="' + label + '">' +
      voiceIconHtml(playing ? 'stop' : 'sound') +
      (playing ? '<span class="beat-say-label">停止</span>' : '') +
      '</button>';
  }
  return '<span class="beat-head">' +
      (name ? '<span class="beat-name">' + esc(name) + '</span>' : '') + btn +
    '</span>' +
    '<span class="beat-line">' + esc(String(text).trim()) + '</span>';
}

/* 图标随按钮走、不写进 index.html 的 sprite：本片在 index.html 上只允许
   「必要的结构挂点」，而这两个图标只属于「朗读 / 停止」这一处。
   画法与 sprite 同源：纯几何线条、无外部字体与图片，样式由 .i 提供。 */
function voiceIconHtml(kind) {
  if (kind === 'stop') {
    return '<svg class="i i-sm" aria-hidden="true" viewBox="0 0 24 24">' +
      '<rect x="7" y="7" width="10" height="10" rx="1.6"/></svg>';
  }
  return '<svg class="i i-sm" aria-hidden="true" viewBox="0 0 24 24">' +
    '<path d="M4 9.5h3.4L11.5 6v12L7.4 14.5H4z"/>' +
    '<path d="M14.8 9.6a3.6 3.6 0 0 1 0 4.8"/></svg>';
}

/* 播放状态回贴：只改按钮与那一拍的类名，不重画整条流。
   名字行重画一次是刻意的——`playable` 跟着当前 `tts` 走，能力探测翻了面
   （`tts` 由真变假 / 由假变真）时按钮跟着消失 / 出现。 */
function voiceRepaint() {
  $$('#stream .msg-beat[data-vis-beat]').forEach(function (beat) {
    var text = voiceAttr(beat, 'data-vis-beat');
    var nameEl = beat.querySelector('.beat-name');
    beat.classList.toggle('is-playing', !!VOICE.playingText && VOICE.playingText === text);
    beat.innerHTML = voiceBeatInnerHtml(text, nameEl ? nameEl.textContent : '');
  });
}

/* 属性里存的是 encodeURIComponent 的全文：解回来才是要发给服务端的那一拍。 */
function voiceAttr(el, name) {
  var raw = (el && el.getAttribute) ? String(el.getAttribute(name) || '') : '';
  if (!raw) return '';
  try { return decodeURIComponent(raw); } catch (e) { return ''; }
}

/* ══════════════ 导引者语音是否可用（§1.3.8 / §2.5）══════════════
   能力探测来自 `GET /api/guide/status`，U2 已把它存进 TURN.guide；
   `tts=false`（含探测拿不到）时「朗读」入口不出现。 */
function voiceTts() {
  return !!(typeof TURN !== 'undefined' && TURN && TURN.guide && TURN.guide.tts);
}

/* ══════════════ 自动朗读（§2.5）══════════════
   仅在设置里开了「朗读导引者对白」时播**旁白拍**，对白拍一律手动点。
   由衔接点顺带触发（「渲染完这条含 beats 的条目之后」），用**条目对象身份**
   去重：同一条目在重画 / 会话刷新时会再渲染一遍，只认第一次。 */
function voiceAutoRead(entry) {
  if (!entry || typeof entry !== 'object') return;
  if (VOICE.seen.indexOf(entry) >= 0) return;
  if (VOICE.seen.length > 200) VOICE.seen.shift();
  VOICE.seen.push(entry);      // **无论开关**都记上：开关是「此后」的口径，重画不追播旧条目
  if (!SETTINGS.readAloud || !voiceTts()) return;
  var texts = ((entry && entry.beats) || []).filter(function (b) {
    return b && !String(b.speaker || '') && String(b.text || '').trim() &&
      !VOICE.failed[String(b.text)];
  }).map(function (b) { return String(b.text); });
  if (!texts.length) return;
  VOICE.queue = VOICE.queue.concat(texts);
  voiceNextQueued();
}

function voiceNextQueued() {
  if (VOICE.playingText || !VOICE.queue.length) return;
  voiceSpeak(VOICE.queue.shift());
}

/* ══════════════ 播放：AudioContext · 边收边播 ══════════════
   响应是分块流且没有 Content-Length，所以不能等整段下完、也不能用
   `<audio src>`：一边读流一边把整块样本排进 AudioBufferSourceNode 的
   播放时间线（`VOICE.nextTime` 接着上一块的结束时刻）。 */

function voiceCtx() {
  if (VOICE.ctx) return VOICE.ctx;
  var AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  try { VOICE.ctx = new AC({ sampleRate: VOICE_SAMPLE_RATE }); }
  catch (e) { try { VOICE.ctx = new AC(); } catch (e2) { VOICE.ctx = null; } }
  return VOICE.ctx;
}

/* 一块 pcm16（s16le）→ 一段 Float32 样本 → 排进播放时间线。 */
function voiceSchedule(ctx, bytes) {
  var n = (bytes && bytes.length ? bytes.length : 0) >> 1;   // 只吃整样本
  if (!n) return;
  var buf = ctx.createBuffer(1, n, VOICE_SAMPLE_RATE);
  var ch = buf.getChannelData(0);
  var dv = new DataView(bytes.buffer, bytes.byteOffset, n * 2);
  for (var i = 0; i < n; i++) ch[i] = dv.getInt16(i * 2, true) / 32768;
  var src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(ctx.destination);
  var at = Math.max(VOICE.nextTime, ctx.currentTime);
  src.start(at);
  VOICE.nextTime = at + buf.duration;
  VOICE.sources.push(src);
}

/* 点「朗读」→ `POST /api/guide/speak` → 边收边播。同一个按钮再点一次 = 停止。 */
async function voiceSpeak(line) {
  var text = String(line == null ? '' : line);
  if (!text) return;
  if (VOICE.playingText === text) { voiceStop(); return; }
  voiceStop();
  var ctx = voiceCtx();
  if (!ctx) { voiceFail(text, '这台设备不能播放语音'); return; }
  var token = ++VOICE.token;
  VOICE.playingText = text;
  voiceRepaint();
  // 自动播可能被浏览器的自动播放策略拦下：能恢复就恢复，**不等**它——
  // `resume()` 在用户手势之前可能一直不落地，等它就等于把请求也一起卡死
  // （听不到不是错误，正文与按钮都不受影响）。
  try { if (ctx.state === 'suspended') ctx.resume().catch(function () {}); } catch (e) { /* 被拦下 */ }

  var res;
  try {
    var headers = { 'Content-Type': 'application/json' };
    if (SID) headers['X-Session'] = SID;
    res = await fetch('/api/guide/speak', {
      method: 'POST', headers: headers, body: JSON.stringify({ text: text })
    });
  } catch (e) {
    if (token === VOICE.token) voiceFail(text, '无法连接服务，请确认后端仍在运行');
    return;
  }
  if (token !== VOICE.token) return;              // 已经点了停止 / 换了一段
  voiceRepaint();                                 // 按钮可能刚被重画过：补一次状态

  var ctype = String(res.headers.get('content-type') || '').toLowerCase();
  if (!res.ok || ctype.indexOf('json') >= 0) {
    // 4xx 一律是固定 JSON（`语音不可用` / `没有可朗读的句子` / 太频繁 …）：
    // 照抄服务端原文，不自己编话术（§1.3.10 的口径）。
    var data = null;
    try { data = await res.json(); } catch (e2) { /* 非 JSON 响应 */ }
    if (token !== VOICE.token) return;
    voiceFail(text, String((data && data.error) || ('播放失败（' + res.status + '）')));
    return;
  }

  var left = null;              // 半个样本的尾字节：留到下一块，样本不能错位
  var ok = true;
  try {
    if (res.body && res.body.getReader) {
      var reader = res.body.getReader();
      for (;;) {
        var chunk = await reader.read();
        if (token !== VOICE.token) return;
        if (chunk.done) break;
        var bytes = chunk.value || new Uint8Array(0);
        if (left) {
          var merged = new Uint8Array(left.length + bytes.length);
          merged.set(left, 0); merged.set(bytes, left.length);
          bytes = merged; left = null;
        }
        if (bytes.length % 2) { left = bytes.slice(bytes.length - 1); bytes = bytes.slice(0, bytes.length - 1); }
        voiceSchedule(ctx, bytes);
      }
    } else {
      var all = new Uint8Array(await res.arrayBuffer());
      if (all.length % 2) all = all.slice(0, all.length - 1);
      voiceSchedule(ctx, all);
    }
  } catch (e) {
    ok = false;
  }
  if (token !== VOICE.token) return;
  if (!ok) { voiceFail(text, '播放失败'); return; }

  // 调度完了再等最后一块播完：结束时刻是确定的（= 最后一块的 at + 时长）。
  var wait = Math.max(0, VOICE.nextTime - ctx.currentTime) * 1000 + 120;
  if (VOICE.timer) clearTimeout(VOICE.timer);
  VOICE.timer = setTimeout(function () {
    VOICE.timer = null;
    if (token !== VOICE.token) return;
    voiceFinish();
  }, wait);
}

function voiceFinish() {
  VOICE.playingText = '';
  VOICE.sources = [];
  VOICE.nextTime = 0;
  voiceRepaint();
  voiceNextQueued();            // 自动朗读的下一拍
}

/* 停止：在途的读取作废、已调度的样本立刻停、自动朗读的队列一并清掉。 */
function voiceStop() {
  voiceTeardown();
  voiceRepaint();
}

function voiceTeardown() {
  VOICE.token++;
  VOICE.playingText = '';
  VOICE.queue = [];
  VOICE.nextTime = 0;
  if (VOICE.timer) { clearTimeout(VOICE.timer); VOICE.timer = null; }
  VOICE.sources.forEach(function (s) { try { s.stop(0); } catch (e) { /* 已经停了 */ } });
  VOICE.sources = [];
}

/* 降级（§2.5）：按钮变灰 + toast **一次**，正文一动不动；这一拍**不重试**。 */
function voiceFail(line, message) {
  VOICE.failed[line] = true;
  voiceTeardown();
  voiceRepaint();
  voiceToastOnce(String(message || '播放失败'));
}

/* 「toast 一次」：同一句提示短时间内不重复弹（连续失败不刷屏）。 */
var VOICE_TOAST = { msg: '', at: 0 };
function voiceToastOnce(msg) {
  var now = Date.now();
  if (msg === VOICE_TOAST.msg && now - VOICE_TOAST.at < 4000) { VOICE_TOAST.at = now; return; }
  VOICE_TOAST.msg = msg;
  VOICE_TOAST.at = now;
  toast(msg);
}

/* ══════════════ 本片事件绑定 ══════════════ */

/* 事件委托：叙事流是整块重画的（U2 的 renderStream 直接换 innerHTML），
   逐个按钮绑监听会被下一次重画丢掉；委托一次，按钮随画随有。
   「朗读」按钮本身就是 <button>，Tab 可达（§2.5 的键盘要求）。 */
function voiceOnClick(ev) {
  var el = ev.target;
  var btn = (el && el.closest) ? el.closest('.beat-say') : null;
  if (!btn || btn.disabled) return;
  var beat = btn.closest('.msg-beat');
  var text = voiceAttr(beat, 'data-vis-beat');
  if (!text) return;
  ev.preventDefault();
  voiceSpeak(text);
}

function voiceSnapshot() {
  return {
    tts: voiceTts(),
    auto: !!SETTINGS.readAloud,
    playing: VOICE.playingText,
    queue: VOICE.queue.length,
    failed: Object.keys(VOICE.failed).length,
    beats: $$('#stream .msg-beat').length,
    dialogue: $$('#stream .msg-beat.is-dialogue').length,
    buttons: $$('#stream .beat-say').length,
    playingButtons: $$('#stream .beat-say.is-playing').length
  };
}

function bindVoice() {
  document.addEventListener('click', voiceOnClick);
  window.__VOICE = voiceSnapshot;     // 只读快照，供测试 / 调试核对本片状态
}

onInit(bindVoice);

/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U4：状态与记录面板（队伍 / 世界 / 记录）
   底栏三个 tab 的高亮切换（现状只改高亮并弹「尚未接通」）。
   共用件在 js/core.js；本片后续把三个面板做成真面板时，只改本文件
   （与 css/panels.css）——面板复用 #auxSheet 的 L2 辅助层，
   不需要动 core 的浮层状态机。
   ═══════════════════════════════════════════════════════════ */
'use strict';

function bindPanels() {
  $$('.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      $$('.tab').forEach(function (x) {
        var on = x === t;
        x.classList.toggle('is-on', on);
        x.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      toast('示例骨架：该面板尚未接通');
    });
  });
}

onInit(bindPanels);

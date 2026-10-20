#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建引擎（骰池）六角格适配器（Issue #425，M13-B10）。

让使用构建引擎的战斗角色也能消费六角格 TacticalContext（地形、高度、
掩体、视线与环境交互），而不是被降级成 d20 路径（02C 第六节「双引擎
适配契约」管线的第 4 步，构建引擎侧）。

分层职责（与 Issue #448 的边界，防重复实现）：

- **映射逻辑复用 #448**：`prism_core.map_context_for_build` /
  `prism_core.validate_tactical_context` 已在规则核心落地（掩体 /
  高地 → dodge_need_modifiers 每项 +1，02B:261；封顶仍由
  `build_dodge_need_adjusted` 的 BUILD_DODGE_MODIFIER_MAX 执行）。
  本模块 **import 调用，不重写映射**；
- **本模块职责** = context 校验入口（`check_context`）+ 构建引擎
  **支持矩阵**（`forge_support_matrix`，明确支持 / 显式不支持 /
  显式不映射）+ 调用编排（`resolve_build_attack`）+ 结构化返回
  （适配输入、所用规则 id、结果摘要、未映射特征、来源信息）；
- **零公式**：骰池构成、骰阶、成功阈值、需求成功数、结果梯度、
  动量、超载连锁等全部在 `prism_core`（docs/system/02B），本模块
  只编排调用，不写任何数值与判定公式；应力 / 动量池 / 符纹插槽
  等会话机制同样留在核心，本模块不复制第二套管线；
- **算法隔离（02B 第十一节「混用限制」）**：不把 d20 的助势骰、
  命中五 / 六档或 AP 修正当作构建结算输入——成功数只由骰池构成
  决定，空间事实只影响防御侧的闪避需求；
- **不静默**：没有构建规则依据的空间事实逐项进 `unmapped_features`
  （地形标签 / 特征引用 / 移动视图）或 #448 的 `not_mapped`
  （距离 / 高度差），绝不静默丢弃、不擅自编造效果；stale /
  invalid context 结构化拒绝（status="context_rejected"），不降级
  成「当没有 context」；视线被遮（attack_blocked）与「完全」掩体
  （无数值档）不掷骰结算——目标合法性事实显式返回，归调用方处理
  （02A:264 / 02C:121 同一口径）；
- **无 context 兼容**：`context=None`（或缺省）时修正为 0、无阻断，
  结算与既有构建引擎调用完全一致（旧构建战斗不受影响）；
- **可序列化**：输出全部是 str / int / float / bool / None /
  list / dict，键序冻结，可直接 JSON 落盘。

已知口径：`movement_destination` 是 #447 `build_context` 的显式参数
（不默认指向目标格，见 tactical_context.py docstring）。
"""
import prism_core as pc

__all__ = [
    "ADAPTER_SCHEMA_VERSION",
    "ADAPTER_KIND",
    "ENGINE_ID",
    "FORGE_UNCONSUMED_FIELDS",
    "forge_support_matrix",
    "check_context",
    "resolve_build_attack",
]

ADAPTER_SCHEMA_VERSION = 1
ADAPTER_KIND = "tactical.forge_adapter_result"
ENGINE_ID = "forge"

# 结果状态（受控枚举）：
#   ok                 —— 已按构建引擎规则结算；
#   context_rejected   —— context stale / invalid，结构化拒绝（不降级）；
#   attack_blocked     —— 视线被遮（02C:121），目标合法性归调用方；
#   unsupported_target —— 目标带无法映射的空间事实（「完全」掩体），
#                         不猜测数值，归调用方处理（02A:264）。
FORGE_RESULT_STATUSES = ("ok", "context_rejected", "attack_blocked",
                         "unsupported_target")

# 适配层不消费的 context 字段（构建攻击结算没有对应的输入位）：
# 地形标签与特征引用没有构建规则依据（02B 未定义地形修正输入）；
# 移动视图属于移动管线（#419），不参与攻击结算。逐项显式声明，
# 不静默丢弃——后续若 02B 扩展地形规则，从这里接入。
FORGE_UNCONSUMED_FIELDS = (
    "terrain_tags",
    "active_feature_refs",
    "reachable",
    "path",
    "path_cost",
    "movement_blocked",
)

_UNCONSUMED_WHY = {
    "terrain_tags": "构建规则（02B）没有地形修正输入；显式不映射，"
                    "不静默丢弃、不臆造效果",
    "active_feature_refs": "特征引用没有构建规则依据（02B 未定义）；"
                           "显式不映射，供后续扩展",
    "reachable": "移动视图属移动管线（#419），不参与构建攻击结算",
    "path": "移动视图属移动管线（#419），不参与构建攻击结算",
    "path_cost": "移动视图属移动管线（#419），不参与构建攻击结算",
    "movement_blocked": "移动视图属移动管线（#419），不参与构建攻击结算",
}


def forge_support_matrix() -> list:
    """构建引擎的空间事实支持矩阵（纯数据，可 JSON 序列化）。

    每个条目声明一类空间事实在构建引擎下的处置：supported（有规则
    依据，映射到既有输入）/ unsupported（规则明确没有数值档）/
    not_mapped（引擎没有该输入位，显式声明）。规则 id 与 #448
    `prism_core.CONTEXT_RULE_REFS` 同源，本模块不新造锚点。
    """
    return [
        {"fact": "high_ground", "value": True, "status": "supported",
         "effect": "dodge_need_modifiers +1（目标的位置优势增加需求成功数）",
         "rule_ref": pc.CONTEXT_RULE_REFS["build_dodge_modifiers"]},
        {"fact": "cover_grade", "value": "light/medium/heavy",
         "status": "supported",
         "effect": "dodge_need_modifiers +1（掩体位置优势；封顶归 "
                   "build_dodge_need_adjusted）",
         "rule_ref": pc.CONTEXT_RULE_REFS["build_dodge_modifiers"]},
        {"fact": "cover_grade", "value": "full", "status": "unsupported",
         "reason": "「完全」掩体没有需求成功数档（02A 掩体表：无法被攻击）；"
                   "目标合法性由调用方处理，本层不猜数值",
         "rule_ref": pc.CONTEXT_RULE_REFS["cover_full"]},
        {"fact": "line_of_sight", "value": False, "status": "supported",
         "effect": "attack_blocked=True（视线被遮，目标合法性事实）",
         "rule_ref": pc.CONTEXT_RULE_REFS["line_of_sight"]},
        {"fact": "hex_distance/metric_distance/abstract_distance/"
                 "signed_height_delta", "value": None, "status": "not_mapped",
         "reason": "骰池引擎未定义距离 / 高度差修正输入；d20 助势骰与加值"
                   "不得移植进骰池（02B 混用限制）",
         "rule_ref": pc.CONTEXT_RULE_REFS["adapter_contract"]},
        {"fact": "terrain_tags", "value": None, "status": "not_mapped",
         "reason": _UNCONSUMED_WHY["terrain_tags"],
         "rule_ref": pc.CONTEXT_RULE_REFS["adapter_contract"]},
        {"fact": "active_feature_refs", "value": None, "status": "not_mapped",
         "reason": _UNCONSUMED_WHY["active_feature_refs"],
         "rule_ref": pc.CONTEXT_RULE_REFS["adapter_contract"]},
        {"fact": "reachable/path/path_cost/movement_blocked", "value": None,
         "status": "not_mapped",
         "reason": _UNCONSUMED_WHY["reachable"],
         "rule_ref": pc.CONTEXT_RULE_REFS["adapter_contract"]},
    ]


def _unmapped_features(context) -> list:
    """适配层不消费且**非空**的 context 字段 → 逐项声明（不静默丢弃）。"""
    items = []
    for fact in FORGE_UNCONSUMED_FIELDS:
        value = context.get(fact)
        if value:
            items.append({"fact": fact, "value": value,
                          "reason": _UNCONSUMED_WHY[fact]})
    return items


def check_context(context) -> dict:
    """context 校验入口（#425 适配器的第一道门）。

    校验逻辑**复用 #448** `prism_core.validate_tactical_context`（来源 /
    字段 / 状态三道检查，stale / invalid 一律拒绝，不在此重写）。在此
    之上附一份适配层的 `unmapped_features` 声明（构建结算不消费且非空
    的字段），让调用方在结算前就能看到哪些事实不会进入本次结算。

    返回 `{"ok", "reasons", "facts", "unmapped_features"}`；纯函数，
    输出可 JSON 序列化。
    """
    check = pc.validate_tactical_context(context)
    result = {"ok": check["ok"], "reasons": check["reasons"],
              "facts": check["facts"], "unmapped_features": []}
    if check["ok"] and isinstance(context, dict):
        result["unmapped_features"] = _unmapped_features(context)
    return result


def _source_info(context) -> dict:
    """来源信息（追溯用）：context 的身份与修订快照；无 context 全 None。"""
    if not isinstance(context, dict):
        return {"context_kind": None, "context_status": None,
                "state_revision": None, "battle_id": None,
                "battlefield_id": None, "actor_id": None, "target_id": None}
    return {"context_kind": context.get("kind"),
            "context_status": context.get("status"),
            "state_revision": context.get("state_revision"),
            "battle_id": context.get("battle_id"),
            "battlefield_id": context.get("battlefield_id"),
            "actor_id": context.get("actor_id"),
            "target_id": context.get("target_id")}


def resolve_build_attack(context=None, *, attribute, prof_bonus, target_type,
                         specialization=False, temp_dice=0,
                         sides=pc.BUILD_DEFAULT_SIDES, rng=None,
                         used_this_scene=False) -> dict:
    """一次构建引擎攻击的适配结算（02C:172 管线第 4 步，骰池侧）。

    参数：

    - `context`：TacticalContext（#447 冻结契约）。`None`（或缺省）=
      无六角格事实的旧构建战斗——修正 0、无阻断，与既有构建结算
      100% 兼容；
    - `attribute` / `prof_bonus` / `specialization` / `temp_dice`：
      骰池四部分（02B 第一节），原样交给 `prism_core.build_pool`；
    - `target_type`：闪避需求 5 档 id 或中文名（static/standard/...）；
    - `sides`：骰阶骰面（通常经 `prism_core.build_rank_die(rank)` 取得）；
    - `rng`：确定性随机（测试注入）；`used_this_scene`：超载连锁
      每场景限次的调用方记账。

    编排（全部公式在 `prism_core`，本函数零公式）：

      1. context 非 None 时经 `map_context_for_build`（#448）取得
         `dodge_need_modifiers` / `attack_blocked` 与 mapped /
         unsupported / not_mapped 明细；
      2. stale / invalid → `status="context_rejected"`，来源 reasons
         原样透传，不掷骰；视线被遮 → `status="attack_blocked"`；
         「完全」掩体 → `status="unsupported_target"`——三者都不结算
         （目标合法性归调用方，不猜测、不静默）；
      3. 否则依次调用 `build_pool` → `build_dodge_need_adjusted`
         （封顶在核心执行）→ `build_roll` → `build_attack_result` →
         `build_overload_chain`，结果进 `settlement`。

    返回 dict（键序冻结，可 JSON 落盘）：schema_version / kind /
    status / engine / source（来源信息）/ reasons / mapping（#448
    明细原样透传）/ inputs（适配输入）/ unmapped_features（未映射
    特征）/ settlement（结果摘要）/ rule_refs（所用规则 id，与
    `prism_core.CONTEXT_RULE_REFS` 同源）。
    """
    result = {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "kind": ADAPTER_KIND,
        "status": "ok",
        "engine": ENGINE_ID,
        "source": _source_info(context),
        "reasons": [],
        "mapping": {"mapped": [], "unsupported": [], "not_mapped": []},
        "inputs": None,
        "unmapped_features": [],
        "settlement": None,
        "rule_refs": dict(pc.CONTEXT_RULE_REFS),
    }

    # ① context 校验与映射：复用 #448，不重写（无效 / 过期不降级猜测）。
    if context is None:
        modifiers = 0
        result["inputs"] = {"dodge_need_modifiers": 0,
                            "attack_blocked": False}
    else:
        mapping = pc.map_context_for_build(context)
        result["mapping"] = {"mapped": list(mapping["mapped"]),
                             "unsupported": list(mapping["unsupported"]),
                             "not_mapped": list(mapping["not_mapped"])}
        result["unmapped_features"] = _unmapped_features(context) \
            if isinstance(context, dict) else []
        if not mapping["ok"]:
            result["status"] = "context_rejected"
            result["reasons"] = list(mapping["reasons"])
            return result
        inputs = mapping["inputs"]
        modifiers = int(inputs["dodge_need_modifiers"])
        result["inputs"] = {"dodge_need_modifiers": modifiers,
                            "attack_blocked": bool(inputs["attack_blocked"])}
        if inputs["attack_blocked"]:
            # 视线被遮（02C:121）：目标合法性事实，显式返回，不掷骰。
            result["status"] = "attack_blocked"
            return result
        if mapping["unsupported"]:
            # 「完全」掩体（02A:264）：无数值档，不猜测、不结算。
            result["status"] = "unsupported_target"
            return result

    # ② 结算编排：骰池构成 / 闪避需求（封顶在核心）/ 掷骰 / 命中结果
    #    / 超载连锁——全部调用既有核心函数，本模块零公式。
    pool = pc.build_pool(attribute, prof_bonus,
                         specialization=specialization, temp=temp_dice)
    need = pc.build_dodge_need_adjusted(target_type, modifiers=modifiers)
    roll = pc.build_roll(pool["total"], need, sides=sides, rng=rng)
    attack = pc.build_attack_result(roll["successes"], need)
    overload = pc.build_overload_chain(roll["successes"], need,
                                       used_this_scene=used_this_scene)
    result["settlement"] = {"pool": dict(pool), "dodge_need": need,
                            "roll": roll, "attack": attack,
                            "overload": overload}
    return result

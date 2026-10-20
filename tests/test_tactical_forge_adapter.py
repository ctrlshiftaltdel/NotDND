#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建引擎（骰池）六角格适配器回归测试（Issue #425，M13-B10）。

覆盖：

- 支持矩阵：高地 / 掩体三档 / 视线有构建规则依据（supported），
  「完全」掩体显式 unsupported，距离 / 高度差 / 地形 / 移动视图
  显式 not_mapped——支持与不支持的映射均有测试；
- context 校验入口：复用 #448 校验（ok / stale / invalid），附
  未映射特征声明；
- 适配结算：无 context 与旧构建结算 100% 兼容（显式 None == 不传 ==
  手工按核心公式编排）；高地 / 掩体 → 闪避需求修正（封顶归核心）；
  视线被遮 / 「完全」掩体不掷骰（目标合法性显式返回）；
- 算法隔离（02B 混用限制）：骰池战斗仍由构建引擎规则结算，没有
  d20 算法混入——适配输入无 advantage_layers / guard_bonus，骰池
  构成与骰点不受 context 影响（成功数只由骰池构成决定）；
- 未映射项不静默丢弃（terrain_tags / 特征引用 / 移动视图逐项声明；
  距离事实随 #448 not_mapped 透传）；stale context 结构化拒绝；
- 运行态与结果可 JSON 序列化。

context 一律经 #447 tactical_context.build_context 真实产物构造
（契约来源只有一个；fixture 与 tests/test_prism_core.py 同口径）。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_tactical_forge_adapter.py` 运行。
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import prism_core as pc
import tactical_battlefield as tb
import tactical_context as tctx
import tactical_forge_adapter as forge


class SeqRandom:
    """确定性随机源：按序吐出给定值（与 test_prism_core 同名助手同口径）。"""

    def __init__(self, *values):
        self._values = list(values)
        self._i = 0

    def randint(self, low, high):
        value = self._values[self._i % len(self._values)]
        self._i += 1
        assert low <= value <= high, (low, value, high)
        return value


# ── fixture：半径 2 的 metric 六角盘（与 test_prism_core 同口径）──────

_HEX = tb.space_rules()["scale"]["hex_size_cm"]


def _inject_sight():
    """数据尚未落 `sight`（#422 已知口径）→ 测试注入五档配置。"""
    rules = json.loads(json.dumps(tb.space_rules()))
    rules["sight"] = {
        "small":  {"eye_height_cm": 90,  "visible_height_cm": 70},
        "medium": {"eye_height_cm": 160, "visible_height_cm": 120},
        "large":  {"eye_height_cm": 220, "visible_height_cm": 180},
        "huge":   {"eye_height_cm": 320, "visible_height_cm": 280},
        "boss":   {"eye_height_cm": 450, "visible_height_cm": 400},
    }
    return rules


_SIGHT_RULES = _inject_sight()


def _disk(radius):
    coords = []
    for q in range(-radius, radius + 1):
        low = max(-radius, -q - radius)
        high = min(radius, -q + radius)
        for r in range(low, high + 1):
            coords.append((q, r))
    return sorted(coords)


def _cell(cell_id, q, r, **overrides):
    cell = {
        "cell_id": cell_id,
        "q": q,
        "r": r,
        "surface_id": "ground",
        "terrain_id": "floor",
        "terrain_tags": ["open"],
        "movement_cost": _HEX,
        "blocks_movement": False,
        "blocks_los": False,
        "hazard_refs": [],
        "feature_refs": [],
        "spawnable": True,
        "elevation_cm": 0,
    }
    cell.update(overrides)
    return cell


def _battlefield(overrides=None):
    """半径 2 的 metric 六角盘；返回 (战场 doc, {(q, r): cell_id})。"""
    cells = []
    ids = {}
    for order, (q, r) in enumerate(_disk(2)):
        cell_id = "g%02d" % order
        ids[(q, r)] = cell_id
        cell = _cell(cell_id, q, r)
        if overrides and (q, r) in overrides:
            cell.update(overrides[(q, r)])
        cells.append(cell)
    return {
        "schema_version": 1,
        "kind": "tactical.battlefield",
        "source": "docs/system/02c-hex-battlefield.md:29",
        "battlefield_id": "bf-b10-01",
        "world_id": "threshold",
        "site_id": "site-b10-01",
        "atlas_ref": "threshold/region-01",
        "seed": 425,
        "generator_version": "1.0.0",
        "geometry": {"type": "hex_axial"},
        "scale": {"mode": "metric", "hex_size_cm": _HEX},
        "cells": cells,
        "vertical_links": [],
    }, ids


def _state(ids, revision=7):
    return {
        "schema_version": 1,
        "kind": "tactical.battle_state",
        "battle_id": "battle-b10-01",
        "battlefield_id": "bf-b10-01",
        "round": 1,
        "turn": 1,
        "state_revision": revision,
        "participants": [
            {"unit_id": "hero", "cell_id": ids[(0, 0)]},
            {"unit_id": "orc", "cell_id": ids[(2, 0)]},
        ],
        "objects": [],
    }


def _ctx_build(bf, ids, state, **kwargs):
    """经 #447 build_context 产出 context——契约来源只有一个。"""
    kwargs.setdefault("actor_id", "hero")
    kwargs.setdefault("target_id", "orc")
    kwargs.setdefault("actor_body_size", "medium")
    kwargs.setdefault("target_body_size", "medium")
    kwargs.setdefault("rules", _SIGHT_RULES)
    return tctx.build_context(bf, state, **kwargs)


def _settle(context=None, **kwargs):
    """适配结算的默认口径：骰池 3+1 枚 d10、标准敌人。"""
    kwargs.setdefault("attribute", 3)
    kwargs.setdefault("prof_bonus", 2)
    kwargs.setdefault("target_type", "standard")
    return forge.resolve_build_attack(context, **kwargs)


# ── 检查项 ────────────────────────────────────────────────────────────

def check_support_matrix():
    matrix = forge.forge_support_matrix()
    assert isinstance(matrix, list) and matrix, matrix

    # ① 支持项：高地 / 掩体三档 / 视线（构建规则有明确依据）。
    supported = {(item["fact"], item.get("value")): item
                 for item in matrix if item["status"] == "supported"}
    assert ("high_ground", True) in supported, supported
    assert ("cover_grade", "light/medium/heavy") in supported, supported
    assert ("line_of_sight", False) in supported, supported

    # ② 显式不支持：「完全」掩体没有需求成功数档（02A:264）。
    full = [item for item in matrix
            if item["fact"] == "cover_grade" and item.get("value") == "full"]
    assert len(full) == 1 and full[0]["status"] == "unsupported", full
    assert full[0]["rule_ref"] == pc.CONTEXT_RULE_REFS["cover_full"], full

    # ③ 显式不映射：距离 / 高度差 / 地形 / 特征 / 移动视图。
    not_mapped = {item["fact"] for item in matrix
                  if item["status"] == "not_mapped"}
    for facts in ("hex_distance/metric_distance/abstract_distance/"
                  "signed_height_delta", "terrain_tags",
                  "active_feature_refs",
                  "reachable/path/path_cost/movement_blocked"):
        assert facts in not_mapped, not_mapped

    # ④ 规则 id 与核心同源，本模块不新造锚点；整体可 JSON 序列化。
    for item in matrix:
        assert item["rule_ref"] in pc.CONTEXT_RULE_REFS.values(), item
    json.dumps(matrix)


def check_context_entry():
    bf, ids = _battlefield()
    state = _state(ids)
    context = _ctx_build(bf, ids, state)

    # ① 来源合法：ok，且附适配层未映射特征声明（terrain_tags 非空）。
    check = forge.check_context(context)
    assert check["ok"] is True and check["reasons"] == [], check
    assert any(item["fact"] == "terrain_tags"
               for item in check["unmapped_features"]), check

    # ② stale：复用 #448 校验拒绝，来源 reasons 透传。
    stale = _ctx_build(bf, ids, state,
                       expected_revision=state["state_revision"] - 1)
    check = forge.check_context(stale)
    assert check["ok"] is False and check["facts"] is None, check
    assert check["reasons"][0]["code"] == "stale_context", check["reasons"]

    # ③ 非 dict / 坏 kind：结构化拒绝。
    for bad in (None, "context", 42, {}, {"kind": "tactical.context"}):
        assert forge.check_context(bad)["ok"] is False, bad
    json.dumps(check)


def check_no_context_compat():
    # ① 显式 None == 不传：旧构建战斗（无六角格）行为完全一致。
    out_none = _settle(None, rng=SeqRandom(6, 3, 9, 5))
    out_omit = _settle(rng=SeqRandom(6, 3, 9, 5))
    assert out_none == out_omit, "显式 None 必须与不传完全一致"
    assert out_none["status"] == "ok", out_none
    assert out_none["inputs"] == {"dodge_need_modifiers": 0,
                                  "attack_blocked": False}, out_none["inputs"]
    assert out_none["mapping"] == {"mapped": [], "unsupported": [],
                                   "not_mapped": []}, out_none["mapping"]
    assert out_none["unmapped_features"] == [], out_none["unmapped_features"]
    assert out_none["source"]["context_kind"] is None, out_none["source"]

    # ② 结算与按核心公式手工编排逐项一致（适配器零公式、纯编排）。
    pool = pc.build_pool(3, 2)
    need = pc.build_dodge_need_adjusted("standard", modifiers=0)
    roll = pc.build_roll(pool["total"], need, rng=SeqRandom(6, 3, 9, 5))
    attack = pc.build_attack_result(roll["successes"], need)
    manual = {"pool": pool, "dodge_need": need, "roll": roll,
              "attack": attack,
              "overload": pc.build_overload_chain(roll["successes"], need)}
    assert out_none["settlement"] == manual, \
        (out_none["settlement"], manual)

    # ③ 结算摘要保留既有机制：骰池构成 / 结果梯度 / 动量 / 超载连锁。
    settlement = out_none["settlement"]
    assert settlement["pool"] == {"attribute": 3, "proficiency": 1,
                                  "specialization": 0, "temp": 0,
                                  "total": 4}, settlement["pool"]
    assert settlement["roll"]["outcome"] in pc.BUILD_OUTCOME_MOMENTUM
    assert settlement["roll"]["momentum"] == \
        pc.BUILD_OUTCOME_MOMENTUM[settlement["roll"]["outcome"]] + \
        (1 if settlement["roll"]["all_fail"] else 0)
    assert settlement["attack"]["result"] in dict(
        (row["id"], row["name"]) for row in pc.BUILD_HIT_RESULTS)
    assert set(settlement["overload"]) == {"triggered", "available",
                                           "options", "limit", "reason"}
    # 骰阶由调用方经 build_rank_die 传入（公式在核心）。
    out_rank = _settle(sides=pc.build_rank_die("III"), rng=SeqRandom(6, 3, 9, 5))
    assert out_rank["settlement"]["roll"]["sides"] == 10, out_rank["settlement"]
    json.dumps(out_none)


def check_high_ground_and_cover_mapping():
    # ① 高地 + 轻掩体：需求成功数修正每项 +1（02B:261），封顶归核心。
    bf, ids = _battlefield({(2, 0): {"elevation_cm": -150,
                                     "cover_grade": "light"}})
    context = _ctx_build(bf, ids, _state(ids))
    out = _settle(context, rng=SeqRandom(6, 3, 9, 5))
    assert out["status"] == "ok", out
    assert out["inputs"] == {"dodge_need_modifiers": 2,
                             "attack_blocked": False}, out["inputs"]
    assert out["settlement"]["dodge_need"] == \
        pc.build_dodge_need_adjusted("standard", modifiers=2), out["settlement"]
    assert out["settlement"]["dodge_need"] == 5, out["settlement"]
    assert all(item["rule_ref"] == pc.CONTEXT_RULE_REFS["build_dodge_modifiers"]
               for item in out["mapping"]["mapped"]), out["mapping"]["mapped"]
    assert out["source"]["context_kind"] == tctx.CONTEXT_KIND, out["source"]
    assert out["source"]["state_revision"] == 7, out["source"]

    # ② 仅掩体：+1（standard 3 → 4）；骰池构成与骰点不受 context 影响。
    bf_c, ids_c = _battlefield({(2, 0): {"cover_grade": "light"}})
    out_c = _settle(_ctx_build(bf_c, ids_c, _state(ids_c)),
                    rng=SeqRandom(6, 3, 9, 5))
    assert out_c["inputs"]["dodge_need_modifiers"] == 1, out_c["inputs"]
    assert out_c["settlement"]["dodge_need"] == 4, out_c["settlement"]
    plain = _settle(None, rng=SeqRandom(6, 3, 9, 5))
    assert out_c["settlement"]["roll"]["dice"] == \
        plain["settlement"]["roll"]["dice"], "骰点只由骰池构成与 rng 决定"
    assert out_c["settlement"]["pool"] == plain["settlement"]["pool"]

    # ③ 高地-only：+1 且 attack_blocked=False。
    bf_h, ids_h = _battlefield({(2, 0): {"elevation_cm": -150}})
    out_h = _settle(_ctx_build(bf_h, ids_h, _state(ids_h)),
                    rng=SeqRandom(6, 3, 9, 5))
    assert out_h["inputs"] == {"dodge_need_modifiers": 1,
                               "attack_blocked": False}, out_h["inputs"]
    json.dumps(out)


def check_blocked_and_unsupported_targets():
    # ① 视线被遮（02C:121）：不掷骰，目标合法性事实显式返回。
    bf_wall, ids_wall = _battlefield({(1, 0): {"blocks_los": True}})
    context = _ctx_build(bf_wall, ids_wall, _state(ids_wall))
    out = _settle(context, rng=SeqRandom(6, 3, 9, 5))
    assert out["status"] == "attack_blocked", out
    assert out["settlement"] is None, out["settlement"]
    assert out["inputs"]["attack_blocked"] is True, out["inputs"]
    assert any(item["fact"] == "line_of_sight"
               for item in out["mapping"]["mapped"]), out["mapping"]

    # ② 「完全」掩体（02A:264）：无数值档 → unsupported，不猜数值。
    bf_full, ids_full = _battlefield({(2, 0): {"cover_grade": "full"}})
    out = _settle(_ctx_build(bf_full, ids_full, _state(ids_full)),
                  rng=SeqRandom(6, 3, 9, 5))
    assert out["status"] == "unsupported_target", out
    assert out["settlement"] is None, out["settlement"]
    assert out["mapping"]["unsupported"][0]["fact"] == "cover_grade", out
    assert out["mapping"]["unsupported"][0]["rule_ref"] == \
        pc.CONTEXT_RULE_REFS["cover_full"], out["mapping"]
    json.dumps(out)


def check_unmapped_features_surfaced():
    # ① 带移动视图 + 地形 + 特征的 context：非空未消费字段逐项声明。
    bf, ids = _battlefield({
        (0, 0): {"terrain_tags": ["open", "rough"], "feature_refs": ["f1"]},
        (2, 0): {"feature_refs": ["f2"]},
    })
    context = _ctx_build(bf, ids, _state(ids),
                         movement_budget=_HEX * 6)
    out = _settle(context, rng=SeqRandom(6, 3, 9, 5))
    assert out["status"] == "ok", out
    declared = {item["fact"] for item in out["unmapped_features"]}
    for fact in forge.FORGE_UNCONSUMED_FIELDS:
        if context.get(fact):
            assert fact in declared, (fact, out["unmapped_features"])
    assert "terrain_tags" in declared and "reachable" in declared, declared
    assert all(item["value"] == context.get(item["fact"])
               for item in out["unmapped_features"]), out["unmapped_features"]

    # ② 距离 / 高度差事实经 #448 not_mapped 透传，不静默丢弃。
    not_mapped = {item["fact"] for item in out["mapping"]["not_mapped"]}
    assert {"hex_distance", "metric_distance",
            "signed_height_delta"} <= not_mapped, not_mapped

    # ③ 空值字段不产生噪音声明。
    bf_p, ids_p = _battlefield()
    out_plain = _settle(_ctx_build(bf_p, ids_p, _state(ids_p)),
                        rng=SeqRandom(6, 3, 9, 5))
    assert all(item["fact"] not in ("reachable", "path", "path_cost",
                                    "movement_blocked")
               for item in out_plain["unmapped_features"]), \
        out_plain["unmapped_features"]
    json.dumps(out)


def check_stale_context_rejected():
    bf, ids = _battlefield({(2, 0): {"cover_grade": "light"}})
    state = _state(ids)
    stale = _ctx_build(bf, ids, state,
                       expected_revision=state["state_revision"] - 1)
    assert stale["status"] == "stale_context", stale["status"]
    out = _settle(stale, rng=SeqRandom(6, 3, 9, 5))
    assert out["status"] == "context_rejected", out
    assert out["settlement"] is None, out["settlement"]
    assert out["inputs"] is None, out["inputs"]
    assert out["reasons"][0]["code"] == "stale_context", out["reasons"]
    assert out["reasons"][0]["source_reasons"] == stale["reasons"], \
        out["reasons"]
    json.dumps(out)


def check_no_d20_mixing():
    bf, ids = _battlefield({(2, 0): {"elevation_cm": -150,
                                     "cover_grade": "medium"}})
    context = _ctx_build(bf, ids, _state(ids))
    out = _settle(context, rng=SeqRandom(6, 3, 9, 5))

    # ① 适配输入里没有 d20 的助势骰 / 防护加值；d20 映射里没有骰池修正。
    assert "advantage_layers" not in out["inputs"], out["inputs"]
    assert "guard_bonus" not in out["inputs"], out["inputs"]
    assert "dodge_need_modifiers" not in pc.map_context_for_d20(context)["inputs"]
    assert all("助势骰" not in item["effect"]
               and "guard_bonus" not in item["effect"]
               and "AP" not in item["effect"]
               for item in out["mapping"]["mapped"]), out["mapping"]["mapped"]

    # ② 骰池构成不受 context 影响；同一 rng 下骰点逐枚相同——成功数
    #    只由骰池构成决定，空间事实只移动防御侧需求成功数。
    plain = _settle(None, rng=SeqRandom(6, 3, 9, 5))
    assert out["settlement"]["pool"] == plain["settlement"]["pool"]
    assert out["settlement"]["roll"]["dice"] == \
        plain["settlement"]["roll"]["dice"]
    assert out["settlement"]["roll"]["successes"] == \
        plain["settlement"]["roll"]["successes"]
    assert out["settlement"]["dodge_need"] == \
        plain["settlement"]["dodge_need"] + 2, out["settlement"]["dodge_need"]

    # ③ 命中结果走构建引擎 5 档（build_attack_result），不是 d20 命中
    #    六档；伤害 / 状态结算不被复制（settlement 只有摘要，无管线）。
    assert out["settlement"]["attack"]["result"] in dict(
        (row["id"], row["name"]) for row in pc.BUILD_HIT_RESULTS)
    assert "grade" not in out["settlement"], out["settlement"]
    assert "damage" not in out["settlement"], out["settlement"]
    json.dumps(out)


def check_result_serializable():
    variants = []
    bf, ids = _battlefield({(2, 0): {"elevation_cm": -150,
                                     "cover_grade": "light"}})
    variants.append(_settle(_ctx_build(bf, ids, _state(ids)),
                            rng=SeqRandom(6, 3, 9, 5)))
    variants.append(_settle(None, rng=SeqRandom(6, 3, 9, 5)))
    bf_wall, ids_wall = _battlefield({(1, 0): {"blocks_los": True}})
    variants.append(_settle(_ctx_build(bf_wall, ids_wall, _state(ids_wall)),
                            rng=SeqRandom(6, 3, 9, 5)))
    stale = _ctx_build(bf, ids, _state(ids), expected_revision=6)
    variants.append(_settle(stale, rng=SeqRandom(6, 3, 9, 5)))
    for out in variants:
        data = json.loads(json.dumps(out))
        assert data == out, "往返序列化须逐值一致"
        assert out["kind"] == forge.ADAPTER_KIND, out["kind"]
        assert out["status"] in forge.FORGE_RESULT_STATUSES, out["status"]


def main():
    checks = (
        check_support_matrix,
        check_context_entry,
        check_no_context_compat,
        check_high_ground_and_cover_mapping,
        check_blocked_and_unsupported_targets,
        check_unmapped_features_surfaced,
        check_stale_context_rejected,
        check_no_d20_mixing,
        check_result_serializable,
    )
    failures = 0
    for check in checks:
        try:
            check()
        except AssertionError as error:
            print("  断言失败 [%s]: %s" % (check.__name__, error))
            failures += 1
        except Exception as error:  # 意外异常同样计为失败
            print("  异常 [%s]: %r" % (check.__name__, error))
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败"
              % (len(checks) - failures, len(checks), failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

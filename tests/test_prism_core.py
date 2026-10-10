#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prism_core 数值落地回归测试（Issue #53 M2a + #59 M2b + #72 M2c + #122 M2d）。

M2a 覆盖：白名单拒绝、助势骰层数与取消、五档分档边界、灾难后果表、
资源夹取与专注过用、伤害标签修正、状态叠加与【疲惫】、张力曲线触发、
属性修正表与熟练量表、判定记法构造、perform_action 集成。

M2b 覆盖：先攻公式、攻击六档边界、擦过/暴击/重击数值、破韧与首领
阶段、敌体模板与词缀、遭遇预算与组成限制、派生值重算、状态叠层、
濒危三成/三败、经验升级与成长点、A 类 +4 上限、战斗集成。

M2c 覆盖：构建引擎骰池四部分组成、成功阈值与单骰成功率表、需求
成功数 8 档、五档结果边界与动量增益、全骰皆负与最大值爆发两个特例、
骰阶 5 阶与期望成功数速查表、动量池（入账/花费/清零/上下限）、
应力上限与惩罚段、崩溃事件 4 步、降低应力 4 手段。

M2d 覆盖：攻击流程 5 步与闪避需求 5 档（含掩体修正上限）、命中
结果 5 档边界、溢出购买 4 项与 D≥+2 门槛、超载连锁触发（两倍且
盈余 ≥3）与每场景 1 次、符纹插槽（上限 3 / 5 源 / 3 源 / 结构模板）、
构建点（每级 2 BP / 花费 12 项 / 起始 6 项 / 3 路线）、符纹库 28 枚
六类与等级限制、安装上限（同名 / 同类 / 等级）、双引擎互转六表 +
迁移五步 + 混用限制 3 条、d20 单骰成功率推导。

ATLAS I5（Issue #67）覆盖：战术投影接线——attach_tactical_map 的
坏帧 / 坏钉扎校验、距离档（两格图距离 = 第 3.3 节的「近」）、高地
（z 更高且相邻，并折成 +1 枚助势骰）、abstract 帧不把米换成跨区、
没接地图时行为与 M2b 默认完全一致、投影块不进会话快照。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_prism_core.py` 运行。
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import prism_core as pc
import atlas as al          # I5：测试需要手工造帧并投出真实战术帧
import atlas_gen as ag


class SeqRandom:
    """确定性随机源：按预定序列返回 randint 结果，越界截断到 [low, high]。"""

    def __init__(self, *values):
        self.values = [int(v) for v in values]
        self.calls = 0

    def randint(self, low, high):
        value = self.values[self.calls % len(self.values)]
        self.calls += 1
        return max(low, min(high, value))


def _unit(**kwargs):
    return pc.new_unit("u1", "测试角色", **kwargs)


# ── 一、掷骰器：白名单 ──────────────────────────────────────────────────

def check_roll_whitelist():
    rng = SeqRandom(5, 2, 3)
    result = pc.roll("1d20", rng=rng)
    assert result["natural"] == 5 and result["total"] == 5, result
    result = pc.roll("2d6+1", rng=rng)
    assert result["dice"] == [2, 3] and result["total"] == 6, result

    for bad in ("1d20+1d4", "2*6", "d", "1d20*2", "0d6", "1d0",
                "41d6", "1d1001", "1d6+2000", "", "  "):
        try:
            pc.roll(bad)
        except ValueError:
            continue
        raise AssertionError("白名单应拒绝记法：%r" % bad)


# ── 二、掷骰器：助势骰 ──────────────────────────────────────────────────

def check_roll_edge_dice():
    # 优势与劣势互相抵消：2 − 1 = 净 1 层 → 恰好掷 1 枚 d6。
    result = pc.roll("1d20", rng=SeqRandom(10, 4), advantage=2, disadvantage=1)
    assert result["edge"]["net"] == 1 and result["edge"]["dice"] == [4], result
    assert result["total"] == 14, result

    # 净 0 层：不掷助势骰。
    result = pc.roll("1d20", rng=SeqRandom(10), advantage=1, disadvantage=1)
    assert result["edge"]["net"] == 0
    assert result["edge"]["dice"] == [] and result["edge"]["sum"] == 0
    assert result["total"] == 10, result

    # 超出 3 层：3 枚 d6 + 每多一层 +2 固定加值（4 层 → 3d6 + 2）。
    result = pc.roll("1d20", rng=SeqRandom(5, 2, 3, 4), advantage=4)
    assert result["edge"]["dice"] == [2, 3, 4] and result["edge"]["flat"] == 2
    assert result["edge"]["sum"] == 11 and result["total"] == 16, result

    # 劣势：掷 d6 并减去。
    result = pc.roll("1d20", rng=SeqRandom(10, 3, 4), disadvantage=2)
    assert result["edge"]["net"] == -2 and result["edge"]["sum"] == -7
    assert result["total"] == 3, result

    # 记法白名单不受助势骰参数影响。
    try:
        pc.roll("1d20+1d4", advantage=1)
    except ValueError:
        pass
    else:
        raise AssertionError("助势骰不得绕过记法白名单")


# ── 三、五档结果 ────────────────────────────────────────────────────────

def check_judge_check_bands():
    cases = (
        (16, 10, "triumph"),    # R = +6
        (15, 10, "success"),    # R = +5
        (10, 10, "success"),    # R = 0（平手归属行动方）
        (9, 10, "narrow"),      # R = −1
        (8, 10, "narrow"),      # R = −2
        (7, 10, "failure"),     # R = −3
        (1, 10, "failure"),     # R = −9
        (0, 10, "catastrophe"), # R = −10
        (0, 11, "catastrophe"), # R = −11
    )
    for total, df, expected in cases:
        got = pc.judge_check(total, df)
        assert got == expected, "judge_check(%d, %d) = %s，期望 %s" % (
            total, df, got, expected)
    # 自然 1 → 灾难，即使数值上很高。
    assert pc.judge_check(20, 10, natural=1) == "catastrophe"
    assert pc.judge_check(20, 10, natural=20) == "triumph"
    assert pc.judge_check(0, 20, auto_pass=True) == "success"


# ── 四、属性修正表与熟练量表 ────────────────────────────────────────────

def check_attribute_and_proficiency_tables():
    expect = {1: -2, 2: -1, 3: -1, 4: 0, 5: 0,
              6: 1, 7: 1, 8: 2, 9: 2, 10: 3}
    for value, modifier in expect.items():
        assert pc.attribute_modifier(value) == modifier, value
    for bad in (0, 11, -1):
        try:
            pc.attribute_modifier(bad)
        except ValueError:
            continue
        raise AssertionError("属性值 %d 应越界报错" % bad)

    expect_prof = {1: 2, 4: 2, 5: 3, 8: 3, 9: 4, 12: 4}
    for level, bonus in expect_prof.items():
        assert pc.proficiency_bonus(level) == bonus, level
    for bad in (0, 13):
        try:
            pc.proficiency_bonus(bad)
        except ValueError:
            continue
        raise AssertionError("等级 %d 应越界报错" % bad)


# ── 五、资源池：上下限、见底与过度使用 ──────────────────────────────────

def check_resource_clamps():
    session = pc.RuleSession("s1")
    unit = _unit()

    # 负担上限 6（01 第十二节：负担从 0 到 6 累加）。
    out = pc.change_resource(session, unit, "strain", 10)
    assert out["left"] == pc.STRAIN_MAX == 6, out
    # 决意上限 5（01 第十二节：起始 3 点，上限 5 点）。
    out = pc.change_resource(session, unit, "resolve", 9)
    assert out["left"] == pc.RESOLVE_MAX == 5, out
    # 任何池不见负。
    out = pc.change_resource(session, unit, "strain", -20)
    assert out["left"] == 0 and out["out"], out
    # 调用方提供的上限仍生效（专注上限依赖职途，由外部给出）。
    unit["max_resources"] = {"focus": 7}
    assert pc.change_resource(session, unit, "focus", 9)["left"] == 7

    # 专注过度使用：余量 2 花费 5 → 见底、疲惫、负担 +1。
    unit2 = _unit()
    unit2["resources"]["focus"] = 2
    out = pc.change_resource(session, unit2, "focus", -5)
    assert out["left"] == 0 and out["overused"], out
    assert "疲惫" in unit2["conditions"], unit2["conditions"]
    assert unit2["resources"]["strain"] == 1, unit2["resources"]
    # 余量充足的花费不触发过用。
    unit3 = _unit()
    unit3["resources"]["focus"] = 3
    out = pc.change_resource(session, unit3, "focus", -3)
    assert not out["overused"] and out["left"] == 0, out
    assert "疲惫" not in unit3["conditions"]

    try:
        pc.change_resource(session, unit, "mana", 1)
    except ValueError:
        pass
    else:
        raise AssertionError("未知资源池应报错")


# ── 六、伤害标签 ────────────────────────────────────────────────────────

def check_damage_tags():
    session = pc.RuleSession("s2")

    # 抗性：减半向下取整（01 第十一节）。
    unit = _unit()
    unit["max_vitality"] = 10
    unit["vitality"] = 10
    unit["damage_relations"] = {"resistance": ["火焰"]}
    out = pc.deal_damage(session, unit, 5, tag="火焰")
    assert out["relation"] == "resistance" and out["lost"] == 2, out
    # 非声明标签不受影响。
    out = pc.deal_damage(session, unit, 4, tag="电击")
    assert out["relation"] == "" and out["lost"] == 4 and out["modified"] is False

    # 弱点：+50%（向下取整）且额外 1 层【失衡】。
    unit = _unit()
    unit["max_vitality"] = 20
    unit["vitality"] = 20
    unit["damage_relations"] = {"weakness": ["火焰"]}
    out = pc.deal_damage(session, unit, 5, tag="火焰")
    assert out["relation"] == "weakness" and out["lost"] == 7, out
    assert "失衡" in unit["conditions"], unit["conditions"]

    # 免疫：伤害无效。
    unit = _unit()
    unit["max_vitality"] = 10
    unit["vitality"] = 10
    unit["damage_relations"] = {"immunity": ["精神"]}
    out = pc.deal_damage(session, unit, 10, tag="精神")
    assert out["relation"] == "immunity" and out["lost"] == 0, out

    # 吸收：反而恢复等量活力。
    unit = _unit()
    unit["max_vitality"] = 10
    unit["vitality"] = 4
    unit["damage_relations"] = {"absorb": ["力场"]}
    out = pc.deal_damage(session, unit, 6, tag="力场")
    assert out["relation"] == "absorb" and out["healed"] == 6, out
    assert unit["vitality"] == 10 and not unit["downed"]

    # 濒危受击失败计数：普通 +1，暴击 +2（01 第十一节 damage_escalation）。
    unit = _unit()
    unit["max_vitality"] = 10
    unit["vitality"] = 3
    out = pc.deal_damage(session, unit, 5)
    assert unit["downed"] and unit.get("downed_fails", 0) == 0, (out, unit)
    pc.deal_damage(session, unit, 1)
    assert unit["downed_fails"] == 1
    pc.deal_damage(session, unit, 1, crit=True)
    assert unit["downed_fails"] == 3
    # 吸收不计受击。
    unit["damage_relations"] = {"absorb": ["火焰"]}
    pc.deal_damage(session, unit, 1, tag="火焰")
    assert unit["downed_fails"] == 3


# ── 七、状态叠加 ────────────────────────────────────────────────────────

def check_conditions():
    session = pc.RuleSession("s3")
    unit = _unit()

    assert pc.add_condition(session, unit, "中毒") is True
    # 非叠层状态不重复：同名重复施加改为延长持续时间（返回 False）。
    assert pc.add_condition(session, unit, "中毒") is False
    assert unit["conditions"] == ["中毒"]

    try:
        pc.add_condition(session, unit, "不存在的状态")
    except ValueError:
        pass
    else:
        raise AssertionError("白名单外的状态应报错")

    # 两个减益：不触发疲惫。
    unit2 = _unit()
    pc.add_condition(session, unit2, "失衡")
    pc.add_condition(session, unit2, "中毒")
    assert "疲惫" not in unit2["conditions"]
    # 第三个减益：自动获得【疲惫】（01 第五节叠加规则 3）。
    pc.add_condition(session, unit2, "恐惧")
    assert "疲惫" in unit2["conditions"], unit2["conditions"]
    # 疲惫本身不再重复。
    assert unit2["conditions"].count("疲惫") == 1
    # 增益不计数。
    unit3 = _unit()
    for name in ("加速", "护持", "隐匿"):
        pc.add_condition(session, unit3, name)
    assert "疲惫" not in unit3["conditions"]
    # 世界模组登记扩展状态。
    try:
        pc.add_condition(session, unit3, "共鸣灼伤")
    except ValueError:
        pass
    else:
        raise AssertionError("未登记的扩展状态应报错")
    pc.register_condition("共鸣灼伤", debuff=True)
    assert pc.add_condition(session, unit3, "共鸣灼伤") is True


# ── 八、失败代价与灾难后果表 ────────────────────────────────────────────

def check_failure_cost():
    # 源文档未写按 DF 折算的常规代价曲线：全零，不臆造。
    cost = pc.failure_cost(12)
    assert cost["focus"] == 0 and cost["vitality"] == 0
    assert cost["strain"] == 0 and cost["conditions"] == []
    assert "catastrophe_row" not in cost

    # 灾难后果表第 3 行：1d6 活力（非信息型）。
    cost = pc.failure_cost(12, catastrophe=True, rng=SeqRandom(3, 4))
    assert cost["catastrophe_row"] == 3
    assert cost["vitality"] == 4 and cost["strain"] == 0, cost
    # 信息型行动不用活力买情报。
    cost = pc.failure_cost(12, info_only=True, catastrophe=True,
                           rng=SeqRandom(3, 4))
    assert cost["vitality"] == 0, cost
    # 第 10 行：负担 +1。
    cost = pc.failure_cost(12, catastrophe=True, rng=SeqRandom(10))
    assert cost["strain"] == 1 and cost["vitality"] == 0, cost
    # 叙事行：只回文本。
    cost = pc.failure_cost(12, catastrophe=True, rng=SeqRandom(2))
    assert cost["vitality"] == 0 and cost["strain"] == 0
    assert cost["catastrophe_text"] == pc.CATASTROPHE_TABLE[1]
    assert len(pc.CATASTROPHE_TABLE) == 20


def check_apply_failure_settlement():
    session = pc.RuleSession("s4")
    unit = _unit()
    unit["max_vitality"] = 20
    unit["vitality"] = 20

    # 常规失败：无数值曲线 → 状态不变。
    out = pc.apply_failure(session, unit, 12)
    assert unit["vitality"] == 20 and out["damage"] is None
    # 灾难第 10 行：负担 +1 走 change_resource。
    out = pc.apply_failure(session, unit, 12, catastrophe=True,
                           rng=SeqRandom(10))
    assert out["cost"]["catastrophe_row"] == 10
    assert unit["resources"]["strain"] == 1, unit["resources"]
    # 灾难第 3 行：活力伤害走 deal_damage。
    out = pc.apply_failure(session, unit, 12, catastrophe=True,
                           rng=SeqRandom(3, 6))
    assert out["damage"]["lost"] == 6, out
    assert unit["vitality"] == 14


# ── 九、压力计与张力曲线 ────────────────────────────────────────────────

def check_pressure_tension():
    assert pc.tension_band(0)["id"] == "relaxed"
    assert pc.tension_band(4)["id"] == "mild"
    assert pc.tension_band(5)["name"] == "紧绷"
    assert pc.tension_band(9)["id"] == "extreme"
    assert pc.tension_band(99)["id"] == "extreme"  # 夹取到 10

    session = pc.RuleSession("s5")
    # 0 → 3：升入「温和」，触发一次。
    out = pc.tick_pressure(session, 3)
    assert out["fired"] and out["event"]["band"] == "mild", out
    # 3 → 4：同档不触发。
    out = pc.tick_pressure(session, 1)
    assert not out["fired"], out
    # 一次跨多档只算一次（幂等）。
    out = pc.tick_pressure(session, 4)   # 4 → 8，跨两档
    assert out["fired"] and out["event"]["band"] == "high", out
    # 8 → 10：升入「顶点」，触发一次。
    out = pc.tick_pressure(session, 10)
    assert out["pressure"] == pc.PRESSURE_MAX == 10
    assert out["fired"] and out["event"]["band"] == "extreme", out
    # 已在顶点：不再变化、不再触发。
    out = pc.tick_pressure(session, 5)
    assert out["pressure"] == 10 and not out["fired"], out

    # 风险池判定（03 第一节）：危险区 d4 掷 4 触发；安全区 d8 掷 7 不触发。
    assert pc.risk_roll("dangerous", rng=SeqRandom(4))["triggered"] is True
    assert pc.risk_roll("safe", rng=SeqRandom(7))["triggered"] is False
    assert pc.risk_roll("standard", rng=SeqRandom(6))["triggered"] is True
    # 「5–6 触发」变体：face 覆盖默认触发面（03:53）。
    assert pc.risk_roll("standard", rng=SeqRandom(5), face=5)["triggered"] is True
    assert pc.risk_roll("standard", rng=SeqRandom(4), face=5)["triggered"] is False
    try:
        pc.risk_roll("void")
    except ValueError:
        pass
    else:
        raise AssertionError("未知区域应报错")


# ── 十、判定记法构造 ────────────────────────────────────────────────────

def check_check_notation():
    # 显式记法优先。
    assert pc._check_notation({"notation": "2d6"}, None) == "2d6"
    # 无修正 → 默认 1d20。
    assert pc._check_notation({}, _unit()) == "1d20"
    # 负担与疲惫**不折入记法**（统一走劣势层口径，主管预审裁定）。
    unit = _unit()
    unit["resources"]["strain"] = 6
    unit["conditions"] = ["疲惫"]
    assert pc._check_notation({}, unit) == "1d20"
    # 属性 8 → +2；1 级熟练 +2 → 合计 +4；专精再 +1。
    unit = _unit(attributes={"MGT": 8})
    action = {"attribute": "MGT", "proficient": True}
    assert pc._check_notation(action, unit) == "1d20+4"
    action["specialty"] = True
    assert pc._check_notation(action, unit) == "1d20+5"
    # 未知属性报错。
    try:
        pc._check_notation({"attribute": "XYZ"}, unit)
    except ValueError:
        pass
    else:
        raise AssertionError("未知属性应报错")

    # 劣势层（统一口径）：失衡 / 中毒 / 恐惧 / 疲惫 各 −1 层。
    unit = _unit()
    unit["conditions"] = ["失衡", "中毒", "恐惧", "流血"]
    assert pc._unit_disadvantage(unit) == 3
    # 负担档位：2–3 → 1 层；4–5 → 2 层；6 → 3 层。
    unit = _unit()
    unit["resources"]["strain"] = 2
    assert pc._unit_disadvantage(unit) == 1
    unit["resources"]["strain"] = 5
    assert pc._unit_disadvantage(unit) == 2
    unit["resources"]["strain"] = 6
    assert pc._unit_disadvantage(unit) == 3
    # 疲惫单独 −1 层；与负担合并后封顶 3 层（02A:83）。
    unit = _unit()
    unit["conditions"] = ["疲惫"]
    assert pc._unit_disadvantage(unit) == 1
    unit["resources"]["strain"] = 6
    assert pc._unit_disadvantage(unit) == 3
    # 增益不计层。
    unit = _unit()
    unit["conditions"] = ["加速", "护持"]
    assert pc._unit_disadvantage(unit) == 0


# ── 十一、perform_action 集成 ───────────────────────────────────────────

def _scene_session(*action_extra):
    session = pc.RuleSession("s6")
    action = {"id": "a1", "kind": "check", "label": "撬锁", "df": 12,
              "max_fail": 2}
    for extra in action_extra:
        action.update(extra)
    session.scene = {"id": "hall", "actions": [action]}
    session.party = [_unit()]
    return session, action


def check_perform_action_integration():
    # 险成：R = −1，达成（passed），不结算失败代价。
    session, _ = _scene_session()
    out = pc.perform_action(session, "a1", rng=SeqRandom(11))
    assert out["outcome"] == "narrow" and out["passed"] is True, out
    assert session.action_fails.get("a1", 0) == 0

    # 挫败：R = −4，失败代价计入次数并推压力（同档内不触发事件）。
    session, _ = _scene_session()
    out = pc.perform_action(session, "a1", rng=SeqRandom(8))
    assert out["outcome"] == "failure" and out["passed"] is False, out
    assert session.action_fails["a1"] == 1
    assert session.pressure == 1
    # 灾难：自然 1；后果表掷第 5 行（叙事行，无数值代价）。
    session, _ = _scene_session()
    out = pc.perform_action(session, "a1", rng=SeqRandom(1, 5))
    assert out["outcome"] == "catastrophe", out
    assert out["cost"]["cost"]["catastrophe_row"] == 5, out["cost"]
    # 机会耗尽：不再掷骰，直接结算代价并推压力。
    session, _ = _scene_session()
    session.action_fails["a1"] = 2
    out = pc.perform_action(session, "a1", rng=SeqRandom(10))
    assert out["status"] == "exhausted" and out["rolled"] is False, out
    assert session.pressure == 1
    # 免骰行动按达成处理。
    session, _ = _scene_session({"auto_pass": True})
    out = pc.perform_action(session, "a1")
    assert out["outcome"] == "success" and out["rolled"] is False, out
    # 状态劣势折入掷骰：失衡 −1 层 → d20=9 − 1d6=2 → 7 < DF 12 → 挫败。
    session, _ = _scene_session()
    session.party[0]["conditions"] = ["失衡"]
    out = pc.perform_action(session, "a1", rng=SeqRandom(9, 2))
    assert out["roll"]["edge"]["net"] == -1, out["roll"]
    assert out["outcome"] == "failure", out


def check_snapshot_serializable():
    """新字段（edge / damage_relations / downed_fails）不得破坏落盘契约。"""
    session = pc.RuleSession("s7")
    unit = _unit()
    unit["max_vitality"] = 10
    session.party = [unit]
    pc.deal_damage(session, unit, 3, tag="火焰")
    pc.change_resource(session, unit, "focus", -1)
    snapshot = session.snapshot()
    restored = pc.RuleSession.from_snapshot(json.loads(json.dumps(snapshot)))
    assert restored.party[0]["downed"] is True
    assert restored.party[0]["resources"]["focus"] == 0


# ═════════════════════════════════════════════════════════════════════════
# M2b（Issue #59）：战斗解算 / 派生值 / 成长
# ═════════════════════════════════════════════════════════════════════════

def check_initiative_formula():
    """先攻 = d20 + 洞察修正 + 灵巧修正的一半（向下取整）（02A:275）。"""
    unit = _unit(attributes={"INS": 8, "FIN": 6})   # +2 与 +1
    assert pc.initiative_adjustment(unit) == 2      # +1 的一半向下取整 = +0
    unit = _unit(attributes={"INS": 10, "FIN": 3})  # +3 与 −1
    assert pc.initiative_adjustment(unit) == 2      # −1 的一半向下取整 = −1
    unit = _unit(attributes={"INS": 4, "FIN": 7})   # 0 与 +1
    assert pc.initiative_adjustment(unit) == 0
    # 词缀 / 职途先攻加值折入（02A 词缀「迅捷」= 先攻 +3）。
    unit = _unit(attributes={"INS": 8, "FIN": 6})
    unit["init_bonus"] = 3
    assert pc.roll_initiative(unit, rng=SeqRandom(10)) == 10 + 2 + 3


def check_attack_grade_boundaries():
    """攻击六档边界（02A 附录速查表 653-663）。"""
    cases = ((10, "crit"), (11, "crit"), (9, "solid"), (6, "solid"),
             (5, "hit"), (0, "hit"), (-1, "graze"), (-2, "graze"),
             (-3, "miss"), (-9, "miss"))
    for margin, expected in cases:
        got = pc.attack_grade(margin)
        assert got == expected, "attack_grade(%d) = %s，期望 %s" % (
            margin, got, expected)
    # 自然 1 → 严重失手，优先于数值档。
    assert pc.attack_grade(19, natural=1) == "fumble"
    assert pc.attack_grade(0, natural=1) == "fumble"
    assert pc.attack_grade(-3, natural=2) == "miss"


def _combat_pair(session_id, vitality=20):
    session = pc.RuleSession(session_id)
    attacker = _unit()
    target = _unit()
    target["max_vitality"] = vitality
    target["vitality"] = vitality
    target["max_poise"] = 8       # 模板式韧性（敌体由 make_enemy 直接给出）
    target["poise"] = 8
    session.party = [attacker, target]
    return session, attacker, target


def check_resolve_attack_grades():
    """攻击解算：伤害档位数值、韧性削减、擦过不触发附加效果。"""
    # 命中：伤害 5，韧性 −2（伤害的一半，向下取整）。
    session, attacker, target = _combat_pair("s8")
    out = pc.resolve_attack(session, attacker, target, notation="1d6",
                            rng=SeqRandom(12, 5))
    assert out["grade"] == "hit" and out["margin"] == 2, out
    assert out["damage"] == 5 and target["vitality"] == 15
    assert out["poise"]["poise"] == 6, out["poise"]

    # 暴击：两次伤害骰取高；韧性全额削减；附加 1 层【失衡】。
    session, attacker, target = _combat_pair("s9")
    out = pc.resolve_attack(session, attacker, target, notation="1d6",
                            rng=SeqRandom(20, 6, 2))
    assert out["grade"] == "crit", out
    assert out["damage"] == 6 and out["poise"]["poise"] == 2
    assert "失衡" in target["conditions"]

    # 重击：正常伤害 + 2。
    session, attacker, target = _combat_pair("s10")
    out = pc.resolve_attack(session, attacker, target, notation="1d6",
                            rng=SeqRandom(16, 4))
    assert out["grade"] == "solid" and out["damage"] == 6, out

    # 擦过：一半伤害（向下取整），不触发任何附加效果（含韧性削减）。
    session, attacker, target = _combat_pair("s11")
    out = pc.resolve_attack(session, attacker, target, notation="1d6",
                            rng=SeqRandom(9, 7))
    assert out["grade"] == "graze" and out["damage"] == 3, out
    assert target["poise"] == 8 and target["conditions"] == []

    # 落空：无伤害。
    session, attacker, target = _combat_pair("s12")
    out = pc.resolve_attack(session, attacker, target, notation="1d6",
                            rng=SeqRandom(5))
    assert out["grade"] == "miss" and out["damage"] == 0
    assert target["vitality"] == 20

    # 严重失手（自然 1）：无伤害，攻击者获得 1 层【失衡】，掷失手表。
    session, attacker, target = _combat_pair("s13")
    out = pc.resolve_attack(session, attacker, target, notation="1d6",
                            rng=SeqRandom(1, 3))
    assert out["grade"] == "fumble" and out["fumble_row"] == 3, out
    assert out["fumble_text"] == pc.FUMBLE_TABLE[2]
    assert "失衡" in attacker["conditions"]
    assert len(pc.FUMBLE_TABLE) == 6


def check_poise_and_break():
    """韧性 / 破韧（01 第八节）：削减、破韧效果、重整、自然恢复。"""
    session = pc.RuleSession("s14")
    unit = _unit(attributes={"VIG": 6})          # 18 + 中型 6 = 24
    assert pc.max_poise(unit) == 24
    pc.recompute_unit(unit)                      # 派生值统一重算 → 满韧性开局
    assert unit["poise"] == 24 and unit["max_poise"] == 24
    out = pc.apply_poise_damage(session, unit, 20)
    assert out["poise"] == 4 and not out["broken"], out
    out = pc.apply_poise_damage(session, unit, 4)
    assert out["broken"] is True and unit["poise_broken"] is True

    # 破韧效果：承受伤害 +50%（向下取整）；防护 −4。
    unit["max_vitality"] = 20
    unit["vitality"] = 20
    result = pc.deal_damage(session, unit, 6)
    assert result["broken_amp"] is True and result["lost"] == 9, result
    assert pc.compute_guard(unit) == 6           # 10 − 4，夹到非负
    # 破韧期间韧性不自然恢复。
    assert pc.poise_regen_amount(unit) == 0

    # 重整：d20 + 体魄修正 vs DF 12；成功脱离破韧，韧性恢复至上限 50%。
    out = pc.rally_broken(session, unit, rng=SeqRandom(20))
    assert out["rallied"] is True and unit["poise"] == 12, out
    assert unit["poise_broken"] is False
    assert pc.poise_regen_amount(unit) == 6      # 上限 25% 向上取整
    # 重整失败保持破韧。
    unit["poise_broken"] = True
    unit["poise"] = 0
    out = pc.rally_broken(session, unit, rng=SeqRandom(1))
    assert out["rallied"] is False and unit["poise_broken"] is True


def check_boss_shields_and_phases():
    """首领韧性护盾与三阶段转化（01 第八节）。"""
    session = pc.RuleSession("s15")
    boss = pc.make_enemy("boss")
    assert boss["poise_shields"] == 3 and boss["max_poise"] == 40

    out = pc.apply_poise_damage(session, boss, 40)
    assert out["shield_used"] == 1 and out["phase"] == 1, out
    assert boss["poise"] == 24                   # 上限的 60%
    assert pc._condition_guard_bonus(boss) == pc.BOSS_PHASE1_GUARD  # 暴怒 −2

    out = pc.apply_poise_damage(session, boss, 24)
    assert out["shield_used"] == 1 and out["phase"] == 2, out

    # 绝望形态：全部伤害 +2；回合开始消耗 3 点活力。
    target = _unit()
    target["max_vitality"] = 30
    target["vitality"] = 30
    out = pc.resolve_attack(session, boss, target, notation="1d6",
                            rng=SeqRandom(15, 4))
    assert out["grade"] == "hit" and out["damage"] == 6, out   # 4 + 2
    events = pc._turn_start(session, boss)
    assert any(e["type"] == "boss_desperation" for e in events), events

    # 第 3 次破韧 → 崩解形态：真正的破韧（防护 −6，不再恢复韧性）。
    out = pc.apply_poise_damage(session, boss, boss["poise"])
    assert out["broken"] is True and out["phase"] == 3, out
    assert pc._condition_guard_bonus(boss) == pc.BOSS_PHASE3_GUARD
    assert pc.poise_regen_amount(boss) == 0


def check_enemy_templates():
    """敌体四档模板 / 缩放 / 词缀（02A 第九节）。"""
    enemy = pc.make_enemy("standard", name="标准·1")
    assert enemy["max_vitality"] == 26 and enemy["vitality"] == 26
    assert pc.compute_guard(enemy) == 14
    assert enemy["attack_bonus"] == 5 and enemy["damage_notation"] == "1d8"
    assert enemy["damage_bonus"] == 2 and enemy["max_poise"] == 14
    assert enemy["xp"] == 3 and enemy["no_struggle"] is True

    # 缩放：3 级 → 活力 +6 / 攻击 +1 / 伤害 +1（02A:499）。
    minion = pc.make_enemy("minion", level=3)
    assert minion["max_vitality"] == 18 and minion["attack_bonus"] == 4
    assert minion["damage_bonus"] == 2 and pc.compute_guard(minion) == 12

    # 词缀：迅捷先攻 +3；装甲防护 +3；巨型活力翻倍；预算计入经验权重。
    affixed = pc.make_enemy("minion", affixes=("迅捷", "装甲", "巨型"))
    assert affixed["init_bonus"] == 3 and pc.compute_guard(affixed) == 15
    assert affixed["max_vitality"] == 24 and affixed["xp"] == 8

    try:
        pc.make_enemy("minion", affixes=("迅捷",) * 5)
    except ValueError:
        pass
    else:
        raise AssertionError("词缀超过 4 个应报错（02A:530）")
    try:
        pc.make_enemy("titan")
    except ValueError:
        pass
    else:
        raise AssertionError("未知档位应报错")


def check_encounter_budget():
    """遭遇预算公式与 data 表逐格一致；强度档向上取整（02A 第十节）。"""
    with open(os.path.join(ROOT, "data", "system", "tactics_encounter.json"),
              encoding="utf-8") as fh:
        cells = json.load(fh)["budget_cells"]
    for cell in cells:
        got = pc.encounter_budget(cell["party_size"], cell["level"])
        assert got == cell["budget"], (cell, got)
    assert pc.strength_budget(12, "entangled") == 5     # 4.8 → 5
    assert pc.strength_budget(12, "standard") == 9      # 8.4 → 9
    assert pc.strength_budget(12, "harsh") == 12
    assert pc.strength_budget(12, "deadly") == 17       # 16.8 → 17
    try:
        pc.strength_budget(12, "epic")
    except ValueError:
        pass
    else:
        raise AssertionError("未知强度档应报错")


def check_build_encounter():
    """遭遇生成：强度档、组成限制（02A:571-577）。"""
    def _party(session, count, level=1):
        session.party = [pc.new_unit("p%d" % i, "成员%d" % i)
                         for i in range(count)]
        for unit in session.party:
            unit["level"] = level

    session = pc.RuleSession("s16")
    _party(session, 4)
    enemies = pc.build_encounter(session)        # 标准 70%：12 → 9
    tiers = [enemy["tier"] for enemy in enemies]
    spent = sum(pc.ENEMY_TIERS[t]["budget"] for t in tiers)
    assert spent <= 9 and spent >= 6, (tiers, spent)
    assert len(set(tiers)) >= 2                  # 至少两类不同的敌人
    assert "boss" not in tiers and tiers.count("minion") <= 6

    # 致命档 140%：12 → 17 → 精锐×2 + 标准（7+7+3）。
    enemies = pc.build_encounter(session, strength="deadly")
    assert [enemy["tier"] for enemy in enemies] == ["elite", "elite",
                                                    "standard"]

    # 首领预算限制：6 人 12 级总预算 84，首领 20 ≤ 60% → 可出场。
    big = pc.RuleSession("s17")
    _party(big, 6, level=12)
    enemies = pc.build_encounter(big, strength="harsh")
    assert any(enemy["tier"] == "boss" for enemy in enemies)

    # 单类型小预算：给首个敌体挂【迅捷】补足「或至少一个带词缀的个体」。
    small = pc.RuleSession("s18")
    _party(small, 1)
    enemies = pc.build_encounter(small)          # 3 → 标准×1
    assert len(enemies) == 1 and "迅捷" in enemies[0]["affixes"]


def check_derived_values():
    """派生值（01 第二节 / data/system/derived.json）。"""
    # 活力上限 = (体魄×3) + 职途起始活力 + (等级−1)×2。
    unit = _unit(attributes={"VIG": 6})
    unit["career_vitality"] = 8
    unit["level"] = 3
    assert pc.vitality_cap(unit) == 18 + 8 + 4
    unit["trauma"] = 3                           # 创伤 3 层 → 活力上限 −5
    assert pc.vitality_cap(unit) == 30 - 5

    # 移动 = 6 + 灵巧修正，最低 3 米。
    assert pc.move_speed(_unit(attributes={"FIN": 8})) == 8
    assert pc.move_speed(_unit(attributes={"FIN": 1})) == 4
    slow = _unit(attributes={"FIN": 1})
    slow["conditions"] = ["迟滞"]
    assert pc.move_speed(slow) == 3              # (6−2)//2 = 2 → 最低 3
    held = _unit()
    held["conditions"] = ["束缚"]
    assert pc.move_speed(held) == 0              # 束缚：移动降为 0
    runner = _unit()
    runner["stance"] = "游势"
    assert pc.move_speed(runner) == 9            # 游势 +3 米
    loaded = _unit()
    loaded["load"] = ["重", "重"]
    assert pc.move_speed(loaded) == 4            # 两件重 → −2 米

    # 负重：舒适 = 力道×5，最大 = 力道×10。
    assert pc.carry_capacity(_unit(attributes={"MGT": 6})) == {
        "comfort": 30, "max": 60}

    # 专注上限 = max(心智, 气场)×2 + 职途加成；气势池 = 队伍人数 + 2。
    assert pc.focus_cap(_unit(attributes={"MND": 6, "PRE": 4})) == 12
    sage = _unit(attributes={"MND": 6})
    sage["career_focus_bonus"] = 4
    assert pc.focus_cap(sage) == 16
    assert pc.tempo_cap(4) == 6

    # 防护明细：基础 + 装备 + 态势 + 掩体 + 状态（破绽 −3）。
    geared = _unit()
    geared["equipment"] = {"armor": {"name": "警用防弹衣", "guard": 3}}
    geared["stance"] = "守势"
    geared["cover"] = "中度"
    geared["conditions"] = ["破绽"]
    breakdown = pc.guard_breakdown(geared)
    assert breakdown["total"] == 10 + 3 + 3 + 4 - 3, breakdown
    assert [part["label"] for part in breakdown["parts"]] == [
        "基础防护", "警用防弹衣", "态势修正", "掩体修正", "状态修正"]

    # recompute_unit 统一重算：防护 / 活力上限 / 专注上限 / 韧性。
    unit = _unit(attributes={"VIG": 6, "MND": 6})
    unit["career_vitality"] = 8
    pc.recompute_unit(unit)
    assert unit["max_vitality"] == 26            # 18 + 8
    assert unit["guard"] == 10
    assert unit["max_resources"]["focus"] == 12
    assert unit["poise"] == pc.max_poise(unit) == 24


def check_condition_layers():
    """状态层数（01 第五节叠加规则 1-2）：叠至 3 层升级，同名改延长。"""
    session = pc.RuleSession("s19")
    session.combat = {"round": 2}                # 供延长记录读取回合数
    unit = _unit()
    assert pc.add_condition(session, unit, "失衡", layers=2) is True
    assert unit["condition_layers"]["失衡"] == 2
    assert pc.add_condition(session, unit, "失衡") is True   # 叠层 + 延长
    assert unit["condition_layers"]["失衡"] == 3
    assert unit["condition_rounds"]["失衡"] == 2
    # 封顶 3 层。
    assert pc.add_condition(session, unit, "失衡", layers=2) is True
    assert unit["condition_layers"]["失衡"] == 3
    # 非叠层状态不进层数表，重复施加只延长（返回 False）。
    assert pc.add_condition(session, unit, "中毒") is True
    assert pc.add_condition(session, unit, "中毒") is False
    assert "中毒" not in unit["condition_layers"]
    # 流血同为叠层状态（02A 机动「要害打击」给 3 层【流血】）。
    other = _unit()
    pc.add_condition(session, other, "流血", layers=3)
    assert other["condition_layers"]["流血"] == 3


def check_downed_struggle():
    """濒危挣扎循环（01 第十一节）：3 成稳定 / 3 败消亡 / 医疗 DF 14。"""
    session = pc.RuleSession("s20")
    unit = _unit()
    unit["max_vitality"] = 10
    unit["vitality"] = 10
    pc.deal_damage(session, unit, 10)
    assert unit["downed"] is True
    for _ in range(3):
        out = pc.downed_struggle(session, unit, rng=SeqRandom(20))
    assert out["stabilized"] is True, out
    assert unit["downed"] is False and unit["vitality"] == 1
    assert unit["trauma"] == 1                   # 稳定获得 1 层【创伤】

    doomed = _unit()
    doomed["max_vitality"] = 10
    doomed["vitality"] = 10
    doomed["downed"] = True
    for _ in range(3):
        out = pc.downed_struggle(session, doomed, rng=SeqRandom(1))
    assert out["dead"] is True and doomed["dead"] is True, out

    # 医疗急救（DF 14，判定由调用方掷）：立即稳定，只加创伤不回活力。
    patient = _unit()
    patient["max_vitality"] = 10
    patient["downed"] = True
    out = pc.stabilize_downed(session, patient, source="medical")
    assert out["stabilized"] is True and out["healed"] == 0, out
    assert patient["trauma"] == 1 and patient["downed"] is False
    assert pc.MEDICAL_STABILIZE_DF == 14 and pc.DOWNED_STRUGGLE_DF == 12


def check_growth_system():
    """成长（01 第十四节）：经验点法 / 每级收益 / 六类选择 / A 类 +4 上限。"""
    thresholds = dict(pc.LEVEL_XP_THRESHOLDS)
    assert thresholds[2] == 10 and thresholds[3] == 30 and thresholds[12] == 660
    assert pc.level_for_xp(9) == 1 and pc.level_for_xp(10) == 2
    assert pc.level_for_xp(29) == 2 and pc.level_for_xp(30) == 3
    assert pc.level_for_xp(660) == 12 and pc.level_for_xp(9999) == 12
    assert pc.growth_points_for(2) == 1          # 每级固定收益：1 项成长选择

    # 升级：等级写回、成长点数发放、活力上限 +2 经 recompute 生效。
    session = pc.RuleSession("s21")
    unit = _unit(attributes={"VIG": 4})
    session.party = [unit]
    out = pc.award_xp(session, unit, 10)
    assert out["level"] == 2 and out["growth_points"] == 1, out
    assert unit["max_vitality"] == 14            # 12 + 每级活力上限 +2

    # A 类：属性 +1；通过成长每项最多 +4。
    session2 = pc.RuleSession("s22")
    unit2 = _unit()
    session2.party = [unit2]
    unit2["growth_points"] = 10
    pc.assign_attribute(session2, "u1", "MGT", 1)
    assert unit2["attributes"]["MGT"] == 5
    assert unit2["growth_attr_gain"]["MGT"] == 1
    for _ in range(3):
        pc.assign_attribute(session2, "u1", "MGT", 1)
    assert unit2["attributes"]["MGT"] == 8
    try:
        pc.assign_attribute(session2, "u1", "MGT", 1)
    except ValueError:
        pass
    else:
        raise AssertionError("A 类成长每项最多 +4 应报错")

    # 六类成长选择：B 新熟练 → 再选改为专精；C 要求已有熟练。
    unit2["growth_points"] = 10
    pc.apply_growth_choice(session2, "u1", "B", {"skill": "射击"})
    assert "射击" in unit2["proficiencies"]
    pc.apply_growth_choice(session2, "u1", "B", {"skill": "射击"})
    assert "射击" in unit2["specializations"]
    try:
        pc.apply_growth_choice(session2, "u1", "C", {"skill": "射击"})
    except ValueError:
        pass
    else:
        raise AssertionError("已是专精的技能不能再选 C")
    try:
        pc.apply_growth_choice(session2, "u1", "C", {"skill": "医疗"})
    except ValueError:
        pass
    else:
        raise AssertionError("C 类专精要求该技能已有熟练")
    # 同一类别不得连续选择超过 2 次（01 第十四节）。
    try:
        pc.apply_growth_choice(session2, "u1", "B", {"skill": "运动"})
    except ValueError:
        pass
    else:
        raise AssertionError("B 类连选第 3 次应报错")
    # 换个类别打断连续计数，B 类恢复可用。
    pc.apply_growth_choice(session2, "u1", "A", {"attribute": "FIN"})
    assert unit2["attributes"]["FIN"] == 5
    pc.apply_growth_choice(session2, "u1", "B", {"skill": "运动"})
    assert "运动" in unit2["proficiencies"]
    # D/E/F：世界模组与导引者裁定，规则层只记录。
    pc.apply_growth_choice(session2, "u1", "E", {"feat": "先手直觉"})
    assert unit2["growth_records"][-1] == {"category": "E",
                                           "payload": {"feat": "先手直觉"}}


def check_combat_integration():
    """战斗集成：先攻一次掷骰整场保持、敌体行动走 deal_damage、
    濒危挣扎在战斗内循环、失衡 3 层倒地、创伤 4 层开局获得【恐惧】。"""
    # 骰序设计（party 先掷）：英雄先攻 1、敌体先攻 20 → 敌体首轮暴击把
    # 英雄打濒危（挣扎第 1 次失败）；此后敌体连掷自然 1 严重失手（3 次
    # 后自身失衡 3 层倒地），英雄三个回合挣扎全成 → 稳定。
    saved_random = pc.random
    pc.random = SeqRandom(1, 20, 20, 8, 8, 1, 1, 6, 20, 1, 6, 20, 1, 6, 20)
    try:
        session = pc.RuleSession("s23")
        hero = _unit()
        hero["max_vitality"] = 20
        hero["vitality"] = 3
        hero["max_poise"] = 18                   # VIG 4 × 3 + 中型 6
        hero["poise"] = 18
        session.party = [hero]
        enemy = pc.make_enemy("standard", name="暴徒", unit_id="enemy-1")
        pc.start_combat(session, enemies=[enemy])
        # 先攻：一次掷骰，整场保持（01 第六节回合结构）。
        assert session.combat["inits"]["party:u1"] == 1
        assert session.combat["inits"]["enemy:0"] == 20
        assert pc.combat_view(session)["current"]["id"] == "u1"

        # 敌体首轮暴击打濒危 → 敌体连续严重失手 → 英雄挣扎 3 次成功 →
        # 稳定（+1 活力、+1 创伤）；敌体失衡 3 层 → 自动倒地。
        pc._advance(session)
        assert hero["downed"] is False, hero
        assert hero["vitality"] == 1 and hero["trauma"] == 1, hero
        assert hero["poise_broken"] is False, hero
        events = session.combat["events"]
        assert any(e["type"] == "damage" and e["grade"] == "crit"
                   for e in events), events
        assert any(e["type"] == "downed_struggle" and e["stabilized"]
                   for e in events), events
        assert any(e["type"] == "prone" for e in events), events

        # 创伤 4 层 → 每次进入战斗开局获得 1 层【恐惧】（01 第十一节）。
        session2 = pc.RuleSession("s24")
        veteran = _unit()
        veteran["max_vitality"] = 20
        veteran["vitality"] = 20
        veteran["trauma"] = 4
        session2.party = [veteran]
        pc.start_combat(session2, enemies=[
            pc.make_enemy("minion", unit_id="enemy-1")])
        assert "恐惧" in veteran["conditions"], veteran["conditions"]
    finally:
        pc.random = saved_random


# ════════════════════════════════════════════════════════════════════════
# M2c（Issue #72）：构建引擎骰池与结算（02B 第一至五节）
# ════════════════════════════════════════════════════════════════════════

# ── 骰池的构成（02B 第一节，四部分）─────────────────────────────────────

def check_build_pool_composition():
    # 熟练加值 → 枚数：+2 → +1 枚；+3 → +2 枚；+4 → +2 枚（02B 第一节）。
    assert pc.build_proficiency_dice(2) == 1
    assert pc.build_proficiency_dice(3) == 2
    assert pc.build_proficiency_dice(4) == 2
    try:
        pc.build_proficiency_dice(-1)
        raise AssertionError("熟练加值为负应报错")
    except ValueError:
        pass
    # 四部分：主属性枚数 = 属性值本身（属性 5 → 5 枚）。
    parts = pc.build_pool(5, 2)
    assert parts == {"attribute": 5, "proficiency": 1,
                     "specialization": 0, "temp": 0, "total": 6}, parts
    # 专精额外 +1 枚；临时加成照传入结算。
    parts = pc.build_pool(6, 3, specialization=True, temp=2)
    assert parts["total"] == 6 + 2 + 1 + 2, parts
    assert pc.build_pool(10, 4)["attribute"] == 10
    assert pc.BUILD_SPECIALIZATION_DICE == 1
    assert pc.BUILD_TEMP_DICE_TYPICAL == (1, 3)  # 参考量级：通常 1–3 枚


# ── 成功阈值与单骰成功率表（02B 第一节）────────────────────────────────

def check_build_threshold_and_rates():
    assert pc.BUILD_SUCCESS_THRESHOLD == 5
    assert pc.BUILD_DEFAULT_SIDES == 10
    for sides, rate in ((6, 33), (8, 50), (10, 60), (12, 67)):
        assert pc.build_die_rate(sides) == rate, (sides, rate)
    try:
        pc.build_die_rate(100)
        raise AssertionError("未记录的骰面应报错")
    except ValueError:
        pass


# ── 需求成功数 8 档（02B 第一节）───────────────────────────────────────

def check_build_required_successes():
    assert len(pc.BUILD_REQUIRED_SUCCESSES) == 8
    for need, name, df in ((1, "平凡", 8), (2, "简易", 10), (3, "常规", 12),
                           (4, "有挑战", 14), (5, "困难", 16), (6, "严峻", 18),
                           (8, "极限", 20), (10, "传说", 24)):
        info = pc.build_difficulty(need)
        assert info["need"] == need and info["name"] == name \
            and info["tactics_df"] == df, info
    try:
        pc.build_difficulty(7)
        raise AssertionError("非 8 档的需求成功数应报错")
    except ValueError:
        pass


# ── 五档结果梯度（02B 第二节：结果 = S − N）────────────────────────────

def check_build_outcome_bands():
    cases = (
        (-5, "catastrophe"), (-2, "catastrophe"),
        (-1, "failure"),
        (0, "narrow"),
        (1, "success"), (2, "success"),
        (3, "triumph"), (7, "triumph"),
    )
    for margin, expected in cases:
        got = pc.build_outcome(margin)
        assert got == expected, "build_outcome(%d) = %s，期望 %s" % (
            margin, got, expected)
    # 动量增益：凯旋 +2 / 成功 +1 / 险成 +1 / 挫败 +1 / 灾难 +2
    assert pc.BUILD_OUTCOME_MOMENTUM == {
        "triumph": 2, "success": 1, "narrow": 1,
        "failure": 1, "catastrophe": 2}


# ── 判定结算与两个特例（02B 第一节四步 + 第二节）───────────────────────

def check_build_roll_resolution():
    # 普通结算：d10 掷 [7, 3, 5]，S = 2，need 2 → 0 → 险成，动量 +1。
    seq = SeqRandom(7, 3, 5)
    result = pc.build_roll(3, 2, rng=seq)
    assert result["successes"] == 2 and result["margin"] == 0
    assert result["outcome"] == "narrow" and result["momentum"] == 1
    assert result["all_fail"] is False and result["burst"] == 0
    json.dumps(result)  # 判定结果必须可直接落盘

    # 特例 1 · 最大值爆发：最大面值的骰记 2 次成功（d10 的 10）。
    seq = SeqRandom(10, 10, 4)
    result = pc.build_roll(3, 1, rng=seq)
    assert result["burst"] == 2 and result["successes"] == 4, result
    assert result["outcome"] == "triumph" and result["momentum"] == 2
    # d8 的 8 / d12 的 12 同理（最大面值 + 阈值达标各记 1 次）。
    result = pc.build_roll(2, 1, sides=8, rng=SeqRandom(8, 5))
    assert result["successes"] == 3 and result["burst"] == 1, result
    result = pc.build_roll(2, 1, sides=12, rng=SeqRandom(12, 5))
    assert result["successes"] == 3 and result["burst"] == 1, result

    # 特例 2 · 全骰皆负：无任何一枚达到阈值 → 至少挫败 + 1 点额外动量。
    seq = SeqRandom(4, 4, 4)
    result = pc.build_roll(3, 1, rng=seq)
    assert result["all_fail"] is True and result["outcome"] == "failure"
    assert result["momentum"] == 2, result   # 挫败 1 + 额外 1
    # 全骰皆负把灾难压回挫败：need 3、全 4 → margin −3 本应灾难。
    result = pc.build_roll(4, 3, rng=SeqRandom(4, 4, 4, 4))
    assert result["margin"] == -3 and result["outcome"] == "failure"
    assert result["momentum"] == 2
    # 阈值边界：≥5 记成功，4 不算（含 d6/d12 的阈值不变）。
    result = pc.build_roll(2, 1, rng=SeqRandom(5, 4))
    assert result["successes"] == 1 and result["outcome"] == "narrow"
    # 空池同样按全骰皆负处理（没有任何一枚达到阈值）。
    result = pc.build_roll(0, 2, rng=SeqRandom())
    assert result["all_fail"] is True and result["outcome"] == "failure"


# ── 骰阶与期望成功数速查（02B 第三节）──────────────────────────────────

def check_build_die_ranks():
    assert len(pc.BUILD_DIE_RANKS) == 5
    for rank, sides in (("I", 6), ("II", 8), ("III", 10), ("IV", 12), ("V", 20)):
        assert pc.build_rank_die(rank) == sides, rank
    for bad in ("VI", "", "x"):
        try:
            pc.build_rank_die(bad)
            raise AssertionError("未知骰阶应报错：%r" % bad)
        except ValueError:
            pass
    # 起始角色的所有骰阶为 d8（骰阶 II）。
    assert pc.BUILD_STARTING_RANK == "II"
    assert pc.build_rank_die(pc.BUILD_STARTING_RANK) == 8
    # 期望成功数速查表：8 行 × d8/d10/d12 逐格吻合。
    assert len(pc.BUILD_EXPECTED_SUCCESS_TABLE) == 8
    for pool, d8, d10, d12 in pc.BUILD_EXPECTED_SUCCESS_TABLE:
        assert pc.build_expected_successes(pool, 8) == d8, (pool, "d8")
        assert pc.build_expected_successes(pool, 10) == d10, (pool, "d10")
        assert pc.build_expected_successes(pool, 12) == d12, (pool, "d12")
    # 表外组合由同一公式推导：6 枚 d10 → 3.6（02B 第三节示例）。
    assert pc.build_expected_successes(6, 10) == 3.6


# ── 动量（02B 第四节：7 源获取 / 7 项花费 / 共享池与个人持有）──────────

def check_momentum_pool():
    # 获取 7 源：3 条判定档位 + 4 条叙事触发，数值逐条对照。
    assert len(pc.MOMENTUM_GAIN_SOURCES) == 7
    amounts = dict((row[0], row[1]) for row in pc.MOMENTUM_GAIN_SOURCES)
    assert amounts == {
        "outcome_success": 1, "outcome_triumph": 2,
        "outcome_catastrophe": 2, "assist": 1, "heavy_damage_taken": 1,
        "driven_choice": 1, "enemy_morale_break": 2}, amounts
    # 花费 7 项：费用逐条对照（补强 1 / 重掷 2 / 升阶 2 / 改写 1 /
    # 额外行动 3 / 刷新 2 / 连携增强 1）。
    assert len(pc.MOMENTUM_SPENDS) == 7
    costs = dict((key, value[0]) for key, value in pc.MOMENTUM_SPENDS.items())
    assert costs == {"boost": 1, "reroll": 2, "upgrade": 2, "rewrite": 1,
                     "extra_action": 3, "refresh": 2, "combo_boost": 1}, costs
    assert pc.MOMENTUM_BOOST_MAX == 3

    # 共享池起始 0（上限 10）、个人持有上限 5。
    assert pc.MOMENTUM_SHARED_START == 0
    assert pc.MOMENTUM_SHARED_CAP == 10
    assert pc.MOMENTUM_PERSON_CAP == 5
    pool = pc.new_momentum_pool(("a", "b"))
    assert pool == {"shared": 0, "members": {"a": 0, "b": 0}}
    json.dumps(pool)

    # 个人入账：先填个人持有（上限 5），溢出进共享池（上限 10）。
    pc.momentum_add(pool, "a", 3)
    assert pool["members"]["a"] == 3 and pool["shared"] == 0
    pc.momentum_add(pool, "a", 4)      # 3 + 4 → 个人 5，共享 2
    assert pool["members"]["a"] == 5 and pool["shared"] == 2
    # 全队来源（士气崩溃）：直接进共享池，夹在 10。
    pc.momentum_add(pool, None, 20)
    assert pool["shared"] == 10
    try:
        pc.momentum_add(pool, "a", -1)
        raise AssertionError("负数入账应报错")
    except ValueError:
        pass

    # 花费：先扣个人，不足部分从共享池补扣。
    out = pc.momentum_spend(pool, "a", "extra_action")   # 3 点 → 个人出 3
    assert out["cost"] == 3
    assert pool["members"]["a"] == 2 and pool["shared"] == 10
    out = pc.momentum_spend(pool, "b", "reroll")          # 个人 0 → 全部共享出
    assert pool["members"]["b"] == 0 and pool["shared"] == 8
    out = pc.momentum_spend(pool, "a", "upgrade", times=1)  # 个人 2 出 2
    assert pool["members"]["a"] == 0 and pool["shared"] == 8
    out = pc.momentum_spend(pool, "a", "extra_action")    # 个人 0 → 共享出 3
    assert pool["shared"] == 5
    out = pc.momentum_spend(pool, "b", "extra_action")    # 个人 0 → 共享出 3
    assert pool["shared"] == 2
    # 合计不足 → 整体拒绝，不产生部分扣减。
    before = dict(pool)
    try:
        pc.momentum_spend(pool, "b", "extra_action")      # 需 3，只有共享 2
        raise AssertionError("动量不足应报错")
    except ValueError:
        pass
    assert pool == before, "拒绝时不得产生部分扣减"
    try:
        pc.momentum_spend(pool, "b", "nope")
        raise AssertionError("未知花费项应报错")
    except ValueError:
        pass

    # 场景结束：共享池清零，个人持有不动。
    pc.momentum_add(pool, "b", 2)
    cleared = pc.momentum_clear(pool)
    assert cleared["cleared"] == 2 and pool["shared"] == 0
    assert pool["members"]["b"] == 2


# ── 应力与超载（02B 第五节）────────────────────────────────────────────

def check_stress_system():
    # 应力上限 = 体魄 + 心智 + 5（典型 13–21）。
    assert pc.stress_cap(_unit()) == 13                       # 4 + 4 + 5
    assert pc.stress_cap(_unit(attributes={"VIG": 6, "MND": 6})) == 17
    assert pc.STRESS_CAP_FLAT == 5
    # 获取应力的 5 种方式（数值逐条对照）。
    assert len(pc.STRESS_GAINS) == 5
    gains = dict((row[0], row[1]) for row in pc.STRESS_GAINS)
    assert gains == {"overload_check": 2, "heavy_hit": 1,
                     "repeat_overload": 3, "beyond_ability": 2,
                     "desperate": 2}, gains

    # 惩罚 4 段：0–49% 无 / 50–79% −1 枚 / 80–99% −2 枚 + 耐受判定 / 100% 崩溃。
    p = pc.stress_penalty(0, 20)
    assert p["band"] == "0–49%" and p["pool_dice"] == 0
    assert p["tolerance_need"] is None and p["collapse"] is False
    p = pc.stress_penalty(10, 20)                              # 50%
    assert p["band"] == "50–79%" and p["pool_dice"] == -1
    p = pc.stress_penalty(15, 20)                              # 75%
    assert p["band"] == "50–79%" and p["pool_dice"] == -1
    p = pc.stress_penalty(16, 20)                              # 80%
    assert p["band"] == "80–99%" and p["pool_dice"] == -2
    assert p["tolerance_need"] == pc.STRESS_TOLERANCE_NEED == 3
    p = pc.stress_penalty(19, 20)                              # 95%
    assert p["band"] == "80–99%" and p["collapse"] is False
    p = pc.stress_penalty(20, 20)                              # 100% → 崩溃
    assert p["collapse"] is True and p["band"] == "100%"

    # 应力累积：夹在上限内；达到上限立即触发崩溃（4 步）。
    session = pc.RuleSession("stress")
    unit = _unit(attributes={"VIG": 5, "MND": 5})              # 上限 15
    out = pc.stress_gain(session, unit, 14)
    assert out["stress"] == 14 and out["overload"] is None
    out = pc.stress_gain(session, unit, 5)                     # 顶到 15 → 崩溃
    assert out["overload"] is not None
    steps = out["overload"]
    # 步骤 4：应力回落至上限的 50%（15 // 2 = 7）。
    assert steps["stress_after"] == 7 and unit["stress"] == 7
    # 步骤 1：失去本回合所有 AP（带 ap 字段的单位直接清零）。
    assert steps["ap_lost"] is True
    # 步骤 2：3 层【疲惫】与 1 点负担。
    assert steps["fatigue_layers"] == 3
    assert "疲惫" in unit["conditions"]
    assert unit["condition_layers"]["疲惫"] == 3
    assert steps["burden"] == 1 and unit["resources"]["strain"] == 1
    # 步骤 3：中断标记。
    assert steps["interrupted"] is True
    # 崩溃后还能继续累积并再次崩溃（回落 ≠ 免疫）。
    out = pc.stress_gain(session, unit, 8)                     # 7 + 8 = 15
    assert out["overload"] is not None and unit["stress"] == 7

    # AP 清零路径：战斗单位带 ap 字段。
    fighter = _unit()                                          # 上限 13
    fighter["ap"] = 3
    pc.stress_gain(pc.RuleSession("ap"), fighter, 13)
    assert fighter["ap"] == 0

    # 降低应力 4 手段：短歇 −2 / 长歇 −4（安全据点 −6）/ 驱动目标 −2 /
    # 能力道具须显式传入 amount。
    assert pc.LONG_REST_SAFE_HAVEN == -6
    rest = _unit()
    session2 = pc.RuleSession("rest")
    pc.stress_gain(session2, rest, 12)                         # 上限 13，不崩溃
    assert rest["stress"] == 12
    pc.stress_reduce(session2, rest, "short_rest")             # −2 → 10
    assert rest["stress"] == 10
    pc.stress_reduce(session2, rest, "long_rest")              # −4 → 6
    assert rest["stress"] == 6
    pc.stress_reduce(session2, rest, "long_rest", safe_haven=True)  # −6 → 0
    assert rest["stress"] == 0
    pc.stress_reduce(session2, rest, "drive_goal")             # 0 为下限
    assert rest["stress"] == 0
    try:
        pc.stress_reduce(session2, rest, "ability_item")
        raise AssertionError("能力道具未传 amount 应报错")
    except ValueError:
        pass
    out = pc.stress_reduce(session2, rest, "ability_item", amount=3)
    assert out["changed"] == 0                                 # 已是 0，不再降
    try:
        pc.stress_reduce(session2, rest, "meditate")
        raise AssertionError("未知手段应报错")
    except ValueError:
        pass

    # recompute_unit 把应力夹回上限内（属性下降后）。
    shrunk = _unit(attributes={"VIG": 6, "MND": 6})            # 上限 17
    shrunk["stress"] = 16
    pc.recompute_unit(shrunk, session2)
    assert shrunk["stress"] == 16
    shrunk["attributes"]["MND"] = 3                            # 上限 14
    pc.recompute_unit(shrunk, session2)
    assert shrunk["stress"] == 14


# ── ATLAS I5：战术投影接到规则核心（Issue #67）─────────────────────────

#: 夹具地图里由 generate_tactical 投出的战术帧 id（world=t-i5，房间 r1）。
TAC_ID = "t-i5/tactical/r1"


def _tactical_atlas():
    """I5 测试夹具：手工造一张地图，不读 data/worlds/。

      · w/site/s1   metric 地点帧：r1-r2-r3 一条线 + 高台 h1(z=1，邻 r1)；
                    供 atlas_gen.generate_tactical 投出**真实战术帧**；
      · w/met       metric 平帧：mA-mB-mC-mD 一条线（覆盖 3 格 = 中）；
      · w/abstract  abstract 帧：a1-a2-a3 一条线（不换算米）。
    """
    atlas = al.new_atlas("t-i5", 7)
    al.add_frame(atlas, "w/site/s1", space="metric",
                 z_meaning="层", cell="房间")
    for pid, (x, y, z) in {"r1": (0, 0, 0), "r2": (1, 0, 0),
                           "r3": (2, 0, 0), "h1": (0, 1, 1)}.items():
        al.add_place(atlas, {"id": pid, "name": pid, "kind": "room",
                             "frame_id": "w/site/s1", "x": x, "y": y, "z": z})
    al.add_link(atlas, "w/site/s1", "r1", "r2", "东")
    al.add_link(atlas, "w/site/s1", "r2", "r3", "东")
    al.add_link(atlas, "w/site/s1", "h1", "r1", "南")
    al.add_frame(atlas, "w/met", space="metric", z_meaning="街区", cell="6米")
    for pid, (x, y, z) in {"mA": (0, 0, 0), "mB": (1, 0, 0),
                           "mC": (2, 0, 0), "mD": (3, 0, 0)}.items():
        al.add_place(atlas, {"id": pid, "name": pid, "kind": "zone",
                             "frame_id": "w/met", "x": x, "y": y, "z": z})
    al.add_link(atlas, "w/met", "mA", "mB", "东")
    al.add_link(atlas, "w/met", "mB", "mC", "东")
    al.add_link(atlas, "w/met", "mC", "mD", "东")
    al.add_frame(atlas, "w/abstract", space="abstract",
                 z_meaning="层", cell="一次走位")
    for pid, (x, y, z) in {"a1": (0, 0, 0), "a2": (1, 0, 0),
                           "a3": (2, 0, 0)}.items():
        al.add_place(atlas, {"id": pid, "name": pid, "kind": "zone",
                             "frame_id": "w/abstract", "x": x, "y": y,
                             "z": z})
    al.add_link(atlas, "w/abstract", "a1", "a2", "东")
    al.add_link(atlas, "w/abstract", "a2", "a3", "东")
    return atlas


def _tac_zones(atlas):
    """战术帧内 {房间名: 区域 place_id}。"""
    return {place["name"]: place["id"]
            for place in atlas["frames"][TAC_ID]["places"].values()}


def check_tactical_projection_bands():
    """距离档：0 同区 / 1 相邻 / 2 近 / 3–4 中（ATLAS-DESIGN.md §3.3）；
    没接地图或没钉住 → None（心象剧场默认）。"""
    atlas = _tactical_atlas()
    tac = ag.generate_tactical(atlas, "r1")
    assert tac["space"] == "metric"
    session = pc.RuleSession("s-band")
    zones = _tac_zones(atlas)
    pc.attach_tactical_map(session, atlas, TAC_ID,
                           {"u1": zones["r1"], "u2": zones["r2"],
                            "u3": zones["r3"]})
    u1 = pc.new_unit("u1", "甲")
    u2 = pc.new_unit("u2", "乙")
    u3 = pc.new_unit("u3", "丙")
    ghost = pc.new_unit("u9", "没钉住的游魂")
    assert pc.tactical_range_band(session, u1, u1) == "same"
    assert pc.tactical_range_band(session, u1, u2) == "adjacent"
    # 验收主断言：两格图距离得到第 3.3 节的档（2 → 近）。
    assert pc.tactical_range_band(session, u1, u3) == "near"
    # 没钉住的单位 → None：不臆造位置，回到心象剧场。
    assert pc.tactical_range_band(session, u1, ghost) is None
    # 3 格 = 中：内核直接投影 + 会话接了带 4 格的 metric 帧都能查到。
    assert al.range_band(atlas, "w/met", "mA", "mD") == "mid"
    wide = pc.RuleSession("s-wide")
    pc.attach_tactical_map(wide, atlas, "w/met", {"u1": "mA", "u4": "mD"})
    assert pc.tactical_range_band(wide, u1, pc.new_unit("u4", "丁")) == "mid"
    # 没接地图 → None；detach 之后同样 None。
    assert pc.tactical_range_band(pc.RuleSession("s-bare"), u1, u2) is None
    pc.detach_tactical_map(session)
    assert pc.tactical_range_band(session, u1, u2) is None
    # attach 校验：帧不存在、钉扎不在帧内，都要直接 raise。
    for bad_atlas, bad_frame, bad_zones in (
            (atlas, "w/none", {}),
            (atlas, "w/met", {"u1": "a1"})):
        try:
            pc.attach_tactical_map(pc.RuleSession("s-bad"), bad_atlas,
                                   bad_frame, bad_zones)
            raise AssertionError("attach 应拒绝：%s %r" % (bad_frame, bad_zones))
        except ValueError:
            pass


def check_tactical_high_ground_and_attack():
    """高地：z 更高且相邻（有连接）为高地；折进攻击解算 = +1 枚助势骰
    （docs/system/02A 附录「常用情境修正」）；没地图时无此修正。"""
    atlas = _tactical_atlas()
    ag.generate_tactical(atlas, "r1")
    zones = _tac_zones(atlas)
    session = pc.RuleSession("s-hg")
    pc.attach_tactical_map(session, atlas, TAC_ID,
                           {"e1": zones["h1"], "u1": zones["r1"],
                            "u2": zones["r2"]})
    sharp = pc.new_unit("e1", "高台射手")
    victim = pc.new_unit("u1", "低处目标")
    other = pc.new_unit("u2", "隔壁目标")
    # h1(z=1) 对相邻且更低的 r1：高地成立。
    assert pc.tactical_high_ground(session, sharp, victim) is True
    # 反向（对方更高）不成立；相邻但没有连接（h1→r2）也不成立。
    assert pc.tactical_high_ground(session, victim, sharp) is False
    assert pc.tactical_high_ground(session, sharp, other) is False
    # 没钉住 / 没接地图 → False（行为与 M2b 默认一致）。
    assert pc.tactical_high_ground(
        session, sharp, pc.new_unit("u9", "游魂")) is False
    assert pc.tactical_high_ground(pc.RuleSession("s-bare"),
                                   sharp, victim) is False
    # 攻击解算接线： SeqRandom(15, 3) → d20=15、助势 d6=3。
    # 有高地：总 18，对防护 10 的裸单位 margin +8 → 重击，并留高地标记。
    out = pc.resolve_attack(session, sharp, victim, notation="1d6",
                            rng=SeqRandom(15, 3))
    assert out["high_ground"] is True, out
    assert out["grade"] == "solid", out
    # 没接地图：同一掷 d20=15 → margin +5 → 命中，且没有高地标记。
    # （换一个全新目标：上一个已被打到濒危，状态修正会改变防护。）
    plain = pc.RuleSession("s-plain")
    fresh = pc.new_unit("u3", "全新目标")
    out2 = pc.resolve_attack(plain, sharp, fresh, notation="1d6",
                             rng=SeqRandom(15))
    assert "high_ground" not in out2, out2
    assert out2["grade"] == "hit", out2


def check_tactical_move_zones_abstract():
    """metric 帧把米换成跨区 ceil(米/6)；abstract 帧不换算（§5.6/§6.3）；
    没接地图 → None；投影块不进快照。"""
    atlas = _tactical_atlas()
    session = pc.RuleSession("s-mz")
    pc.attach_tactical_map(session, atlas, "w/met", {"u1": "mA"})
    assert pc.tactical_move_zones(session, 6) == 1
    assert pc.tactical_move_zones(session, 12) == 2
    assert pc.tactical_move_zones(session, 3) == 1        # 不足一格向上取整
    assert pc.tactical_move_zones(session, 0) == 0
    # abstract 帧：米数不参与，一次走位跨一区。
    abs_session = pc.RuleSession("s-mz-abs")
    pc.attach_tactical_map(abs_session, atlas, "w/abstract", {"u1": "a1"})
    assert pc.tactical_move_zones(abs_session, 12) is None
    assert pc.tactical_move_zones(abs_session, 3) is None
    # 没接地图 → None。
    assert pc.tactical_move_zones(pc.RuleSession("s-mz-none"), 12) is None
    # 元信息视图不含活地图本体。
    assert pc.tactical_map(abs_session) == {
        "frame_id": "w/abstract", "space": "abstract",
        "unit_zones": {"u1": "a1"}}
    # 投影块（活地图）不进快照；恢复后由网页层重新 attach。
    snap = abs_session.snapshot()
    assert "tactical" not in snap
    restored = pc.RuleSession.from_snapshot(snap)
    assert restored.tactical is None
    assert pc.tactical_move_zones(restored, 12) is None


# ════════════════════════════════════════════════════════════════════════
# M2d（Issue #122）：构建引擎收尾（02B 第六至十一节）
# ════════════════════════════════════════════════════════════════════════

# ── 战斗中的应用（02B 第六节 / data/system/build_combat.json）─────────

def check_build_attack_flow():
    assert len(pc.BUILD_ATTACK_FLOW) == 5
    # 闪避需求 5 档，数值逐条对照。
    assert len(pc.BUILD_DODGE_NEEDS) == 5
    needs = dict((row[0], row[2]) for row in pc.BUILD_DODGE_NEEDS)
    assert needs == {"static": 2, "standard": 3, "agile": 4,
                     "protected": 5, "armored": 6}, needs
    assert pc.build_dodge_need("static") == 2
    assert pc.build_dodge_need("标准敌人") == 3          # 中文名亦可
    assert pc.build_dodge_need("armored") == 6
    try:
        pc.build_dodge_need("dragon")
        raise AssertionError("未知目标类型应报错")
    except ValueError:
        pass
    # 掩体/态势/位置优势：每项 +1，最多 +2；不为负。
    assert pc.BUILD_DODGE_MODIFIER_MAX == 2
    assert pc.build_dodge_need_adjusted("standard", modifiers=1) == 4
    assert pc.build_dodge_need_adjusted("standard", modifiers=5) == 5
    assert pc.build_dodge_need_adjusted("standard", modifiers=-3) == 3


def check_build_hit_bands():
    # 命中结果 5 档；边界逐条对照。
    assert len(pc.BUILD_HIT_RESULTS) == 5
    cases = ((9, "overrun", 3), (5, "overrun", 3), (4, "overrun", 3),
             (3, "multi_hit", 2), (2, "multi_hit", 2),
             (1, "hit", 1), (0, "hit", 1),
             (-1, "graze", 0.5),
             (-2, "miss", 0), (-9, "miss", 0))
    for d, rid, hits in cases:
        got = pc.build_hit_result(d)
        assert got["id"] == rid and got["hits"] == hits, (d, got)
    # 贯穿打击附加 3 层【失衡】；擦过为半次伤害。
    assert "失衡" in pc.build_hit_result(4)["effect"]
    assert "一半" in pc.build_hit_result(-1)["effect"]


def check_build_attack_result_and_overflow():
    # D = 成功数 − 闪避需求；D ≥ +2 才可购买附加效果，每 +1 次成功一项。
    assert pc.BUILD_OVERFLOW_MIN_D == 2
    assert len(pc.BUILD_OVERFLOW_OPTIONS) == 4
    out = pc.build_attack_result(5, 3)                  # D = +2
    assert out["d"] == 2 and out["result"] == "multi_hit"
    assert out["overflow"] == 2, out
    out = pc.build_attack_result(7, 3)                  # D = +4 → 贯穿
    assert out["result"] == "overrun" and out["hits"] == 3
    assert out["overflow"] == 4
    out = pc.build_attack_result(4, 3)                  # D = +1 → 不可购买
    assert out["overflow"] == 0 and out["result"] == "hit"
    out = pc.build_attack_result(2, 3)                  # D = −1 → 擦过
    assert out["result"] == "graze" and out["overflow"] == 0
    json.dumps(pc.build_attack_result(8, 4))            # 可直接落盘


# ── 超载连锁（02B 第七节 / data/system/build_overload_chain.json）──────

def check_build_overload_chain():
    assert pc.BUILD_OVERLOAD_CHAIN_LIMIT == 1
    assert pc.BUILD_OVERLOAD_CHAIN_MIN_MARGIN == 3
    assert len(pc.BUILD_OVERLOAD_CHAIN_OPTIONS) == 4
    # 触发：成功数 ≥ 需求两倍 **且** 盈余 ≥ 3，两个条件同时满足。
    assert pc.build_overload_chain_triggered(6, 3) is True    # 2×3=6，盈余 3
    assert pc.build_overload_chain_triggered(5, 3) is False   # 盈余 2
    assert pc.build_overload_chain_triggered(6, 4) is False   # 未达 2×4=8
    assert pc.build_overload_chain_triggered(8, 4) is True    # 2×4=8，盈余 4
    assert pc.build_overload_chain_triggered(7, 4) is False   # 未达 8
    assert pc.build_overload_chain_triggered(4, 1) is True    # 2×1=2，盈余 3
    assert pc.build_overload_chain_triggered(3, 1) is False   # 盈余 2
    assert pc.build_overload_chain_triggered(0, 0) is False
    # 每场景限一次：已用则不可再选。
    out = pc.build_overload_chain(6, 3)
    assert out["triggered"] and out["available"] and out["reason"] is None
    out = pc.build_overload_chain(6, 3, used_this_scene=True)
    assert out["triggered"] and not out["available"] and out["reason"]
    out = pc.build_overload_chain(5, 3)
    assert not out["triggered"] and not out["available"]
    json.dumps(pc.build_overload_chain(6, 3))


# ── 符纹插槽系统（02B 第八节 / data/system/build_glyph_slots.json）────

def check_build_glyph_slots():
    assert pc.BUILD_MAX_SLOTS_PER_FACULTY == 3
    assert len(pc.BUILD_SLOT_SOURCES) == 5
    assert len(pc.BUILD_GLYPH_SOURCES) == 3
    assert pc.BUILD_SLOT_PURCHASE_BP == 3
    assert pc.BUILD_SLOT_PURCHASE_MAX == 2
    assert pc.BUILD_STARTING_GLYPHS == 2
    assert "符纹名" in pc.BUILD_GLYPH_TEMPLATE
    # 插槽总数：起始 1；等级 4/7/10 各 +1；购买（上限 2）。
    assert pc.build_slot_total(1) == 1
    assert pc.build_slot_total(3) == 1
    assert pc.build_slot_total(4) == 2
    assert pc.build_slot_total(6) == 2
    assert pc.build_slot_total(7) == 3
    assert pc.build_slot_total(10) == 4
    assert pc.build_slot_total(10, purchased=2) == 6
    # 插槽池可超过单能力上限（每能力仍最多 3）。
    assert pc.build_slot_total(10, purchased=2) > pc.BUILD_MAX_SLOTS_PER_FACULTY
    for bad in (-1, 3):
        try:
            pc.build_slot_total(10, purchased=bad)
            raise AssertionError("购买的插槽个数越界应报错")
        except ValueError:
            pass


# ── 构建点与成长（02B 第九节 / data/system/build_growth.json）──────────

def check_build_growth_points():
    assert pc.BUILD_BP_PER_LEVEL == 2
    assert len(pc.BUILD_GROWTH_SPEND) == 12
    bps = dict((key, value[0]) for key, value in pc.BUILD_GROWTH_SPEND.items())
    assert bps == {
        "attribute": 3, "die_rank": 4, "new_skill": 2, "specialize": 2,
        "new_glyph": 2, "new_slot": 3, "faculty_level": 2, "new_faculty": 3,
        "focus_cap": 1, "stress_cap": 1, "momentum_start": 2,
        "crossover": 5}, bps
    assert len(pc.BUILD_STARTING_RESOURCES) == 6
    assert len(pc.BUILD_ROUTES) == 3
    names = [route[0] for route in pc.BUILD_ROUTES]
    assert names == ["「重炮」路线", "「织网」路线", "「共鸣」路线"], names
    # 累计 BP：1 级 0，每级 +2。
    assert pc.BUILD_STARTING_BP == 0
    assert pc.build_bp_total(1) == 0
    assert pc.build_bp_total(2) == 2
    assert pc.build_bp_total(5) == 8
    assert pc.build_bp_total(0) == 0
    assert pc.build_growth_cost("die_rank") == 4
    assert pc.build_growth_cost("crossover") == 5
    try:
        pc.build_growth_cost("teleport")
        raise AssertionError("未知花费项应报错")
    except ValueError:
        pass


# ── 符纹库（02B 第十节 / data/system/build_glyphs.json）────────────────

def check_build_glyph_library():
    assert len(pc.BUILD_GLYPHS) == 28
    assert pc.BUILD_GLYPH_CATEGORIES == (
        "增幅类", "效率类", "转化类", "触发类", "连锁类", "代价类")
    # 编号 1–28 连续无重复；id 唯一；六类各占数量。
    nos = [glyph["no"] for glyph in pc.BUILD_GLYPHS]
    assert nos == list(range(1, 29)), nos
    ids = [glyph["id"] for glyph in pc.BUILD_GLYPHS]
    assert len(set(ids)) == 28
    counts = {}
    for glyph in pc.BUILD_GLYPHS:
        counts[glyph["category"]] = counts.get(glyph["category"], 0) + 1
        assert glyph["level"] in ("I", "II", "III")
    assert counts == {"增幅类": 5, "效率类": 5, "转化类": 5, "触发类": 5,
                      "连锁类": 4, "代价类": 4}, counts
    # 逐枚抽样：名称 / 等级 / 效果 / 代价。
    sample = pc.build_glyph("assault")
    assert sample["name"] == "强袭符" and sample["level"] == "I"
    assert sample["effect"] == "伤害 +3" and sample["cost"] == "AP 消耗 +1"
    assert pc.build_glyph("命运符")["id"] == "fate"
    assert pc.build_glyph("resonance_detonate")["level"] == "III"
    assert len(pc.build_glyphs_by_category("连锁类")) == 4
    try:
        pc.build_glyphs_by_category("杂类")
        raise AssertionError("未知分类应报错")
    except ValueError:
        pass
    try:
        pc.build_glyph("不存在")
        raise AssertionError("未知符纹应报错")
    except ValueError:
        pass
    # 等级限制：I 任意 / II ≥ 4 / III ≥ 7。
    assert pc.BUILD_GLYPH_LEVEL_LIMITS == {"I": 1, "II": 4, "III": 7}
    assert pc.build_glyph_level_requirement("I") == 1
    assert pc.build_glyph_level_requirement("ii") == 4
    assert pc.build_glyph_level_requirement("III") == 7


def check_build_glyph_install_limit():
    # 同名 / 同类不可叠装；等级门槛。
    assert pc.build_can_install_glyph("assault", character_level=1)["allowed"]
    # II 级符纹需角色等级 ≥ 4。
    gate = pc.build_can_install_glyph("multi", character_level=3)
    assert not gate["allowed"] and "4" in gate["reason"]
    assert pc.build_can_install_glyph("multi", character_level=4)["allowed"]
    # III 级符纹需角色等级 ≥ 7。
    assert not pc.build_can_install_glyph(
        "fate", character_level=6)["allowed"]
    # 同名（同一 id）。
    same = pc.build_can_install_glyph("assault", installed=["assault"])
    assert not same["allowed"] and "同名" in same["reason"]
    # 同类（增幅类）：强袭符(增幅) + 广域符(增幅)。
    same = pc.build_can_install_glyph("area", installed=["assault"])
    assert not same["allowed"] and "同类" in same["reason"]
    # 不同类可共存：增幅类 + 效率类。
    ok = pc.build_can_install_glyph("frugal", installed=["assault"],
                                    character_level=1)
    assert ok["allowed"] and ok["reason"] is None


# ── 双引擎互转（02B 第十一节 / data/system/build_conversion.json）─────

def check_build_conversion_tables():
    assert len(pc.BUILD_ENGINE_SWITCH) == 5
    assert len(pc.BUILD_ATTRIBUTE_CONVERSION) == 5
    assert len(pc.BUILD_PROFICIENCY_CONVERSION) == 4
    assert len(pc.BUILD_DIFFICULTY_CONVERSION) == 8
    assert len(pc.BUILD_EDGE_CONVERSION) == 4
    assert len(pc.BUILD_ENEMY_DEFENSE_CONVERSION) == 5
    assert len(pc.BUILD_DAMAGE_CONVERSION) == 5
    assert len(pc.BUILD_MIGRATION_STEPS) == 5
    assert len(pc.BUILD_MIXING_LIMITS) == 3
    # 熟练转换：+2 → +1 枚；+4 → +2 枚且整体骰阶 +1。
    assert pc.BUILD_PROFICIENCY_CONVERSION[0][2] == "骰池 +1 枚"
    assert pc.BUILD_PROFICIENCY_CONVERSION[2][2] == \
        "骰池 +2 枚，且该骰池整体骰阶 +1"
    # 助势骰 ↔ 骰池：1/2/3 枚助势 → +1/+2/+3 枚；劣势 −1 枚。
    assert [row[1] for row in pc.BUILD_EDGE_CONVERSION] == [1, 2, 3, -1]
    # 属性枚数 = 属性值本身（与 build_pool 的 attribute 部分一致）。
    assert pc.build_pool(6, 0)["attribute"] == 6
    # 换引擎建议逐条。
    assert pc.build_engine_switch_advice(
        "大家觉得「每次都要数一堆骰子，太慢」") == "换战术引擎"
    try:
        pc.build_engine_switch_advice("随便")
        raise AssertionError("未记录的情形应报错")
    except ValueError:
        pass


def check_build_df_and_guard_conversion():
    # 难度转换：表内 DF 直接取档（bonus 0）。
    for df, need in ((6, 1), (8, 1), (10, 2), (12, 3), (14, 4),
                     (16, 5), (18, 6), (20, 8), (24, 10)):
        got = pc.build_df_to_need(df)
        assert got["need"] == need and got["bonus_die"] == 0, (df, got)
    # 中间 DF 取较高一档 + 1 枚额外骰（02B 第十一节 difficulty_note）。
    assert pc.BUILD_MID_DF_BONUS_DIE == 1
    for df, need in ((9, 2), (11, 3), (13, 4), (15, 5), (19, 8), (21, 10)):
        got = pc.build_df_to_need(df)
        assert got["need"] == need and got["bonus_die"] == 1, (df, got)
    for bad in (5, 25, 40):
        try:
            pc.build_df_to_need(bad)
            raise AssertionError("越界 DF 应报错：%d" % bad)
        except ValueError:
            pass
    # 敌人防御转换：Guard → 闪避需求。
    for guard, dodge in ((10, 2), (11, 2), (12, 3), (13, 3), (14, 4),
                         (15, 4), (16, 5), (17, 5), (18, 6), (30, 6)):
        assert pc.build_guard_to_dodge(guard) == dodge, guard
    try:
        pc.build_guard_to_dodge(9)
        raise AssertionError("低于表范围的 Guard 应报错")
    except ValueError:
        pass


def check_build_die_rate_derived():
    # d20（骰阶 V）按文档自身的阈值规则推导：16/20 = 80%（原来的 TODO 已定）。
    assert pc.build_die_rate(20) == 80
    assert pc.build_die_rate(6) == 33
    assert pc.build_die_rate(8) == 50
    assert pc.build_die_rate(10) == 60
    assert pc.build_die_rate(12) == 67
    try:
        pc.build_die_rate(100)
        raise AssertionError("表外骰面应报错")
    except ValueError:
        pass


def main():
    checks = (
        check_roll_whitelist,
        check_roll_edge_dice,
        check_judge_check_bands,
        check_attribute_and_proficiency_tables,
        check_resource_clamps,
        check_damage_tags,
        check_conditions,
        check_failure_cost,
        check_apply_failure_settlement,
        check_pressure_tension,
        check_check_notation,
        check_perform_action_integration,
        check_snapshot_serializable,
        # M2b（Issue #59）
        check_initiative_formula,
        check_attack_grade_boundaries,
        check_resolve_attack_grades,
        check_poise_and_break,
        check_boss_shields_and_phases,
        check_enemy_templates,
        check_encounter_budget,
        check_build_encounter,
        check_derived_values,
        check_condition_layers,
        check_downed_struggle,
        check_growth_system,
        check_combat_integration,
        # M2c（Issue #72）
        check_build_pool_composition,
        check_build_threshold_and_rates,
        check_build_required_successes,
        check_build_outcome_bands,
        check_build_roll_resolution,
        check_build_die_ranks,
        check_momentum_pool,
        check_stress_system,
        # M2d（Issue #122）
        check_build_attack_flow,
        check_build_hit_bands,
        check_build_attack_result_and_overflow,
        check_build_overload_chain,
        check_build_glyph_slots,
        check_build_growth_points,
        check_build_glyph_library,
        check_build_glyph_install_limit,
        check_build_conversion_tables,
        check_build_df_and_guard_conversion,
        check_build_die_rate_derived,
        # ATLAS I5（Issue #67）
        check_tactical_projection_bands,
        check_tactical_high_ground_and_attack,
        check_tactical_move_zones_abstract,
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

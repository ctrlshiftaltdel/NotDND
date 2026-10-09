#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prism_core 数值落地回归测试（Issue #53 M2a + Issue #59 M2b）。

M2a 覆盖：白名单拒绝、助势骰层数与取消、五档分档边界、灾难后果表、
资源夹取与专注过用、伤害标签修正、状态叠加与【疲惫】、张力曲线触发、
属性修正表与熟练量表、判定记法构造、perform_action 集成。

M2b 覆盖：先攻公式、攻击六档边界、擦过/暴击/重击数值、破韧与首领
阶段、敌体模板与词缀、遭遇预算与组成限制、派生值重算、状态叠层、
濒危三成/三败、经验升级与成长点、A 类 +4 上限、战斗集成。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_prism_core.py` 运行。
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import prism_core as pc


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

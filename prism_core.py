#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prism_core.py —— 「棱镜 PRISM」规则核心的组织骨架（M2 前置）

本模块只提供**组织**：函数切分、控制流、数据流与工程范式。
一切数值、公式与判定语义按 `docs/system/` 在 M2 逐项落地，
文件内以 `TODO(M2)` 标记并注明对应章节；`TODO(M3)` 指向落盘 / 并发 / HTTP 接线。

M2a（Issue #53）已落地：属性修正 / 熟练量表 / 助势骰（层数与取消）/
五档结果 / 资源夹取与专注过用 / 伤害标签（抗性 / 弱点 / 免疫 / 吸收）/
状态叠加与【疲惫】/ 灾难后果表 / 张力曲线触发。
源文档未写的数值一律不臆造，仍以 `TODO(M2)` / `TODO(M2b)` 标注。

设计约定
  · 零第三方依赖（仅 Python 3 标准库）；`import prism_core` **无副作用**。
  · 所有状态都是纯数据（dict / list / 基本类型），可直接 JSON 序列化：
    跨请求的回合状态必须能落盘，不能只放在内存里（见 RuleSession）。
  · 规则函数只返回结构化结果；叙事文案归调用方（HTTP 层 / 导引者模块）。
  · 命名已全部 PRISM 化：六维属性 = 力道 MGT / 灵巧 FIN / 体魄 VIG /
    洞察 INS / 心智 MND / 气场 PRE。

章节地图（骨架 ↔ 本模块小节）
  一、术语与显式白名单      二、掷骰器
  三、会话状态契约          四、结算的单一进出口
  五、场景行动单入口        六、战斗状态机
  七、派生值与明细          八、成长
"""

from __future__ import annotations

import copy
import random
import re

# ════════════════════════════════════════════════════════════════════════
# 【权限边界】玩家意图 vs 服务端结算
#
# 这是规则核心的边界契约。新增任何入口之前先过这一关。
#
# ── 玩家侧（可以有 HTTP 入口）只表达「我想做什么」────────────────────
#   · 选择场景行动 / 选择战斗动作 / 指定目标
#   · 输入自由文本（由导引者模块裁定后，仍回到本模块结算）
#   · 在成长时消费**服务端已经发放**的点数（分配，不是发放）
#   玩家侧永不提交结果：骰值、伤害、命中、资源增减、等级与经验的数值
#   一律不接受——传进来也只会被忽略或直接报错。
#
# ── 结算侧（只有内部函数，没有玩家入口）─────────────────────────────
#   · 掷骰与成败判定            perform_action / resolve_check
#   · 伤害与濒危                deal_damage（**唯一伤害出口**）
#   · 活力恢复                  heal_unit（**唯一恢复出口**）
#   · 资源增减                  change_resource（唯一资源出口）
#   · 失败代价                  apply_failure（场景行动与自由输入共用）
#   · 战斗推进与胜负            _advance / _end_combat / combat_abandon
#   · 经验与成长点数发放        award_xp（发放）；assign_attribute（消费）
#   · 战斗内临时增益清理        _clear_combat_buffs（所有战斗出口都走）
#
#   绕过这些函数、直接写 `unit["vitality"]` / `unit["resources"]` 之类的
#   改动都是**旁路**：新增路径若绕开单一进出口，应视为缺陷而不是风格问题。
#
# ── 显式清单 ─────────────────────────────────────────────────────────
#   所有外部输入先过白名单：六维属性、资源池、行动类别、战斗动作、
#   装备槽位。清单之外的输入一律 raise，不做「尽力猜测」。
# ════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════
# 一、术语与显式白名单
# ════════════════════════════════════════════════════════════════════════

# 六维属性：产品层统一使用 PRISM 名（docs/system/01 第一节）。
ATTRIBUTES = {
    "MGT": {"zh": "力道", "en": "Might"},
    "FIN": {"zh": "灵巧", "en": "Finesse"},
    "VIG": {"zh": "体魄", "en": "Vigor"},
    "INS": {"zh": "洞察", "en": "Insight"},
    "MND": {"zh": "心智", "en": "Mind"},
    "PRE": {"zh": "气场", "en": "Presence"},
}
ATTRIBUTE_IDS = tuple(ATTRIBUTES)
ATTRIBUTE_MIN = 1
ATTRIBUTE_MAX = 10

# 防护基准：`防护 = 10 + 护甲 Guard 值 + 态势修正 + 掩体修正`（docs/system/01 第二节）。
GUARD_BASE = 10

# 四大资源池（docs/system/01 第十二节）。
RESOURCE_POOLS = ("focus", "tempo", "strain", "resolve")

# 场景行动的两类：纯叙述 / 需要判定（行动数据用 kind 字段声明）。
ACTION_KINDS = ("narrate", "check")

# 战斗动作白名单：玩家在战斗里能选的动作只有这些（docs/system/01 第六节、
# data/system/action_economy.json 的动作表）。别在这里加「指定伤害」——
# 那是裁定权，不是玩家动作。
COMBAT_ACTIONS = (
    "move", "stance_switch", "assist_action", "basic_attack",
    "full_ability", "quick_ability", "suppress", "recon",
    "assist", "reaction",
)

# 装备槽位（角色卡模板：主武器 / 副手 / 护甲 / 随身）。
EQUIP_SLOTS = (
    ("main", "主武器"),
    ("off", "副手"),
    ("armor", "护甲"),
    ("carry", "随身"),
)

# 判定结果档位（docs/system/02A 第三节 / 02B 第二节 的五档命名）。
OUTCOMES = ("triumph", "success", "narrow", "failure", "catastrophe")
_OUTCOME_ZH = {
    "triumph": "凯旋",
    "success": "成功",
    "narrow": "险成",
    "failure": "挫败",
    "catastrophe": "灾难",
}
# 未达成档位：这些结果需要结算失败代价。
_FAILED_OUTCOMES = ("failure", "catastrophe")

# ── 属性修正表（docs/system/01 第一节「属性修正（战术引擎专用）」）────────
# 属性值 1 → −2；2–3 → −1；4–5 → +0；6–7 → +1；8–9 → +2；10 → +3。
# 与 data/system/attributes.json 的 modifiers 字段一一对应。
ATTRIBUTE_MODIFIER_RANGES = ((1, 1, -2), (2, 3, -1), (4, 5, 0),
                             (6, 7, 1), (8, 9, 2), (10, 10, 3))

# ── 熟练加值量表（docs/system/01 第十四节「熟练加值量表」）───────────────
# 等级 1–4 → +2；5–8 → +3；9–12 → +4。
PROFICIENCY_LEVELS = ((1, 4, 2), (5, 8, 3), (9, 12, 4))

# ── 资源池上下限（docs/system/01 第十二节）──────────────────────────────
STRAIN_MAX = 6     # 负担：从 0 到 6 累加
RESOLVE_MAX = 5    # 决意：起始 3 点，上限 5 点

# ── 张力曲线（docs/system/04 第十二节 / data/system/tension.json）────────
# 张力值是 0–10 的隐形数值；场景压力计（session.pressure）按同一刻度夹取。
PRESSURE_MAX = 10
TENSION_BANDS = (
    {"id": "relaxed", "name": "松弛", "low": 0, "high": 2,
     "scene": "休整、日常、交易"},
    {"id": "mild", "name": "温和", "low": 3, "high": 4,
     "scene": "探索、情报收集"},
    {"id": "tense", "name": "紧绷", "low": 5, "high": 6,
     "scene": "潜入、交涉、追查"},
    {"id": "high", "name": "高压", "low": 7, "high": 8,
     "scene": "火力战斗、限时拯救"},
    {"id": "extreme", "name": "顶点", "low": 9, "high": 10,
     "scene": "生死抉择、核心揭示、Boss 决战"},
)

# ── 状态白名单（data/system/conditions.json / docs/system/01 第五节）─────
# 减益：3 个及以上叠加时触发【疲惫】（01 第五节叠加规则 3）。
DEBUFF_CONDITIONS = ("失衡", "束缚", "麻痹", "恐惧", "流血", "中毒",
                     "迟滞", "沉默", "破绽", "负担过重", "熵染")
# 增益 + 派生（疲惫 / 过载中）：conditions.json buffs 与 derived 两节。
_BUFF_CONDITIONS = ("加速", "护持", "隐匿", "专注", "再生")
_DERIVED_CONDITIONS = ("疲惫", "过载中")
KNOWN_CONDITIONS = DEBUFF_CONDITIONS + _BUFF_CONDITIONS + _DERIVED_CONDITIONS


def attribute_modifier(value: int) -> int:
    """属性值 → 战术引擎修正（docs/system/01 第一节量表）。

    构建引擎不使用本表（直接用属性值作骰池枚数，见 docs/system/02B）。
    """
    value = int(value or 0)
    for low, high, modifier in ATTRIBUTE_MODIFIER_RANGES:
        if low <= value <= high:
            return modifier
    raise ValueError(
        f"属性值必须在 {ATTRIBUTE_MIN}–{ATTRIBUTE_MAX} 之间（当前 {value}）")


def proficiency_bonus(level: int) -> int:
    """等级 → 熟练加值（docs/system/01 第十四节「熟练加值量表」）。"""
    level = 1 if level is None else int(level)
    for low, high, bonus in PROFICIENCY_LEVELS:
        if low <= level <= high:
            return bonus
    raise ValueError(f"等级必须在 1–12 之间（当前 {level}）")


def register_condition(name: str, *, debuff: bool = False) -> None:
    """登记世界模组扩展状态（docs/system/01 第五节【可替换】）。

    状态名之外的白名单由世界模组在装载时扩充；未登记的名字在
    add_condition 处直接 raise，不做「尽力猜测」。
    """
    global KNOWN_CONDITIONS, DEBUFF_CONDITIONS
    name = str(name or "").strip()
    if not name:
        raise ValueError("状态名不能为空")
    if name in KNOWN_CONDITIONS:
        return
    KNOWN_CONDITIONS = KNOWN_CONDITIONS + (name,)
    if debuff:
        DEBUFF_CONDITIONS = DEBUFF_CONDITIONS + (name,)

# ════════════════════════════════════════════════════════════════════════
# 二、掷骰器
# ════════════════════════════════════════════════════════════════════════

# 工程护栏：规模上限只防「超大输入把服务打挂」，不是游戏数值。
DICE_MAX_COUNT = 40
DICE_MAX_SIDES = 1000

# 判定记法的占位默认值；真正的记法由行动数据或 M2 的判定层给出。
DEFAULT_CHECK_NOTATION = "1d20"

# 白名单：`NdM` 或 `NdM±K`。拒绝一切自由记法（例如嵌套表达式、多次掷骰）。
_NOTATION_RE = re.compile(r"^(\d{0,3})d(\d{1,3})([+-]\d{1,3})?$", re.IGNORECASE)

# 助势骰（docs/system/02A 第二节 / data/system/tactics_resolution.json
# edge_dice）：每层优势额外掷一枚 d6；上限 3 层；超出部分每层改为 +2
# 固定加值。劣势每层掷一枚 d6 并**减去**，同样最多 3 层（超出直接截断）。
EDGE_DIE_SIDES = 6
EDGE_MAX_LAYERS = 3
EDGE_BEYOND_FLAT = 2


def cancel_edge_layers(advantage: int, disadvantage: int) -> int:
    """优势与劣势互相抵消（不各自计算），返回净层数（负数 = 净劣势）。

    docs/system/02A 第二节：「当一个判定同时有优势和劣势时，它们互相
    抵消（而不是各自计算），这是为了桌上的速度。」
    """
    return int(advantage or 0) - int(disadvantage or 0)


def roll(notation: str, *, rng=None, advantage: int = 0,
         disadvantage: int = 0) -> dict:
    """按白名单记法掷骰，返回结构化结果；不合法时抛 ValueError。

    返回 {notation, count, sides, dice, modifier, total, detail, natural, edge}：
      · dice     —— 每颗骰的原始面值（按掷出顺序）
      · total    —— 面值之和 + 修正 + 助势骰总和
      · natural  —— 单颗骰时的原始面值（多颗为 None），供判定层使用
      · edge     —— 助势骰明细 {advantage, disadvantage, net, dice, flat, sum}
      · detail   —— 人类可读明细（叙事流 / 界面用）

    助势骰按 docs/system/02A 第二节落地：净层数 = 优势 − 劣势（先抵消）；
    净优势 ≤ 3 层每层 +1d6，超出部分每层 +2 固定加值；净劣势每层掷 d6
    并减去（最多 3 层）。记法本身仍走白名单，助势骰不进记法。
    `rng` 只为测试注入确定性随机（默认 Python 标准库的 random）。
    """
    text = (notation or "").strip().replace(" ", "")
    matched = _NOTATION_RE.match(text)
    if not matched:
        raise ValueError(f"不支持的骰点记法：{notation}")
    count = int(matched.group(1) or 1)
    sides = int(matched.group(2))
    if count < 1 or sides < 1:
        raise ValueError("骰子数量与面值必须为正")
    if count > DICE_MAX_COUNT or sides > DICE_MAX_SIDES:
        raise ValueError("骰子规模过大")
    modifier = int(matched.group(3)) if matched.group(3) else 0

    source = rng or random
    values = [int(source.randint(1, sides)) for _ in range(count)]

    net = cancel_edge_layers(advantage, disadvantage)
    layers = min(abs(net), EDGE_MAX_LAYERS)
    flat = 0
    if net > 0:
        flat = max(0, abs(net) - EDGE_MAX_LAYERS) * EDGE_BEYOND_FLAT
    edge_values = [int(source.randint(1, EDGE_DIE_SIDES)) for _ in range(layers)]
    if net > 0:
        edge_sum = sum(edge_values) + flat
    elif net < 0:
        edge_sum = -sum(edge_values)
    else:
        edge_sum = 0
    edge = {"advantage": int(advantage or 0), "disadvantage": int(disadvantage or 0),
            "net": net, "dice": edge_values, "flat": flat, "sum": edge_sum}

    label = f"{count} 颗 d{sides}" if count > 1 else f"1 颗 d{sides}"
    detail = f"{label}：{values} → {sum(values)}"
    if modifier:
        detail += f"，修正 {modifier:+d}"
    if net > 0:
        detail += f"，助势 {net} 层：{edge_values or '—'}"
        if flat:
            detail += f" + 超层固定 {flat:+d}"
    elif net < 0:
        detail += f"，劣势 {abs(net)} 层：−{edge_values or '—'}"
    total = sum(values) + modifier + edge_sum
    return {
        "notation": text,
        "count": count,
        "sides": sides,
        "dice": values,
        "modifier": modifier,
        "total": total,
        "detail": detail,
        "natural": values[0] if count == 1 else None,
        "edge": edge,
    }


# ════════════════════════════════════════════════════════════════════════
# 三、会话状态契约
# ════════════════════════════════════════════════════════════════════════


def new_unit(unit_id: str, name: str, *, attributes: dict | None = None) -> dict:
    """创建一个单位（角色 / 敌体）的最小数据卡。

    只放规则核心用得到的字段；职途、技能、能力、装备目录等数据由 M2
    接入（docs/system/02A/02B、data/system/*.json）。属性默认 4 =
    普通成年人水平（docs/system/01 第一节）。
    """
    attrs = {key: 4 for key in ATTRIBUTE_IDS}
    for key, value in (attributes or {}).items():
        if key not in ATTRIBUTES:
            raise ValueError(f"未知属性：{key}")
        value = int(value)
        if not (ATTRIBUTE_MIN <= value <= ATTRIBUTE_MAX):
            raise ValueError(
                f"属性 {key} 必须在 {ATTRIBUTE_MIN}–{ATTRIBUTE_MAX} 之间")
        attrs[key] = value
    return {
        "id": str(unit_id),
        "name": str(name),
        "level": 1,
        "xp": 0,
        "growth_points": 0,
        "attributes": attrs,
        "vitality": 0,
        "max_vitality": 0,
        "guard": 0,
        "downed": False,          # 濒危标记（docs/system/01 第十一节）
        "downed_fails": 0,        # 濒危挣扎失败计数（01 第十一节：受击 +1，暴击 +2）
        "dead": False,
        "conditions": [],
        "resources": {pool: 0 for pool in RESOURCE_POOLS},
        "damage_relations": {},   # {resistance/weakness/immunity/absorb: [伤害标签]}
        "equipment": {},
        "stance": "",
        "cover": "",
    }


class RuleSession:
    """规则核心的最小会话状态切片。

    字段刻意保持 JSON 可序列化：战斗状态、行动去重表与失败计数都必须
    能随存档落盘——跨请求的回合状态不能只放内存，刷新页面就丢。

    TODO(M3)：完整会话 / 存档层（M3 的 HTTP 与存档实现）接管落盘（原子写、
    加载迁移）与并发锁；本类的字段名应被复用或无损扩展，不要在别处
    另起一套。会话标识沿用 `sid`，与存档层的键保持一致。
    """

    def __init__(self, session_id: str = ""):
        self.sid = str(session_id)
        self.party: list[dict] = []
        self.scene: dict = {"id": "", "actions": []}
        self.active_unit_id = ""
        self.log: list[dict] = []
        self.done_actions: dict = {}     # 已完成的场景行动（成功一次即锁）
        self.action_fails: dict = {}     # {行动 id: 累计失败次数}
        self.pressure = 0                # 场景压力计（见 tick_pressure）
        self.combat: dict | None = None
        self.dirty = False               # M3 存档层据此判断是否需要写盘

    def add_log(self, kind: str, text: str, *, speaker: str = "",
                extra: dict | None = None) -> dict:
        entry = {"kind": str(kind), "text": str(text), "speaker": str(speaker)}
        if extra:
            entry["extra"] = dict(extra)
        self.log.append(entry)
        self.dirty = True
        return entry

    def touch(self) -> None:
        """标记状态已变化（M3 存档层读它决定是否写盘）。"""
        self.dirty = True

    def snapshot(self) -> dict:
        """返回 JSON 可序列化的整会话快照（深拷贝，改快照不影响现场）。

        TODO(M3)：真正的落盘（写 .tmp 再原子替换、加载时的惰性迁移）
        由存档层实现；本函数只保证状态本身是纯数据。
        """
        return {
            "sid": self.sid,
            "party": copy.deepcopy(self.party),
            "scene": copy.deepcopy(self.scene),
            "active_unit_id": self.active_unit_id,
            "log": copy.deepcopy(self.log),
            "done_actions": copy.deepcopy(self.done_actions),
            "action_fails": copy.deepcopy(self.action_fails),
            "pressure": self.pressure,
            "combat": copy.deepcopy(self.combat),
        }

    @classmethod
    def from_snapshot(cls, data: dict) -> "RuleSession":
        """从 snapshot() 的结构恢复会话（M3 加载路径的骨架）。"""
        data = data or {}
        session = cls(str(data.get("sid") or ""))
        session.party = list(data.get("party") or [])
        session.scene = dict(data.get("scene") or {"id": "", "actions": []})
        session.active_unit_id = str(data.get("active_unit_id") or "")
        session.log = list(data.get("log") or [])
        session.done_actions = dict(data.get("done_actions") or {})
        session.action_fails = dict(data.get("action_fails") or {})
        session.pressure = int(data.get("pressure") or 0)
        session.combat = data.get("combat")
        return session


def _unit_view(unit: dict | None) -> dict:
    """单位的只读视图：白名单字段 + 深拷贝，不把内部引用交给调用方。"""
    if not unit:
        return {}
    fields = ("id", "name", "level", "vitality", "max_vitality", "guard",
              "downed", "dead", "conditions", "resources", "attributes")
    return {key: copy.deepcopy(unit.get(key)) for key in fields}


# ════════════════════════════════════════════════════════════════════════
# 四、结算的单一进出口
# ════════════════════════════════════════════════════════════════════════


def deal_damage(session: RuleSession, unit: dict | None, amount: int,
                *, label: str = "", tag: str = "", crit: bool = False) -> dict:
    """**唯一的伤害出口**：标签修正 → 扣活力 → 濒危判定。

    检定失败同样能把人打到濒危，所以失败代价也走这里（而不是直接改
    vitality）——绕过它就会出现「活力归零但濒危标记没挂上」的状态。

    `tag` 为伤害标签（docs/system/01 第四节：斩击 / 钝击 / 火焰……），
    按目标的 damage_relations 应用抗性 / 弱点 / 免疫 / 吸收；
    `crit` 表示本次伤害为暴击（攻击层判定，M2b 接线）：濒危单位受击的
    失败计数 +2，普通受击 +1（docs/system/01 第十一节 damage_escalation）。

    返回 {vitality, lost, downed, dead, raw, modified, label, relation,
    healed}。叙事文案归调用方。
    """
    out = {
        "vitality": int((unit or {}).get("vitality") or 0),
        "lost": 0,
        "downed": bool((unit or {}).get("downed")),
        "dead": bool((unit or {}).get("dead")),
        "raw": max(0, int(amount or 0)),
        "modified": False,
        "relation": "",
        "healed": 0,
        "label": str(label or ""),
    }
    if unit is None:
        return out

    was_downed = out["downed"]
    tags = _apply_damage_tags(unit, amount, tag)
    out["modified"] = bool(tags["modified"])
    out["relation"] = tags["relation"]
    effective = int(tags["amount"])

    if tags["relation"] == "absorb":
        # 吸收：该伤害类型反而恢复等量活力（docs/system/01 第十一节）。
        before = int(unit.get("vitality") or 0)
        cap = int(unit.get("max_vitality") or 0)
        healed = before + effective
        if cap > 0:
            healed = min(cap, healed)
        unit["vitality"] = max(0, healed)
        out["vitality"] = int(unit["vitality"])
        out["healed"] = int(unit["vitality"]) - before
        session.touch()
        return out

    before = int(unit.get("vitality") or 0)
    unit["vitality"] = max(0, before - max(0, effective))
    out["vitality"] = int(unit["vitality"])
    out["lost"] = before - int(unit["vitality"])

    # 刚跌到 0 及以下：进入濒危。已经不是濒危的再次受击不改标记，
    # 但按 01 第十一节推进濒危挣扎的失败计数（暴击 +2）。注意濒危单位
    # 活力已夹在 0，不能拿 lost 判断——只要这次命中真造成了伤害
    # （有效伤害 > 0）就计数。
    if int(unit["vitality"]) <= 0 and not out["downed"]:
        unit["downed"] = True
    out["downed"] = bool(unit.get("downed"))
    out["dead"] = bool(unit.get("dead"))
    if out["downed"] and was_downed and effective > 0:
        unit["downed_fails"] = int(unit.get("downed_fails") or 0) \
            + (2 if crit else 1)
    # 弱点：伤害 +50% 之外，额外造成 1 层【失衡】（01 第十一节）。
    if tags["relation"] == "weakness" and effective > 0:
        add_condition(session, unit, "失衡")
    session.touch()
    return out


def _apply_damage_tags(unit: dict, amount: int, tag: str) -> dict:
    """伤害标签的修正（docs/system/01 第十一节 / data/system/damage.json）。

    目标在 `damage_relations` 中声明与标签的关系：
      · immunity 免疫：伤害无效 → 0；
      · absorb 吸收：反而恢复等量活力 → 原值返回，relation=absorb；
      · weakness 弱点：伤害 +50%（向下取整），另由 deal_damage 施加【失衡】；
      · resistance 抗性：伤害减半（向下取整）。
    同一标签命中多条关系时按 免疫 > 吸收 > 弱点 > 抗性 取第一条；
    未声明关系或未带标签时原值返回。
    """
    amount = max(0, int(amount or 0))
    tag = str(tag or "").strip()
    relations = (unit or {}).get("damage_relations") or {}
    if not tag or not isinstance(relations, dict):
        return {"amount": amount, "relation": "", "modified": False}

    def _listed(key: str) -> bool:
        entries = relations.get(key) or []
        return any(str(entry) == tag for entry in entries)

    if _listed("immunity"):
        return {"amount": 0, "relation": "immunity", "modified": True}
    if _listed("absorb"):
        return {"amount": amount, "relation": "absorb", "modified": True}
    if _listed("weakness"):
        return {"amount": amount + amount // 2, "relation": "weakness",
                "modified": True}
    if _listed("resistance"):
        return {"amount": amount // 2, "relation": "resistance",
                "modified": True}
    return {"amount": amount, "relation": "", "modified": False}


def heal_unit(session: RuleSession, unit: dict | None, amount: int) -> int:
    """**唯一的活力恢复出口**，返回实际恢复量。

    治疗路径（药剂、休息、医疗、能力）都必须走这里，漏一条就会出现
    「活力回满了但濒危标记还挂着」这类不一致。

    TODO(M2)：濒危的解除条件（挣扎 / 医疗 / 恢复至 0 以上是否立即脱离）
    按 docs/system/01 第十一节落地；在语义落地前本函数只做数值夹取，
    不改动濒危标记。
    """
    amount = max(0, int(amount or 0))
    if unit is None or not amount:
        return 0
    before = int(unit.get("vitality") or 0)
    cap = int(unit.get("max_vitality") or 0)
    healed = before + amount
    if cap > 0:
        healed = min(cap, healed)
    unit["vitality"] = max(0, healed)
    got = int(unit["vitality"]) - before
    session.touch()
    return got


def change_resource(session: RuleSession, unit: dict | None, pool: str,
                    delta: int) -> dict:
    """**唯一资源进出口**：正数获得、负数消耗。

    返回 {pool, changed, left, out, overused}；`out` 表示该池已见底；
    `overused` 表示本次触发了专注过度使用。

    上下限按 docs/system/01 第十二节与 data/system/resources.json 落地：
      · 负担 strain：0–6（STRAIN_MAX）；决意 resolve：上限 5（RESOLVE_MAX）；
      · 专注 focus / 气势 tempo 的上限依赖职途加成 / 队伍人数，本层无法
        独立推导，沿用 unit["max_resources"] 里调用方提供的上限；
      · 任何池不见负数（下限 0）。
    专注过度使用（01 第十二节）：专注归零后仍可过度使用，每次引发
    1 层【疲惫】并获得 1 点负担——本函数按「一次消耗请求超出当前余量」
    判定一次过用（能力层 M2b 接线后同样经由本出口生效）。
    """
    pool = str(pool or "")
    if pool not in RESOURCE_POOLS:
        raise ValueError(f"未知资源池：{pool}")
    delta = int(delta or 0)
    out = {"pool": pool, "changed": 0, "left": 0, "out": False,
           "overused": False}
    if unit is None or delta == 0:
        if unit is not None:
            out["left"] = int((unit.get("resources") or {}).get(pool, 0) or 0)
            out["out"] = out["left"] <= 0
        return out

    resources = unit.setdefault("resources", {})
    current = int(resources.get(pool, 0) or 0)

    # 专注过度使用：请求的消耗超出余量（含余量已为 0 仍在花费）。
    if pool == "focus" and delta < 0 and current + delta < 0:
        out["overused"] = True
        add_condition(session, unit, "疲惫")
        if change_resource(session, unit, "strain", 1)["changed"]:
            pass  # 负担 +1 已由递归调用结算（上限 6 内）

    new = current + delta
    if pool == "strain":
        new = min(new, STRAIN_MAX)
    elif pool == "resolve":
        new = min(new, RESOLVE_MAX)
    else:
        caps = unit.get("max_resources") or {}
        cap = caps.get(pool)
        if cap is not None:
            new = min(new, int(cap))
    new = max(0, new)
    resources[pool] = new
    out.update(changed=new - current, left=new, out=(new <= 0))
    session.touch()
    return out


def add_condition(session: RuleSession, unit: dict | None, name: str) -> bool:
    """给单位施加一个状态，返回是否新加。

    叠加规则按 docs/system/01 第五节与 data/system/conditions.json：
      · 同一种状态不叠加数值，只取最强一档——同名重复施加返回 False
        （持续时间追踪属回合推进，见 TODO(M2b)）；
      · 同时拥有 3 个及以上减益状态时，自动额外获得【疲惫】。
    状态名走白名单（KNOWN_CONDITIONS，来自 conditions.json）；世界模组
    扩展状态须经 register_condition 登记，未登记的名字直接 raise。
    TODO(M2b)：标注「N 层」的状态（叠至 3 层升级为完整效果）与
    【创伤】层数效应（活力上限 −5 等）随状态数据接入落地。
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("状态名不能为空")
    if name not in KNOWN_CONDITIONS:
        raise ValueError(
            f"未知状态：{name}（白名单见 data/system/conditions.json；"
            "世界模组扩展状态须经 register_condition 登记）")
    if unit is None:
        return False
    conditions = unit.setdefault("conditions", [])
    if name in conditions:
        return False
    conditions.append(name)
    if name in DEBUFF_CONDITIONS:
        active = [item for item in conditions if item in DEBUFF_CONDITIONS]
        if len(active) >= 3 and "疲惫" not in conditions:
            conditions.append("疲惫")
    session.touch()
    return True


# ── 灾难后果表（docs/system/02A 第三节「灾难后果表」，d20）──────────────
# 与 data/system/tactics_resolution.json 的 catastrophe_table.rows 一致；
# 供导引者一时想不出灾难怎么写时掷一次。机械可结算的行见
# _CATASTROPHE_MECHANICAL，其余行是叙事后果，由导引者模块演出。
CATASTROPHE_TABLE = (
    "你的武器／工具损坏或脱手，落入难以取回的位置",
    "你发出巨响，引来了不必要的注意",
    "你伤到了自己（1d6 伤害）或伤到了同伴",
    "你触发了场景中的一个负面场地要素",
    "关键情报被误导了：你得出一个错误的结论",
    "你失去了高地／位置优势，被压制在不利地形",
    "你损坏了一个重要的第三方物件（需赔偿或解释）",
    "你被卷入下一个危险空间（掉进下一层、被拖进裂缝、或落入包围圈）",
    "你暴露了身份／意图，对方开始针对你采取措施",
    "你累计 1 点负担",
    "你错过了时机，机会窗口关闭（可能需要另想办法）",
    "你的盟友／雇主对你的能力产生怀疑，声望 −1",
    "敌人获得了你身上的一个重要物品",
    "你被困、被压或被卡住，需要一次行动才能脱身",
    "一个无关者被卷入并受伤，道德成本产生",
    "环境被永久改变，从此这条路线不可用",
    "你触发了某个伏笔（导引者把计划中的麻烦提前）",
    "你让一个敌人获得了针对你的针对性增益（下次针对你 +1 枚助势骰）",
    "你的行动反而帮助了对手（导引者说明为何）",
    "双重灾难：再掷一次本表，两个后果同时发生",
)
# 行号 → 机械结算钩子：第 3 行 1d6 活力伤害；第 10 行负担 +1。
_CATASTROPHE_MECHANICAL = {3: "vitality", 10: "strain"}


def failure_cost(df: int, *, info_only: bool = False,
                 catastrophe: bool = False, rng=None) -> dict:
    """一次失败按难度值折算出的代价（结构化，不做结算）。

    按源文档现状（issue #53 的边界要求：源文档没写的不臆造）：
      · 挫败 / 灾难的「按 DF 折算资源、活力」常规代价曲线**源文档未写**
        ——docs/system/01 第五节 / 第十二节只有负担的来源与档位效应、
        专注过用规则，docs/system/03 没有数值化的失败代价曲线，
        因此常规代价返回全零（见 TODO(M2)，落地前不猜测数值）。
      · 灾难（catastrophe=True，由调用方按五档结果传入）按
        docs/system/02A 第三节「灾难后果表」掷 d20：
        第 3 行 = 1d6 活力伤害（info_only 的信息型行动不用活力买情报，
        跳过活力项）、第 10 行 = 负担 +1；其余行只回叙事文本。
    `info_only` 表示信息型行动——不该用活力去买情报。
    """
    cost = {"focus": 0, "vitality": 0, "strain": 0, "conditions": []}
    if catastrophe:
        source = rng or random
        row = int(source.randint(1, 20))
        cost["catastrophe_row"] = row
        cost["catastrophe_text"] = CATASTROPHE_TABLE[row - 1]
        if _CATASTROPHE_MECHANICAL.get(row) == "vitality" and not info_only:
            cost["vitality"] = int(source.randint(1, 6))
        elif _CATASTROPHE_MECHANICAL.get(row) == "strain":
            cost["strain"] = 1
    return cost


def apply_failure(session: RuleSession, unit: dict | None, df: int,
                  label: str = "", *, info_only: bool = False,
                  catastrophe: bool = False, rng=None) -> dict:
    """结算一次失败的代价。**场景行动与自由输入共用这一个出口。**

    两条路径必须付出完全相同的代价：如果自由输入只推压力不掉资源，
    它就成了一条零风险旁路——玩家可以无限重试同一个想法，把场景行动
    的代价体系整个绕过去。
    """
    cost = failure_cost(df, info_only=info_only, catastrophe=catastrophe,
                        rng=rng)
    out = {"cost": dict(cost), "focus": None, "strain": None, "damage": None}
    if unit is None:
        return out
    focus_cost = int(cost.get("focus") or 0)
    if focus_cost:
        out["focus"] = change_resource(session, unit, "focus", -focus_cost)
    strain_cost = int(cost.get("strain") or 0)
    if strain_cost:
        out["strain"] = change_resource(session, unit, "strain", strain_cost)
    for name in cost.get("conditions") or []:
        add_condition(session, unit, name)
    vitality_cost = int(cost.get("vitality") or 0)
    if vitality_cost:
        out["damage"] = deal_damage(session, unit, vitality_cost,
                                    label=label or "失败代价")
    return out


def tension_band(value: int) -> dict:
    """张力值 → 五档之一（docs/system/04 第十二节 / data/system/tension.json）。

    返回 TENSION_BANDS 中的档位 dict（含 id / name / low / high / scene），
    另附 rank（0–4，用于比较升降）。
    """
    value = max(0, min(PRESSURE_MAX, int(value or 0)))
    for rank, band in enumerate(TENSION_BANDS):
        if band["low"] <= value <= band["high"]:
            return {**band, "rank": rank}
    raise ValueError(f"张力值 {value} 不在任何档位内")


def tick_pressure(session: RuleSession, step: int = 1) -> dict:
    """推进场景压力计，返回 {fired, pressure, event}。

    失败要真的改变局势，就必须有一个所有路径共用的推进器：
    只在某一条路径上推，其他路径就白嫖。压力计按 docs/system/04
    第十二节的张力刻度（0–10）夹取；事件触发判据见 _pressure_event_due。
    """
    old = int(session.pressure or 0)
    session.pressure = min(PRESSURE_MAX, old + max(0, int(step or 0)))
    new = session.pressure
    out = {"fired": False, "pressure": new, "event": None}
    if new == old:
        return out
    session.touch()
    if not _pressure_event_due(old, new):
        return out
    out["fired"] = True
    out["event"] = _fire_pressure_event(session)
    return out


def _pressure_event_due(old: int, new: int) -> bool:
    """压力事件触发判据（幂等：跨越多个阈值也只算一次）。

    按 docs/system/04 第十二节张力曲线：只有当压力**升入更高一档**
    （松弛 → 温和 → 紧绷 → 高压 → 顶点）时触发一次；同档内推进、
    一次跨多档都只算一次。
    """
    return tension_band(new)["rank"] > tension_band(old)["rank"]


def _fire_pressure_event(session: RuleSession) -> dict | None:
    """压力事件的内容与后果。

    数值部分（档位、当前压力）由规则层给出；升温信号的具体内容与
    场景恶化事件的叙事（docs/system/04 第十二节升温信号、
    data/system/tension.json heat_signals）由导引者模块挑选与演出，
    规则层不越权写文案。TODO(M3)：导引者模块接线后在此挂事件钩子。
    """
    band = tension_band(session.pressure)
    text = f"局势张力升级：{band['name']}（{band['scene']}）。"
    session.add_log("system", text, speaker="局势")
    return {"band": band["id"], "name": band["name"],
            "pressure": int(session.pressure), "text": text}


# ── 风险池（docs/system/03 第一节 / data/system/risk_pool.json）──────────
# 移动结束掷骰：标准 d6 掷出 6 触发；安全区域 d8（只有 8 触发）；
# 危险区域 d4（只有 4 触发）。标记的放入与清空、掷骰时机归移动层
# （TODO(M3) 会话 / 移动接线），本层只提供一次纯判定的数值。
RISK_POOL_ZONES = {"safe": (8, 8), "standard": (6, 6), "dangerous": (4, 4)}


def risk_roll(zone: str = "standard", *, rng=None) -> dict:
    """风险池的一次触发判定（纯函数，不碰会话状态）。"""
    if zone not in RISK_POOL_ZONES:
        raise ValueError(f"未知区域类型：{zone}")
    sides, face = RISK_POOL_ZONES[zone]
    value = int((rng or random).randint(1, sides))
    return {"zone": zone, "die": f"d{sides}", "value": value,
            "face": face, "triggered": value >= face}


# ════════════════════════════════════════════════════════════════════════
# 五、场景行动单入口
# ════════════════════════════════════════════════════════════════════════


def perform_action(session: RuleSession, action_id: str, unit_id: str = "",
                   *, reveal: bool = False, rng=None) -> dict:
    """执行一个场景行动：**一次调用完成全部结算**。

    控制流：校验（含战斗锁）→ 已完成短路 → 机会耗尽结算 → 掷骰 →
    判定与代奖（resolve_check）→ 结构化返回。玩家只能传「做哪个行动」，
    掷骰、成败、代价、奖励全部在服务端算完，没有「听结果再改参数」的窗口。

    reveal 只控制叙事流里要不要写骰值明细（透传，不是玩家偏好存储）。
    """
    action = _find_action(session, action_id)
    if action is None:
        raise ValueError("行动不存在于当前场景")

    # 战斗进行中不放行普通场景行动：否则玩家能在战斗界面外把场景清空、
    # 把该做的先做完，回合制的约束整个失效。
    if session.combat and not session.combat.get("over"):
        raise ValueError("战斗还没结束，先打完这场")

    kind = str(action.get("kind") or "check")
    if kind not in ACTION_KINDS:
        raise ValueError(f"未知的行动类别：{kind}")

    if kind == "narrate":
        text = str(action.get("text") or "")
        session.add_log("narrative", text,
                        speaker=str(action.get("label") or "旁白"))
        return {"status": "resolved", "kind": kind, "text": text,
                "need_roll": False}

    unit = _find_action_unit(session, unit_id)
    label = str(action.get("label") or "")

    # 已经成功过的行动：不重复掷骰、不重复发奖；失败可以重试。
    done = session.done_actions.get(action_id)
    if done:
        return {
            "status": "already_done", "need_roll": False, "already": True,
            "action": {"id": action.get("id"), "label": label},
            "text": done.get("text", ""),
            "reward": done.get("reward", []),
            "done_by": done.get("unit", ""),
            "message": f"「{label}」已经完成过了（{done.get('unit', '')}）。",
        }

    # 机会耗尽：行动已被彻底关闭，不再允许重试。这里**必须真的结算代价**，
    # 不能只是返回——否则「耗尽」只是一个灰按钮，世界不会有任何反应。
    max_fail = int(action.get("max_fail") or 0)
    fails_done = int(session.action_fails.get(action_id, 0) or 0)
    if max_fail and fails_done >= max_fail:
        cost = apply_failure(session, unit, int(action.get("df") or 0), label,
                             info_only=not action.get("dangerous"))
        event = tick_pressure(session, 1)
        session.add_log(
            "system",
            f"「{label}」的机会已经用尽（连续失败 {max_fail} 次）；"
            f"放弃同样要付代价。", speaker="代价")
        return {
            "status": "exhausted", "need_roll": False, "exhausted": True,
            "action": {"id": action.get("id"), "label": label,
                       "max_fail": max_fail, "fail_count": fails_done},
            "cost": cost, "pressure_event": event, "rolled": False,
            "roll": None, "left_before": 0,
            "message": f"「{label}」的机会已经用尽。局势继续推进。",
        }

    fails = fails_done
    # 掷骰前的剩余机会：结算后的值会被成功 / 耗尽逻辑改写，
    # 单独返回一份给调用方做严谨断言与展示。
    left_before = max(0, max_fail - fails) if max_fail else None

    if action.get("auto_pass"):
        result = {"notation": "", "count": 0, "sides": 0, "dice": [],
                  "modifier": 0, "total": 0, "natural": None,
                  "detail": "该行动无需掷骰"}
        rolled = False
    else:
        result = roll(_check_notation(action, unit),
                      advantage=int(action.get("advantage") or 0),
                      disadvantage=int(action.get("disadvantage") or 0)
                      + _condition_disadvantage(unit),
                      rng=rng)
        rolled = True

    out = resolve_check(session, action, result, unit, reveal=reveal, rng=rng)
    out["left_before"] = left_before
    out["need_roll"] = False
    out["rolled"] = rolled
    out["roll"] = result if rolled else None
    out["action"] = {
        "id": action.get("id"), "label": label,
        "df": int(action.get("df") or 0), "max_fail": max_fail,
        "fail_count": fails, "left": left_before,
    }
    out["unit"] = _unit_view(unit)
    return out


def resolve_check(session: RuleSession, action: dict, result: dict,
                  unit: dict | None, *, reveal: bool = False,
                  rng=None) -> dict:
    """结算一次判定：成败 → 失败代价 / 成功奖励 → 记录。

    失败代价只在**配置了重试上限**的行动上结算（max_fail > 0）：
    这类行动是有压力的；没有上限的行动属于低风险试探，不计价。
    险成（narrow）按 02A 第三节是「达成 + 一个代价」，代价内容由
    导引者裁定（损失资源 / 引入麻烦 / 留下痕迹 / 暴露自己），规则层
    不代选——TODO(M3) 导引者接线后在此挂代价钩子。
    """
    action_id = str(action.get("id") or "")
    label = str(action.get("label") or "")
    df = int(action.get("df") or 0)
    total = int(result.get("total") or 0)
    auto = bool(action.get("auto_pass"))
    outcome = judge_check(total, df, auto_pass=auto,
                          natural=result.get("natural"))
    passed = outcome not in _FAILED_OUTCOMES

    max_fail = int(action.get("max_fail") or 0)
    fails = 0
    tier_text = ""
    exhausted = False
    cost: dict = {}
    if not passed and max_fail > 0:
        fails = int(session.action_fails.get(action_id, 0) or 0) + 1
        session.action_fails[action_id] = fails
        tiers = action.get("fail_tiers") or []
        if tiers:
            tier_text = str(tiers[min(fails, len(tiers)) - 1])
        else:
            tier_text = f"机会正在减少（{fails}/{max_fail}）。"
        if fails >= max_fail:
            exhausted = True
        cost = apply_failure(session, unit, df, label,
                             info_only=not action.get("dangerous"),
                             catastrophe=(outcome == "catastrophe"),
                             rng=rng)
        cost["pressure_event"] = tick_pressure(session, 1)

    # 检定行：默认只写结论；reveal 时才附骰值明细。
    verdict_zh = _OUTCOME_ZH.get(outcome, outcome)
    if reveal and result.get("detail"):
        check_text = f"{label} · 判定 {result['detail']} = {total} vs DF {df} → {verdict_zh}"
    else:
        check_text = f"{label} · 判定 → {verdict_zh}"
    session.add_log("check", check_text, speaker=verdict_zh,
                    extra={"df": df, "total": total, "passed": passed,
                           "action": label, "detail": result.get("detail", ""),
                           "revealed": bool(reveal)})
    result_text = action.get("on_pass") if passed else action.get("on_fail")
    if result_text:
        session.add_log("result", str(result_text),
                        speaker=str((unit or {}).get("name") or label))
    if tier_text:
        session.add_log("system", tier_text, speaker="局势")

    granted: list = []
    if passed:
        session.done_actions[action_id] = {
            "unit": str((unit or {}).get("name") or ""),
            "text": str(result_text or ""),
            "reward": [],
        }
        granted = _grant_rewards(session, action, unit)
        session.done_actions[action_id]["reward"] = [
            item.get("name", item) if isinstance(item, dict) else item
            for item in granted
        ]

    return {"status": "resolved", "passed": passed, "outcome": outcome,
            "exhausted": exhausted, "fails": fails, "tier_text": tier_text,
            "cost": cost, "granted": granted, "auto": auto}


def judge_check(total: int, df: int, *, auto_pass: bool = False,
                natural: int | None = None) -> str:
    """把判定结果归入档位（见 OUTCOMES）。

    按 docs/system/02A 第三节 / data/system/tactics_resolution.json
    outcome_grades：设 R = 判定结果 − DF——
      R ≥ +6      凯旋 triumph
      0 ~ +5      成功 success（结果 = DF 也算达成，平手归属行动方）
      −1 ~ −2     险成 narrow（达成，但附一个代价——代价内容归导引者，
                  见 resolve_check 注释）
      −3 ~ −9     挫败 failure
      ≤ −10       灾难 catastrophe
    自然 1 → 灾难（同表「≤ −10 或自然 1」）；`natural` 为单颗骰的原始
    面值，多骰或免骰时为 None。auto_pass（无需掷骰）按达成处理。
    """
    if auto_pass:
        return "success"
    if natural is not None and int(natural) == 1:
        return "catastrophe"
    margin = int(total) - int(df)
    if margin >= 6:
        return "triumph"
    if margin >= 0:
        return "success"
    if margin >= -2:
        return "narrow"
    if margin >= -9:
        return "failure"
    return "catastrophe"


def _condition_disadvantage(unit: dict | None) -> int:
    """单位状态带来的劣势层数（docs/system/02A 第二节劣势来源表）。

    处于【失衡】【中毒】【恐惧】等状态：每个 −1 层，上限 −3。
    其余劣势来源（掩体、照明、分心……）是情境裁定，由调用方通过
    action["disadvantage"] 显式传入，本层不猜测。
    """
    if not unit:
        return 0
    count = sum(1 for item in unit.get("conditions") or []
                if item in ("失衡", "中毒", "恐惧"))
    return min(3, count)


def _flat_check_penalty(unit: dict | None) -> int:
    """负担与【疲惫】带来的固定判定减值（负数）。

    负担档位按 docs/system/01 第十二节：2–3 → −1；4–5 → −2；6 → −3；
    【疲惫】按 data/system/conditions.json derived：所有判定 −1。
    注：源文档写作「判定 −1 骰」，本实现按固定减值处理（等效于把
    一层劣势折算为 −1），与助势骰劣势层不混算——理由与对照见 PR。
    """
    if not unit:
        return 0
    penalty = 0
    strain = int((unit.get("resources") or {}).get("strain", 0) or 0)
    if strain >= 6:
        penalty -= 3
    elif strain >= 4:
        penalty -= 2
    elif strain >= 2:
        penalty -= 1
    if "疲惫" in (unit.get("conditions") or []):
        penalty -= 1
    return penalty


def _check_notation(action: dict, unit: dict | None) -> str:
    """构造一次判定的掷骰记法（战术引擎：d20 + 各固定加值）。

    判定公式按 docs/system/02A 第一节：d20 + 属性修正 + 熟练加值 +
    专精加值 + 助势骰总和 + 情境修正。其中——
      · 行动数据自带 `notation` 时以数据为准（显式优先）；
      · `attribute` / `proficient` / `specialty` 字段存在时按公式折入
        固定加值（属性修正表见 data/system/attributes.json；熟练量表见
        docs/system/01 第十四节；专精额外 +1，见 02A 第一节）；
      · 负担与【疲惫】的固定减值一并折入；
      · 助势骰不进记法，由 roll() 的 advantage / disadvantage 参数
        按 docs/system/02A 第二节结算。
    情境修正（态势、高地、掩体等）由调用方折入 `notation` 或
    `advantage` / `disadvantage`，本层不臆测场景。
    """
    explicit = str(action.get("notation") or "").strip()
    if explicit:
        return explicit
    flat = _flat_check_penalty(unit)
    attribute = str(action.get("attribute") or "").strip().upper()
    if attribute:
        if attribute not in ATTRIBUTES:
            raise ValueError(f"未知属性：{attribute}")
        value = int(((unit or {}).get("attributes") or {}).get(attribute, 4))
        flat += attribute_modifier(value)
        if action.get("proficient"):
            level = int((unit or {}).get("level") or 1)
            flat += proficiency_bonus(level)
        if action.get("specialty"):
            flat += 1
    if flat:
        return f"1d20{flat:+d}"
    return DEFAULT_CHECK_NOTATION


def _grant_rewards(session: RuleSession, action: dict,
                   unit: dict | None) -> list:
    """成功后的奖励发放（物品 / 成长等）。

    TODO(M2)：奖励的唯一发放出口，按 docs/system/01 第十三 / 十四节、
    docs/system/03 第五节与 data/system/economy.json 落地。当前为空占位
    ——玩家没有奖励入口，奖励只能从这里进。
    """
    return []


def _find_action(session: RuleSession, action_id: str) -> dict | None:
    target = str(action_id or "")
    for action in (session.scene or {}).get("actions") or []:
        if str(action.get("id") or "") == target:
            return action
    return None


def _find_action_unit(session: RuleSession, unit_id: str) -> dict | None:
    """定位行动的执行者。显式传入的 id 必须存在；缺省时用当前行动角色。"""
    target = str(unit_id or "") or str(session.active_unit_id or "")
    if target:
        unit = next((p for p in session.party
                     if str(p.get("id") or "") == target), None)
        if unit is None:
            raise ValueError("单位不存在")
        return unit
    return session.party[0] if session.party else None


# ════════════════════════════════════════════════════════════════════════
# 六、战斗状态机
# ════════════════════════════════════════════════════════════════════════

# 仅本场战斗有效的临时增益字段。TODO(M2)：按 docs/system/01 第七 / 八节与
# 02A 场地要素列出（能力与场地赋予的临时修正）；所有战斗出口都会清理，
# 漏掉任何一条都会让上一场的修正常驻。
COMBAT_BUFF_FIELDS: tuple[str, ...] = ()


def start_combat(session: RuleSession, *, difficulty: str = "",
                 enemies: list[dict] | None = None) -> dict:
    """开始一场遭遇。**内容由服务端决定**，玩家只能选择「打不打」。

    敌体列表缺省时走遭遇生成（build_encounter）；生成器落地前，
    调用方（测试 / 剧本）必须显式传入敌体列表。

    开局后必须**立刻推进一次**：先攻最高的可能是敌体，如果只顾好顺序就
    返回，没人去跑它的回合，战斗会从第一秒就静止。_advance 会一路推进
    到第一个需要玩家操作的位置才返回。
    """
    if session.combat and not session.combat.get("over"):
        raise ValueError("战斗还没结束")
    squad = list(enemies) if enemies is not None else build_encounter(
        session, difficulty)
    if not squad:
        raise ValueError("这里没有可交战的东西")
    session.combat = {
        "scene_id": str((session.scene or {}).get("id") or ""),
        "round": 1,
        "difficulty": str(difficulty or ""),
        "enemies": squad,
        "order": [],
        "turn_i": 0,
        "acts": {},      # 本回合的去重表：{单位 key: 动作 / 标记}
        "events": [],    # 本回合结算出的事件（前端逐条播报）
        "over": "",      # "" / victory / defeat / fled
    }
    _reorder_initiative(session)
    _advance(session)
    session.add_log("system", f"战斗开始：{len(squad)} 个敌体。", speaker="战斗")
    session.touch()
    return combat_view(session)


def build_encounter(session: RuleSession, difficulty: str = "") -> list[dict]:
    """按遭遇预算生成敌体列表（缺省遭遇生成器）。

    TODO(M2)：预算与强度档按 docs/system/02A 第九、十节与
    data/system/tactics_encounter.json 落地；当前返回空列表——
    调用方必须显式传入敌体，数据驱动的入口保持不变。
    """
    return []


def _reorder_initiative(session: RuleSession) -> None:
    """重排行动顺序，并清零本回合的去重表。

    TODO(M2)：先攻分按 docs/system/02A 第六节、docs/system/01 第二节
    （洞察修正 + 灵巧修正的一半）落地；平手规则同节。当前所有单位
    同分，按单位 key 稳定排序（确定性优先，方便测试与复现）。
    """
    combat = session.combat
    if not combat:
        return
    units: list[dict] = []
    for unit in session.party:
        if _can_act_unit(unit):
            units.append(_order_entry("party", str(unit.get("id") or ""),
                                      str(unit.get("name") or ""), unit))
    for index, enemy in enumerate(combat.get("enemies") or []):
        if _can_act_unit(enemy):
            units.append(_order_entry("enemy", str(index),
                                      str(enemy.get("name") or ""), enemy))
    units.sort(key=lambda entry: (-int(entry.get("init") or 0), entry["key"]))
    combat["order"] = units
    combat["turn_i"] = 0


def _order_entry(side: str, unit_key: str, name: str, unit: dict) -> dict:
    return {"key": _unit_key(side, unit_key), "side": side, "id": unit_key,
            "name": name, "init": _initiative_score(unit)}


def _unit_key(side: str, unit_id: str) -> str:
    return f"{side}:{unit_id}"


def _initiative_score(unit: dict) -> int:
    """单位的一次先攻分。

    TODO(M2)：按 docs/system/02A 第六节 / docs/system/01 第二节落地；
    当前返回 0（占位），排序退化为按单位 key 的稳定顺序。
    """
    return 0


def _can_act_unit(unit: dict | None) -> bool:
    """单位本回合能否行动：消亡与濒危都不能。

    消亡与濒危都还可能「被救回」，所以它们是两个标记、也是一条
    独立判据——只查一个标记，就会出现「全员倒地但战斗不结束」或
    「倒地角色还能行动」这类状态矛盾。
    """
    return bool(unit) and not unit.get("dead") and not unit.get("downed")


def _order_unit(session: RuleSession, entry: dict) -> dict | None:
    """按行动顺序条目取回单位本体（party 直接查，enemy 按下标查）。"""
    if str(entry.get("side") or "") == "enemy":
        index = int(entry.get("id") or -1)
        enemies = (session.combat or {}).get("enemies") or []
        if 0 <= index < len(enemies):
            return enemies[index]
        return None
    return next((unit for unit in session.party
                 if str(unit.get("id") or "") == str(entry.get("id"))), None)


def _order_key_at(combat: dict, index: int) -> str:
    order = combat.get("order") or []
    if 0 <= index < len(order):
        return str(order[index].get("key") or "")
    return ""


def _unit_can_act(session: RuleSession, entry: dict) -> bool:
    return _can_act_unit(_order_unit(session, entry))


def _current_unit(session: RuleSession) -> dict | None:
    """当前该行动的单位；扫不到就返回 None。

    跳过不能只 continue：必须把 turn_i 一起推进，否则每次调用都从同一个
    下标重扫，_advance 的 i+1 会被这个陈旧值拉回去，战斗会卡在同一个
    已倒地的单位上。循环上限是兜底——上限内找不到就说明没有可行动单位。
    """
    combat = session.combat
    if not combat or combat.get("over"):
        return None
    order = combat.get("order") or []
    index = int(combat.get("turn_i", 0) or 0)
    for _ in range(len(order) * 2 + 4):
        if index >= len(order):
            break
        entry = order[index]
        if _unit_can_act(session, entry):
            if index != int(combat.get("turn_i", 0) or 0):
                combat["turn_i"] = index
            return entry
        index += 1
    combat["turn_i"] = index
    return None


def _alive_party(session: RuleSession) -> list[dict]:
    """还没消亡的角色（濒危的也算活着：可以被救起）。"""
    return [unit for unit in session.party if not unit.get("dead")]


def _conscious_party(session: RuleSession) -> list[dict]:
    """还站着的角色（没消亡也没濒危）。**胜负判定用这个**。"""
    return [unit for unit in session.party if _can_act_unit(unit)]


def _check_combat_end(session: RuleSession) -> bool:
    """胜负检查点：敌体全倒 → 胜利；全队不能行动 → 失败。

    这是一个**共用检查点**：任何单位行动完、以及恢复中的战斗每次推进前
    都先过这里。漏掉任何一条出口，战斗就会停在「胜负已分、状态却没标记」
    的死局里——界面还显示进行中，玩家却做什么都不对。
    """
    combat = session.combat
    if not combat or combat.get("over"):
        return True
    if all(enemy.get("dead") for enemy in combat.get("enemies") or []):
        _end_combat(session, "victory")
        return True
    if not _conscious_party(session):
        _end_combat(session, "defeat")
        return True
    return False


def _advance(session: RuleSession) -> None:
    """推进到**下一个需要玩家操作的位置**。

    敌体的回合在这里一次性跑完，直到轮到玩家（或战斗结束）才返回；
    把「敌体行动」摊成玩家按钮不是回合制，是卡顿。

    起始下标取决于当前单位**是否已经行动过**：已有动作记录 → 从下一个
    开始；还没有 → 就从当前开始。无条件 +1 会在「战斗刚开始、当前单位
    尚未行动」时把它整个跳过，先攻高的敌体将永远不动。
    """
    combat = session.combat
    if not combat or combat.get("over"):
        return
    if not (combat.get("order") or []):
        return
    if _check_combat_end(session):
        return

    current = int(combat.get("turn_i", 0) or 0)
    index = current if not combat["acts"].get(
        _order_key_at(combat, current)) else current + 1

    # 上限按「可能走过的单位数」算：每跨一回合所有单位都要重新过一次，
    # 给足 3 轮；上限内推不动就说明状态不一致（见函数尾部兜底）。
    guard = 0
    limit = len(combat["order"]) * 3 + 12
    while guard < limit:
        guard += 1
        if index >= len(combat["order"]):
            combat["round"] = int(combat.get("round", 1) or 1) + 1
            combat["acts"] = {}
            _reorder_initiative(session)
            # 重掷先攻是重新赋值 order，旧下标作废，必须回到 0。
            index = 0
        entry = combat["order"][index]
        if not _unit_can_act(session, entry):
            index += 1
            continue
        # 去重表：少了它，顺序回绕时同一单位会在同一回合里再动一次。
        if combat["acts"].get(entry["key"]):
            index += 1
            continue
        combat["turn_i"] = index
        if entry.get("side") == "enemy":
            _enemy_turn(session, entry)
            combat["acts"][entry["key"]] = "enemy"
            # 敌体行动后重判胜负：打死最后一个角色就该结束了。
            if _check_combat_end(session):
                return
            index += 1
            continue
        # 轮到玩家：停下，把操作权交回去。
        return

    # 兜底：上限内没轮到玩家，先显式判一次胜负；确属状态不一致
    # （还有能行动的单位却推不动）就清空去重表，绝不留在一个
    # 谁都不能动的死状态。
    if _check_combat_end(session):
        return
    combat["acts"] = {}
    combat["turn_i"] = 0


def _pick_enemy_target(session: RuleSession) -> dict | None:
    """敌体的目标选择：当前行动角色优先（他刚暴露自己），倒下就换人。

    目标偏好数据（战术人格）由 M2 接入 docs/system/04；这里只保证
    服务端自己选目标，玩家不能替敌体决定打谁。
    """
    active = next((unit for unit in session.party
                   if str(unit.get("id") or "") == str(session.active_unit_id)),
                  None)
    if _can_act_unit(active):
        return active
    return next((unit for unit in session.party if _can_act_unit(unit)), None)


def _enemy_turn(session: RuleSession, entry: dict) -> None:
    """一个敌体的一次行动（控制流骨架）。"""
    combat = session.combat
    enemy = _order_unit(session, entry)
    if enemy is None or not _can_act_unit(enemy):
        return
    target = _pick_enemy_target(session)
    if target is None:
        return
    outcome = _resolve_enemy_action(session, enemy, target) or {}
    text = str(outcome.get("text") or "")
    if text:
        session.add_log("system", text, speaker="战斗")
    for event in outcome.get("events") or []:
        combat["events"].append(event)


def _resolve_enemy_action(session: RuleSession, enemy: dict,
                          target: dict) -> dict:
    """敌体行动的结算（占位）。

    TODO(M2)：按 docs/system/02A 第五、九节与 data/system/tactics_enemies.json
    落地「选招 → 掷骰 → 比防护 → 走 deal_damage」的全过程，并接入
    docs/system/04 的敌体战术人格。在语义落地前返回空结果，
    不做任何数值猜测。
    """
    return {"text": "", "events": []}


def _end_combat(session: RuleSession, outcome: str) -> None:
    """战斗结束。经验结算与压力推进都在这里——玩家不能自己领奖。"""
    combat = session.combat
    if not combat:
        return
    combat["over"] = str(outcome)
    if outcome == "victory":
        # 只给**真正被击倒**的敌体经验；没打倒的不给。
        xp_total = sum(int(enemy.get("xp") or 0)
                       for enemy in combat.get("enemies") or []
                       if enemy.get("dead"))
        # TODO(M2)：难度 / 预算的结算系数与成长口径按
        # docs/system/02A 第十节、docs/system/01 第十四节落地。
        alive = _alive_party(session)
        share = xp_total // max(1, len(alive))
        for unit in alive:
            award_xp(session, unit, share)
        per = f"（每人 {share}）" if len(alive) > 1 else ""
        session.add_log("system", f"战斗胜利！全队共获得 {xp_total} 点经验{per}。",
                        speaker="战斗")
        tick_pressure(session, 1)
    else:
        session.add_log("system", "队伍全灭，这一程到此为止。", speaker="战斗")
    # 无论胜败，临时增益都要清掉；撤退走 combat_abandon，也调同一个清理。
    _clear_combat_buffs(session)
    session.touch()


def combat_abandon(session: RuleSession) -> dict:
    """脱离战斗（撤退）。**代价真实**：推进压力，且不结算经验。

    没有撤退出口，玩家明知打不过也只能耗到全灭——那是把决策变成惩罚。
    """
    combat = session.combat
    if not combat or combat.get("over"):
        raise ValueError("当前没有进行中的战斗")
    combat["over"] = "fled"
    session.add_log("system", "队伍脱离了战斗（没有获得经验）。", speaker="战斗")
    tick_pressure(session, 1)
    _clear_combat_buffs(session)
    session.touch()
    return combat_view(session)


def _clear_combat_buffs(session: RuleSession) -> None:
    """清掉「只在本场战斗内有效」的临时增益。

    这是一条独立的收尾路径，必须在战斗的所有出口都调到（胜利、失败、
    撤退）。漏掉任何一条，上一场的修正会悄悄常驻到下一场——这种 bug
    在测试里几乎看不出来，只有老玩家会觉得数值对不上。
    """
    for field in COMBAT_BUFF_FIELDS:
        for unit in session.party:
            if field in unit:
                unit[field] = 0
    session.touch()


def combat_view(session: RuleSession) -> dict:
    """战斗视图（前端独立视图直接吃这份）。"""
    combat = session.combat
    if not combat:
        return {"active": False}
    return {
        "active": not bool(combat.get("over")),
        "over": str(combat.get("over") or ""),
        "round": int(combat.get("round", 1) or 1),
        "scene_id": str(combat.get("scene_id") or ""),
        "current": copy.deepcopy(_current_unit(session)),
        "order": copy.deepcopy(combat.get("order") or []),
        "events": copy.deepcopy(combat.get("events") or []),
        "acts": copy.deepcopy(combat.get("acts") or {}),
        "enemies": [_unit_view(enemy) for enemy in combat.get("enemies") or []],
        "party": [_unit_view(unit) for unit in session.party],
    }


# ════════════════════════════════════════════════════════════════════════
# 七、派生值与明细
# ════════════════════════════════════════════════════════════════════════


def equipped_items(unit: dict | None) -> dict:
    """按装备槽位取当前穿戴（只认白名单槽位）。

    TODO(M2)：与装备目录 / 背包接线（docs/system/02A 装备表、各世界模组
    装备节）；目录数据在 M2 落入 data/，本函数保持槽位结构不变。
    """
    equipment = (unit or {}).get("equipment") or {}
    return {slot: equipment.get(slot)
            for slot, _ in EQUIP_SLOTS if equipment.get(slot)}


def item_guard_bonus(item) -> int:
    """装备条目自带的防护加值（护甲 Guard 值，0–6，见 docs/system/01 第二节）。"""
    if not isinstance(item, dict):
        return 0
    return int(item.get("guard") or 0)


def _stance_guard_bonus(unit: dict) -> int:
    """态势对防护的修正。

    TODO(M2)：按 docs/system/01 第七节与 data/system/stances.json 落地；
    当前返回 0。
    """
    return 0


def _cover_guard_bonus(unit: dict) -> int:
    """掩体对防护的修正。

    TODO(M2)：按 docs/system/02A 第五节与 data/system/tactics_combat.json
    落地；当前返回 0。
    """
    return 0


def guard_breakdown(unit: dict | None, session: RuleSession | None = None) -> dict:
    """把防护拆成可展示的几段：基础 + 装备 + 态势 + 掩体。

    展示与结算必须同源：compute_guard 直接调本函数。只返回一个数字的话，
    界面要另算一份——那必然漂移，玩家会看到角色卡与结算对不上。
    """
    parts = [{"label": "基础防护", "value": GUARD_BASE}]
    items = equipped_items(unit)
    for slot, slot_zh in EQUIP_SLOTS:
        item = items.get(slot)
        value = item_guard_bonus(item)
        if value and isinstance(item, dict):
            parts.append({"label": str(item.get("name") or slot_zh),
                          "value": value})
    for label, value in (("态势修正", _stance_guard_bonus(unit or {})),
                         ("掩体修正", _cover_guard_bonus(unit or {}))):
        if value:
            parts.append({"label": label, "value": int(value)})
    total = max(0, sum(int(part["value"]) for part in parts))
    return {"total": total, "base": GUARD_BASE, "parts": parts}


def compute_guard(unit: dict | None, session: RuleSession | None = None) -> int:
    """结算用的防护值（= breakdown 的 total）。"""
    return guard_breakdown(unit, session)["total"]


def recompute_unit(unit: dict | None, session: RuleSession | None = None) -> None:
    """统一重算派生值；凡是会改变属性 / 等级 / 装备的路径都必须走这里。

    当前只重算防护，并按已知上限夹取活力（没有上限时不硬夹——
    不能把「未落地的数据」当成 0 上限处理）。
    TODO(M2)：活力上限 / 移动 / 先攻等其余派生值按 docs/system/01 第二节
    与 02A/02B 落地（职途与装备数据接入后，仍统一从这里重算）。
    """
    if unit is None:
        return
    unit["guard"] = compute_guard(unit, session)
    cap = int(unit.get("max_vitality") or 0)
    vitality = max(0, int(unit.get("vitality") or 0))
    if cap > 0:
        vitality = min(vitality, cap)
    unit["vitality"] = vitality
    if session is not None:
        session.touch()


# ════════════════════════════════════════════════════════════════════════
# 八、成长
# ════════════════════════════════════════════════════════════════════════

# 等级门槛表：[(等级, 升入该级所需的累计经验), ...]。
# TODO(M2)：按 docs/system/01 第十四节（经验点法：积满 [当前等级 × 10] 点
# 升级；或里程碑法直接给等级）落地；空表期间恒定 1 级。
LEVEL_XP_THRESHOLDS: tuple[tuple[int, int], ...] = ()


def level_for_xp(xp: int) -> int:
    """按累计经验算等级；超过表顶就停在表顶。"""
    xp = max(0, int(xp or 0))
    level = 1
    for target_level, need in LEVEL_XP_THRESHOLDS:
        if xp >= int(need) and int(target_level) > level:
            level = int(target_level)
    return level


def growth_points_for(level: int) -> int:
    """升入某个等级时**由服务端发放**的成长点数。

    TODO(M2)：按 docs/system/01 第十四节「每级固定收益」与
    docs/system/02A/02B 的成长表落地；当前返回 0（占位）。

    发放只发生在这里——玩家只能消费已经发放的点数（见 assign_attribute），
    没有「加等级 / 加点数」的入口。
    """
    return 0


def award_xp(session: RuleSession, unit: dict | None, amount: int) -> dict:
    """给经验；跨级时自动升级并发放成长点数。

    升级必须**把等级写回单位**（只算不写会出现「日志显示升级、角色卡
    还是旧等级」）。升级只做派生值重算与上限夹取；活力是否随升级变化
    由 M2 的成长口径决定（docs/system/01 第十四节只写了活力上限 +2）。
    """
    if unit is None:
        raise ValueError("经验必须发到某个单位上")
    granted_amount = max(0, int(amount or 0))
    before_level = int(unit.get("level") or 1)
    unit["xp"] = max(0, int(unit.get("xp") or 0)) + granted_amount
    want_level = level_for_xp(unit["xp"])
    gained = 0
    if want_level > before_level:
        unit["level"] = want_level
        for level in range(before_level + 1, want_level + 1):
            points = int(growth_points_for(level) or 0)
            unit["growth_points"] = int(unit.get("growth_points") or 0) + points
            gained += points
        recompute_unit(unit, session)
    session.add_log(
        "system",
        f"{unit.get('name', '')} 获得 {granted_amount} 点经验"
        + (f"，等级 {before_level} → {unit['level']}" if want_level > before_level
           else f"（LV{unit['level']}）")
        + (f"，发放 {gained} 点成长点数" if gained else ""))
    session.touch()
    return {"xp": unit["xp"], "level": unit["level"],
            "growth_points": int(unit.get("growth_points") or 0),
            "gained": gained}


def assign_attribute(session: RuleSession, unit_id: str, attribute: str,
                     delta: int = 1) -> dict:
    """消费成长点数调整属性：**服务端发放、玩家分配**。

    校验四件事：属性名在白名单内；结果不越 1–10 的界；
    delta 不为 0；手里确实有成长点数。任何一条不过都直接拒绝。

    TODO(M2)：A 类成长「每项属性通过成长最多 +4」需要按
    docs/system/01 第十四节追踪每项属性的历史加成，与上限校验一并落地。
    """
    unit = next((p for p in session.party
                 if str(p.get("id") or "") == str(unit_id or "")), None)
    if unit is None:
        raise ValueError("单位不存在")
    key = str(attribute or "").strip().upper()
    if key not in ATTRIBUTES:
        raise ValueError("属性名不合法")
    delta = int(delta or 0)
    if delta == 0:
        raise ValueError("点数变化不能为 0")
    current = int((unit.get("attributes") or {}).get(key, 4))
    new = current + delta
    if not (ATTRIBUTE_MIN <= new <= ATTRIBUTE_MAX):
        raise ValueError(
            f"{ATTRIBUTES[key]['zh']}必须保持在 {ATTRIBUTE_MIN}–{ATTRIBUTE_MAX} "
            f"之间（当前 {current}）")
    if int(unit.get("growth_points") or 0) < 1:
        raise ValueError("没有可分配的成长点数（由服务端在升级时发放）")
    unit["growth_points"] = int(unit["growth_points"]) - 1
    before_cap = int(unit.get("max_vitality") or 0)
    unit.setdefault("attributes", {})[key] = new
    recompute_unit(unit, session)
    after_cap = int(unit.get("max_vitality") or 0)
    session.add_log(
        "system",
        f"{unit.get('name', '')} 的{ATTRIBUTES[key]['zh']} {current} → {new}"
        + (f"（活力上限 {before_cap} → {after_cap}）" if after_cap != before_cap
           else ""))
    session.touch()
    return {"unit": _unit_view(unit), "attribute": key, "value": new,
            "growth_points": unit["growth_points"],
            "d_vitality_cap": after_cap - before_cap}


__all__ = [
    # 一、术语与白名单
    "ATTRIBUTES", "ATTRIBUTE_IDS", "ATTRIBUTE_MIN", "ATTRIBUTE_MAX",
    "ATTRIBUTE_MODIFIER_RANGES", "GUARD_BASE", "RESOURCE_POOLS",
    "ACTION_KINDS", "COMBAT_ACTIONS", "EQUIP_SLOTS", "OUTCOMES",
    "PROFICIENCY_LEVELS", "STRAIN_MAX", "RESOLVE_MAX",
    "DEBUFF_CONDITIONS", "KNOWN_CONDITIONS", "register_condition",
    "attribute_modifier", "proficiency_bonus",
    # 二、掷骰器
    "DICE_MAX_COUNT", "DICE_MAX_SIDES", "DEFAULT_CHECK_NOTATION", "roll",
    "EDGE_DIE_SIDES", "EDGE_MAX_LAYERS", "EDGE_BEYOND_FLAT",
    "cancel_edge_layers",
    # 三、会话状态契约
    "new_unit", "RuleSession",
    # 四、结算的单一进出口
    "deal_damage", "heal_unit", "change_resource", "add_condition",
    "failure_cost", "apply_failure", "tick_pressure",
    "CATASTROPHE_TABLE", "PRESSURE_MAX", "TENSION_BANDS", "tension_band",
    "RISK_POOL_ZONES", "risk_roll",
    # 五、场景行动单入口
    "perform_action", "resolve_check", "judge_check",
    # 六、战斗状态机
    "COMBAT_BUFF_FIELDS", "start_combat", "build_encounter",
    "combat_abandon", "combat_view",
    # 七、派生值与明细
    "equipped_items", "item_guard_bonus", "guard_breakdown", "compute_guard",
    "recompute_unit",
    # 八、成长
    "LEVEL_XP_THRESHOLDS", "level_for_xp", "growth_points_for", "award_xp",
    "assign_attribute",
]

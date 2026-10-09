#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prism_core.py —— 「棱镜 PRISM」规则核心的组织骨架（M2 前置）

本模块只提供**组织**：函数切分、控制流、数据流与工程范式。
一切数值、公式与判定语义按 `docs/system/` 在 M2 逐项落地，
文件内以 `TODO(M2)` 标记并注明对应章节；`TODO(M3)` 指向落盘 / 并发 / HTTP 接线。

M2a（Issue #53）已落地：属性修正 / 熟练量表 / 助势骰（层数与取消）/
五档结果 / 资源夹取与专注过用 / 伤害标签（抗性 / 弱点 / 免疫 / 吸收）/
状态叠加与【疲惫】/ 灾难后果表 / 张力曲线触发。

M2b（Issue #59）已落地：先攻（d20 + 洞察修正 + 灵巧修正的一半）/
攻击六档解算（暴击 / 重击 / 命中 / 擦过 / 落空 / 严重失手）/
韧性与破韧（含首领护盾与阶段转化）/ 敌体行动（选招 → 掷骰 →
比防护 → deal_damage）/ 遭遇预算（5×6 预算表、强度档、组成限制）/
派生值（活力上限 / 移动 / 负重 / 先攻 / 专注与气势上限）/
濒危挣扎循环与【创伤】层数 / 状态层数（叠至 3 层升级）/
成长（十二级三层制、经验点法、六类成长选择、A 类 +4 上限）。

M2c（Issue #72）已落地：构建引擎骰池与结算（docs/system/02B 第一至
五节）——骰池四部分组成与成功阈值（≥5 记 1 成功，默认 d10）/
需求成功数 8 档（含战术 DF 对照）/ 五档结果梯度（结果 = S − N，
失败也给动量）与两个特例（全骰皆负、最大值爆发）/ 骰阶 5 阶与
期望成功数速查 / 动量（共享池 0–10 + 个人持有 5、7 源获取、7 项
花费、场景清零）/ 应力（上限 = 体魄 + 心智 + 5、5 条来源、4 段
惩罚、崩溃事件 4 步、4 种降低手段）。与战术引擎（d20 路径）并列、
不混用；符纹插槽 / 构建点成长 / 双引擎互转留给 M2d。
源文档未写的数值一律不臆造，仍以 `TODO(M2)` / `TODO(M2d)` 标注。

ATLAS I5（Issue #67）已接线：战术投影——规则核心在需要距离或高地时，
向地图（`atlas.py` 内核）询问投影，而不是另维护一套口头坐标。本层只
调用内核的 `range_band` 与 `high_ground`（外加读已有战术帧的 `space`
口径）；**没接地图时一切行为与 M2b 默认完全一致**（心象剧场）。

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
  六·附、战术投影（ATLAS I5）
  七、派生值与明细          八、成长
  九、构建引擎（M2c，02B 第一至五节）
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

# ── 状态层数（docs/system/01 第五节叠加规则 2 / data/system/conditions.json）
# 「部分状态标注 N 层，叠至 3 层时升级为完整效果」——源文档点名的两层
# 状态是【失衡】（02A 机动：绊摔 / 震击给 3 层 → 倒地）与【流血】
# （02A 机动：要害打击给 3 层）。
LAYERED_CONDITIONS = ("失衡", "流血")
CONDITION_MAX_LAYERS = 3

# ── 濒危挣扎（docs/system/01 第十一节「濒危 Downed」）──────────────────
# d20 + 体魄修正 vs DF 12：累计 3 次成功 → 稳定（+1 活力、+1 层【创伤】）；
# 累计 3 次失败 → 消亡；队友医疗 DF 14 可使其立即稳定。
DOWNED_STRUGGLE_DF = 12
DOWNED_STABILIZE_SUCCESSES = 3
DOWNED_DEATH_FAILURES = 3
MEDICAL_STABILIZE_DF = 14

# ── 创伤（docs/system/01 第十一节「创伤 Trauma」）───────────────────────
# 每次濒危稳定后 +1 层：1–2 层无机械影响；3 层活力上限 −5；
# 4 层起每次进入战斗开局获得 1 层【恐惧】。
TRAUMA_VITALITY_PENALTY_AT = 3
TRAUMA_VITALITY_PENALTY = 5
TRAUMA_FEAR_AT = 4


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
        "poise": 0,               # 韧性（01 第八节；recompute 后才有上限）
        "max_poise": 0,
        "stress": 0,              # 应力（02B 第五节；上限 = 体魄 + 心智 + 5）
        "poise_broken": False,
        "poise_shields": 0,       # 首领韧性护盾层数
        "boss_phase": 0,          # 首领阶段转化（1 暴怒 / 2 绝望 / 3 崩解）
        "trauma": 0,              # 【创伤】层数（01 第十一节）
        "downed": False,          # 濒危标记（docs/system/01 第十一节）
        "downed_fails": 0,        # 濒危挣扎失败计数（01 第十一节：受击 +1，暴击 +2）
        "downed_successes": 0,    # 濒危挣扎成功计数（3 成稳定）
        "dead": False,
        "conditions": [],
        "condition_layers": {},   # 叠层状态层数 {状态名: 层数}（01 第五节）
        "condition_rounds": {},   # 同名重复施加的延长记录 {状态名: 回合}
        "resources": {pool: 0 for pool in RESOURCE_POOLS},
        "damage_relations": {},   # {resistance/weakness/immunity/absorb: [伤害标签]}
        "equipment": {},
        "stance": "",
        "cover": "",
        "career_vitality": 0,     # 职途起始活力（世界模组数据，+6 ~ +14）
        "career_focus_bonus": 0,  # 职途专注加成（世界模组数据）
        "growth_attr_gain": {},   # A 类成长追踪：{属性: 累计 +N}（上限 +4）
        "growth_history": [],     # 成长选择历史（同一类别不得连续超过 2 次）
        "proficiencies": [],      # B 类：技能熟练
        "specializations": [],    # C 类：技能专精
        "growth_records": [],     # D/E/F 类：世界模组驱动的选择记录
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
        # 战术投影块（ATLAS I5，见六·附）：{atlas, frame_id, space,
        # unit_zones}。**运行期状态，不进 snapshot()**——地图的落盘与
        # 恢复由 I4 的 atlas 存档块负责，加载后由网页层重新 attach。
        self.tactical: dict | None = None
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
            # 注意：tactical（活地图）刻意不入快照——I4 的 atlas 存档块
            # 负责地图持久化，网页层加载后重新 attach（见六·附）。
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
              "poise", "max_poise", "poise_broken", "trauma",
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
        "effective": 0,
        "broken_amp": False,
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
        out["effective"] = effective
        session.touch()
        return out

    before = int(unit.get("vitality") or 0)
    # 破韧（docs/system/01 第八节破韧效果表）：承受伤害 +50%。
    if unit.get("poise_broken") and effective > 0:
        effective += effective // 2
        out["broken_amp"] = True
    out["effective"] = effective
    unit["vitality"] = max(0, before - max(0, effective))
    out["vitality"] = int(unit["vitality"])
    out["lost"] = before - int(unit["vitality"])

    # 刚跌到 0 及以下：进入濒危。已经不是濒危的再次受击不改标记，
    # 但按 01 第十一节推进濒危挣扎的失败计数（暴击 +2）。注意濒危单位
    # 活力已夹在 0，不能拿 lost 判断——只要这次命中真造成了伤害
    # （有效伤害 > 0）就计数。敌体没有濒危挣扎（no_struggle，02A 第九节
    # 模板无濒危条目）：活力归零即被击倒。
    if int(unit["vitality"]) <= 0 and not out["downed"]:
        if unit.get("no_struggle"):
            unit["dead"] = True
        else:
            unit["downed"] = True
            unit.setdefault("downed_successes", 0)
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


def downed_struggle(session: RuleSession, unit: dict | None, *,
                    rng=None) -> dict:
    """濒危单位的挣扎判定（docs/system/01 第十一节「濒危 Downed」）。

    每次在自己的回合开始时进行：`d20 + 体魄修正 vs DF 12`。
      · 累计 3 次成功 → 稳定下来：恢复 1 点活力、获得 1 层【创伤】；
      · 累计 3 次失败 → 消亡。
    受击导致的失败计数（普通 +1 / 暴击 +2）由 deal_damage 推进，与本
    函数共用 downed_fails 计数器。医疗急救的立即稳定走 stabilize_downed。
    """
    out = {"struggled": False, "total": 0, "df": DOWNED_STRUGGLE_DF,
           "success": False, "successes": 0, "fails": 0,
           "stabilized": False, "dead": False}
    if not unit or unit.get("dead") or not unit.get("downed"):
        return out
    vigor = int((unit.get("attributes") or {}).get("VIG", 4) or 4)
    total = roll("1d20", rng=rng)["total"] + attribute_modifier(vigor)
    out.update(struggled=True, total=total)
    if total >= DOWNED_STRUGGLE_DF:
        unit["downed_successes"] = int(unit.get("downed_successes") or 0) + 1
        out["success"] = True
    else:
        unit["downed_fails"] = int(unit.get("downed_fails") or 0) + 1
    out["successes"] = int(unit.get("downed_successes") or 0)
    out["fails"] = int(unit.get("downed_fails") or 0)
    if out["successes"] >= DOWNED_STABILIZE_SUCCESSES:
        out["stabilized"] = True
        out["healed"] = stabilize_downed(session, unit)["healed"]
    elif out["fails"] >= DOWNED_DEATH_FAILURES:
        unit["dead"] = True
        unit["downed"] = False
        out["dead"] = True
    session.touch()
    return out


def stabilize_downed(session: RuleSession, unit: dict | None, *,
                     source: str = "struggle") -> dict:
    """稳定一个濒危单位（唯一出口）。

    挣扎 3 次成功（source="struggle"）：恢复 1 点活力 + 获得 1 层【创伤】；
    队友医疗急救 DF 14（source="medical"，判定由调用方掷）：立即稳定，
    只获得【创伤】——源文档未写医疗稳定恢复活力，不臆造。
    """
    out = {"stabilized": False, "healed": 0, "trauma": 0}
    if not unit or unit.get("dead") or not unit.get("downed"):
        return out
    unit["downed"] = False
    unit["downed_fails"] = 0
    unit["downed_successes"] = 0
    out["stabilized"] = True
    out["trauma"] = add_trauma(session, unit, 1)
    if source != "medical":
        out["healed"] = heal_unit(session, unit, 1)
    session.add_log("system", f"{unit.get('name')} 稳定了下来"
                    f"（{'挣扎' if source == 'struggle' else '医疗急救'}）。",
                    speaker="战斗")
    return out


def add_trauma(session: RuleSession, unit: dict | None, layers: int = 1) -> int:
    """叠加【创伤】层数（docs/system/01 第十一节「创伤 Trauma」）。

    每次濒危稳定后 +1 层。层数效应由 vitality_cap（3 层 → 活力上限
    −5）与 start_combat（4 层起 → 开局 1 层【恐惧】）读取。
    """
    if unit is None:
        return 0
    unit["trauma"] = int(unit.get("trauma") or 0) + max(0, int(layers or 0))
    session.touch()
    return int(unit["trauma"])


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


def add_condition(session: RuleSession, unit: dict | None, name: str, *,
                  layers: int = 1) -> bool:
    """给单位施加一个状态，返回是否有新变化（新状态或层数增加）。

    叠加规则按 docs/system/01 第五节与 data/system/conditions.json：
      · 同一种状态**不叠加数值**，只取最强一档；同名重复施加改为延长
        持续时间（规则层记录施加回合 combat["round"]，非叠层状态返回
        False）；
      · 标注「N 层」的状态（LAYERED_CONDITIONS：【失衡】【流血】）可以
        叠层，叠至 3 层时升级为完整效果（如失衡 3 层 → 自动倒地，失去
        1 个回合全部 AP——由 _advance 在该单位回合开始时结算）；层数
        存于 unit["condition_layers"]；
      · 同时拥有 3 个及以上减益状态时，自动额外获得【疲惫】。
    状态名走白名单（KNOWN_CONDITIONS，来自 conditions.json）；世界模组
    扩展状态须经 register_condition 登记，未登记的名字直接 raise。
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
    layers = max(0, int(layers or 0))
    conditions = unit.setdefault("conditions", [])
    if name in conditions:
        if name in LAYERED_CONDITIONS and layers > 0:
            counts = unit.setdefault("condition_layers", {})
            counts[name] = min(CONDITION_MAX_LAYERS,
                               int(counts.get(name) or 1) + layers)
            # 同名重复施加改为延长持续时间（01 第五节叠加规则 1）。
            if session.combat:
                rounds = unit.setdefault("condition_rounds", {})
                rounds[name] = int(session.combat.get("round", 1) or 1)
            session.touch()
            return True
        return False
    conditions.append(name)
    if name in LAYERED_CONDITIONS and layers > 1:
        unit.setdefault("condition_layers", {})[name] = \
            min(CONDITION_MAX_LAYERS, layers)
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


def risk_roll(zone: str = "standard", *, rng=None, face: int | None = None) -> dict:
    """风险池的一次触发判定（纯函数，不碰会话状态）。

    `face` 覆盖默认触发面：docs/system/03:53 写「掷出 6（或 5–6，
    取决于区域危险度）」——标准区想用 5–6 变体时传 face=5 即可
    （掷出 ≥ face 触发）；缺省按区域默认面（d6→6 / d8→8 / d4→4）。
    """
    if zone not in RISK_POOL_ZONES:
        raise ValueError(f"未知区域类型：{zone}")
    sides, default_face = RISK_POOL_ZONES[zone]
    face = default_face if face is None else int(face)
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
                      + _unit_disadvantage(unit),
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


def _unit_disadvantage(unit: dict | None) -> int:
    """单位自身状态与负担带来的劣势层数，合并后封顶 3 层。

    口径统一为**劣势层**（主管预审裁定，PR #57 审查线）：文档里
    「所有判定 −N 骰」与本引擎唯一的骰惩罚机制（劣势骰，02A 第二节 /
    02A:655 命中公式「助势骰 - 劣势骰」）是同一件事——
      · 【失衡】【中毒】【恐惧】等状态：每个 −1 层（02A:112）；
      · 【疲惫】：−1 层（01:276「所有判定 −1 骰」）；
      · 负担 2–3 → −1 层；4–5 → −2 层；6 → −3 层（01:670-672）。
    合并后封顶 3 层（02A:83「劣势同样最多 3 层」）。
    注（文档级张力）：01:253 写恐惧「对恐惧来源的攻击 −1 骰」，此处
    取 02A:112 的「所有判定」口径——前者是攻击的子集情形，见 PR。
    行动数据显式传入的情境劣势（`action["disadvantage"]`）另计，
    最终仍由 roll() 按 02A:83 封顶。
    """
    if not unit:
        return 0
    conditions = unit.get("conditions") or []
    layers = sum(1 for item in conditions
                 if item in ("失衡", "中毒", "恐惧", "疲惫"))
    strain = int((unit.get("resources") or {}).get("strain", 0) or 0)
    if strain >= 6:
        layers += 3
    elif strain >= 4:
        layers += 2
    elif strain >= 2:
        layers += 1
    return min(3, layers)


def _check_notation(action: dict, unit: dict | None) -> str:
    """构造一次判定的掷骰记法（战术引擎：d20 + 各固定加值）。

    判定公式按 docs/system/02A 第一节：d20 + 属性修正 + 熟练加值 +
    专精加值 + 助势骰总和 + 情境修正。其中——
      · 行动数据自带 `notation` 时以数据为准（显式优先）；
      · `attribute` / `proficient` / `specialty` 字段存在时按公式折入
        固定加值（属性修正表见 data/system/attributes.json；熟练量表见
        docs/system/01 第十四节；专精额外 +1，见 02A 第一节）；
      · 助势骰（含优势与劣势层）不进记法：状态与负担的劣势层经
        `_unit_disadvantage`、情境劣势经 `action["disadvantage"]`，
        由 roll() 的 advantage / disadvantage 参数按 02A 第二节结算。
    情境修正（态势、高地、掩体等）由调用方折入 `notation` 或
    `advantage` / `disadvantage`，本层不臆测场景。
    """
    explicit = str(action.get("notation") or "").strip()
    if explicit:
        return explicit
    flat = 0
    attribute = str(action.get("attribute") or "").strip().upper()
    if attribute:
        if attribute not in ATTRIBUTES:
            raise ValueError(f"未知属性：{attribute}")
        value = int(((unit or {}).get("attributes") or {}).get(attribute, 4))
        flat += attribute_modifier(value)
        if action.get("proficient"):
            level = 1 if (unit or {}).get("level") is None \
                else int(unit["level"])
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

# ── 命中解算（docs/system/02A 附录速查表 653-663 / data/system/tactics_combat.json
# attack_grades）：比较 = 结果 − 目标防护。自然 1 走严重失手，优先于数值档。──
CRIT_MARGIN = 10      # ≥ +10 暴击
SOLID_MARGIN = 6      # +6 ~ +9 重击
GRAZE_MARGIN = -2     # −1 ~ −2 擦过；≤ −3 落空
_ATTACK_GRADE_ZH = {"crit": "暴击", "solid": "重击", "hit": "命中",
                    "graze": "擦过", "miss": "落空", "fumble": "严重失手"}

# ── 严重失手表（docs/system/02A 第五节「严重失手」，d6）──────────────────
# 与 data/system/tactics_combat.json fumble.rows 一致；规则层只做
# 「不造成伤害 + 攻击者获得 1 层【失衡】 + 掷出后果文本」，
# 后果演出（误伤队友、武器位置等）归导引者模块。
FUMBLE_TABLE = (
    "武器脱手，落在距离你 1d4 米的位置，需 1 AP 捡回",
    "你的攻击破坏了掩体，自己失去了它",
    "你跌进了敌人的控制区，对方获得一次免费的攻击机会",
    "武器卡住／卡壳，需要 1 AP 修理后才能再次使用",
    "你误伤了相邻的一个队友（造成一半伤害）",
    "你暴露了自己的意图和位置，敌人获得针对你的 +1 骰（持续一回合）",
)

# ── 态势与掩体的防护修正（docs/system/01 第七节 / 02A 第五节掩体表）──────
STANCE_GUARD = {"攻势": -2, "守势": 3, "游势": 1, "专注势": 0}
COVER_GUARD = {"轻度": 2, "中度": 4, "重度": 6}   # 「完全」掩体 = 无法被攻击

# ── 状态对防护的修正（docs/system/01 第五节 / 第八节破韧表）──────────────
CONDITION_GUARD = {"护持": 3, "破绽": -3, "过载中": -2}
BREAK_GUARD_PENALTY = -4        # 破韧：防护 −4
BOSS_PHASE1_GUARD = -2          # 首领暴怒形态：防护 −2
BOSS_PHASE3_GUARD = -6          # 首领崩解形态：防护 −6（真正的破韧）

# ── 韧性（docs/system/01 第八节）─────────────────────────────────────────
# 韧性上限 = 体魄 × 3 + 体型加值 + 职途／物种加值；每回合开始恢复上限 25%
# （向上取整）；韧性归零 → 破韧；重整 DF 12，成功脱离并恢复至上限 50%。
POISE_SIZE_BONUS = {"小型": 0, "中型": 6, "大型": 12, "巨兽": 20, "首领": 30}
POISE_REGEN_NUM = 1
POISE_REGEN_DEN = 4
RALLY_DF = 12
BOSS_SHIELDS = 3                # 首领：3 层韧性护盾
BOSS_SHIELD_RESTORE = 3         # 护盾消耗后恢复至韧性上限的 60%（3/5 → 分子 3）
BOSS_SHIELD_DEN = 5
BOSS_PHASE2_DAMAGE = 2          # 绝望形态：全部伤害 +2
BOSS_PHASE2_VITALITY = 3        # 绝望形态：每回合开始消耗 3 点活力换 1 AP

# ── 敌人四档模板（docs/system/02A 第九节 / data/system/tactics_enemies.json）
# damage 为（伤害骰记法, 固定加值）二元组；budget 同时是该敌体的经验权重
# （源文档未写敌体经验值，暂以遭遇预算权重计，见 PR 的 TODO 清单）。
ENEMY_TIERS = {
    "minion": {"name": "杂兵", "vitality": 12, "guard": 12, "poise": 8,
               "attack_bonus": 3, "damage": ("1d6", 1), "ap": 3, "df": 10,
               "budget": 1},
    "standard": {"name": "标准", "vitality": 26, "guard": 14, "poise": 14,
                 "attack_bonus": 5, "damage": ("1d8", 2), "ap": 3, "df": 12,
                 "budget": 3},
    "elite": {"name": "精锐", "vitality": 48, "guard": 16, "poise": 24,
              "attack_bonus": 7, "damage": ("2d8", 3), "ap": 3, "df": 14,
              "budget": 7},
    "boss": {"name": "首领", "vitality": 110, "guard": 18, "poise": 40,
             "attack_bonus": 9, "damage": ("3d8", 4), "ap": 4, "df": 17,
             "budget": 20, "shields": BOSS_SHIELDS},
}
# 按队伍等级缩放（02A:494-503）：（等级下限, 上限, 活力, 防护, 攻击, 伤害）。
ENEMY_SCALING = ((1, 1, 0, 0, 0, 0), (2, 3, 6, 0, 1, 1), (4, 5, 14, 1, 2, 2),
                 (6, 7, 24, 2, 3, 4), (8, 9, 38, 3, 5, 6),
                 (10, 12, 55, 4, 7, 9))
# 敌人修饰词缀（02A:513-528）：budget 计入遭遇预算；数值型词缀在
# make_enemy 落地，行为型词缀（控场 / 召唤 / 恐惧光环……）只登记名目，
# 行为演出归导引者模块（docs/system/04）。
ENEMY_AFFIXES = {
    "迅捷": {"budget": 1}, "装甲": {"budget": 1}, "狂暴": {"budget": 1},
    "远程": {"budget": 1}, "再生": {"budget": 2}, "控场": {"budget": 2},
    "召唤": {"budget": 3}, "隐伏": {"budget": 2}, "分身": {"budget": 2},
    "反伤": {"budget": 1}, "护主": {"budget": 1}, "恐惧光环": {"budget": 2},
    "适应": {"budget": 2}, "巨型": {"budget": 5},
}
AFFIX_LIMIT = 4                 # 词缀叠加不得超过 4 个（02A:530）

# ── 遭遇预算（docs/system/02A 第十节 / data/system/tactics_encounter.json）──
# 预算上限 = 队伍人数 × (队伍平均等级 + 2)；强度档按占比折算。
ENCOUNTER_STRENGTH = {
    "entangled": 0.4, "纠缠": 0.4,
    "standard": 0.7, "标准": 0.7,
    "harsh": 1.0, "严酷": 1.0,
    "deadly": 1.4, "致命": 1.4,
}
BOSS_BUDGET_SHARE = 0.6         # 单个首领的预算 ≤ 本场总预算的 60%
MINION_CAP = 6                  # 杂兵数量建议 ≤ 6（02A:577）


def attack_grade(margin: int, *, natural: int | None = None) -> str:
    """攻击解算：比较 = 结果 − 目标防护 → 六档之一（02A 附录速查表）。

      ≥ +10 暴击 ｜ +6 ~ +9 重击 ｜ 0 ~ +5 命中 ｜ −1 ~ −2 擦过 ｜
      ≤ −3 落空 ｜ 自然 1 严重失手（优先于数值档）。
    """
    if natural is not None and int(natural) == 1:
        return "fumble"
    margin = int(margin)
    if margin >= CRIT_MARGIN:
        return "crit"
    if margin >= SOLID_MARGIN:
        return "solid"
    if margin >= 0:
        return "hit"
    if margin >= GRAZE_MARGIN:
        return "graze"
    return "miss"


def resolve_attack(session: "RuleSession", attacker: dict | None,
                   target: dict | None, *, notation: str = "1d6",
                   damage_bonus: int = 0, attack_bonus: int = 0,
                   advantage: int = 0, disadvantage: int = 0, tag: str = "",
                   rng=None) -> dict:
    """一次攻击的完整解算：掷骰 → 比防护 → 伤害 → 韧性削减。

    按 docs/system/02A 第五节与附录速查表：
      · 暴击：掷两次伤害骰取较高 + 正常加值，并附加 1 层【失衡】
        （02A:223「必定造成横向的额外状态」；来源默认【失衡】）；
      · 重击：正常伤害 + 2；
      · 命中：正常伤害；
      · 擦过：一半伤害（向下取整），不触发附加效果（含韧性削减）；
      · 落空：无伤害；
      · 严重失手（自然 1）：无伤害，攻击者获得 1 层【失衡】，并掷失手表。
    韧性削减按 docs/system/01 第八节：普通命中减伤害值的一半（向下取整），
    暴击减全额。伤害结算统一走 deal_damage（唯一伤害出口）。
    高地（I5 战术投影）：会话接了战术地图且攻击者对目标占高地时，
    自动 +1 枚助势骰（02A 附录「常用情境修正」）；没接地图无此修正。
    """
    out = {"grade": "miss", "grade_zh": _ATTACK_GRADE_ZH["miss"],
           "attack_total": 0, "guard": 0, "margin": 0, "roll": None,
           "damage": 0, "poise": None, "events": [], "text": ""}
    if attacker is None or target is None:
        return out
    # 高地情境修正（docs/system/02A 附录「常用情境修正」：高地 = +1 枚
    # 助势骰）。有地图时向地图询问投影（I5）；没接地图返回 False，
    # 行为与 M2b 默认完全一致。公式本身不变——情境修正是公式的一列。
    high = tactical_high_ground(session, attacker, target)
    if high:
        advantage = int(advantage or 0) + 1
        out["high_ground"] = True
        out["events"].append({"type": "high_ground",
                              "attacker": attacker.get("name")})
    attack = roll("1d20", advantage=advantage, disadvantage=disadvantage,
                  rng=rng)
    total = int(attack["total"]) + int(attack_bonus or 0)
    guard = compute_guard(target, session)
    grade = attack_grade(total - guard, natural=attack.get("natural"))
    out.update(grade=grade, grade_zh=_ATTACK_GRADE_ZH[grade],
               attack_total=total, guard=guard, margin=total - guard,
               roll=attack)

    if grade == "fumble":
        row = int((rng or random).randint(1, 6))
        out["fumble_row"] = row
        out["fumble_text"] = FUMBLE_TABLE[row - 1]
        add_condition(session, attacker, "失衡")
        out["text"] = f"严重失手：{out['fumble_text']}"
        out["events"].append({"type": "fumble", "attacker": attacker.get("name"),
                              "row": row})
        return out
    if grade == "miss":
        out["text"] = "攻击落空。"
        out["events"].append({"type": "miss", "attacker": attacker.get("name"),
                              "target": target.get("name")})
        return out

    base = roll(notation, rng=rng)["total"] + int(damage_bonus or 0)
    if grade == "crit":
        second = roll(notation, rng=rng)["total"] + int(damage_bonus or 0)
        damage = max(base, second)
    elif grade == "solid":
        damage = base + 2
    elif grade == "graze":
        damage = base // 2
    else:
        damage = base
    # 首领绝望形态：全部伤害 +2（docs/system/01 第八节阶段表）。
    if int(attacker.get("boss_phase") or 0) >= 2:
        damage += BOSS_PHASE2_DAMAGE

    result = deal_damage(session, target, damage, tag=tag,
                         crit=(grade == "crit"))
    out["damage"] = int(result["lost"])
    out["damage_result"] = result
    out["events"].append({"type": "damage", "attacker": attacker.get("name"),
                          "target": target.get("name"), "grade": grade,
                          "damage": out["damage"]})

    # 韧性削减（docs/system/01 第八节）：普通命中 = 伤害的一半（向下取整），
    # 暴击 = 全额；擦过「不触发任何附加效果」→ 不削韧性；吸收反弹不削。
    if grade != "graze" and result["relation"] != "absorb" \
            and int(result.get("effective") or 0) > 0:
        effective = int(result["effective"])
        reduction = effective if grade == "crit" else effective // 2
        out["poise"] = apply_poise_damage(session, target, reduction)

    # 暴击的横向状态（02A:223）；弱点伤害的【失衡】已由 deal_damage 施加。
    if grade == "crit":
        add_condition(session, target, "失衡")
    return out


def max_poise(unit: dict | None) -> int:
    """韧性上限（docs/system/01 第八节）：体魄 × 3 + 体型加值 + 职途／物种加值。

    敌体模板直接给出韧性数值（tactics_enemies.json），存于 unit["max_poise"]
    时优先使用；否则按公式推导（体型缺省按中型 +6）。
    """
    if not unit:
        return 0
    template = int(unit.get("max_poise") or 0)
    if template > 0:
        return template
    vigor = int((unit.get("attributes") or {}).get("VIG", 4) or 4)
    size = str(unit.get("size") or "中型")
    return vigor * 3 + int(POISE_SIZE_BONUS.get(size, POISE_SIZE_BONUS["中型"])) \
        + int(unit.get("poise_bonus") or 0)


def apply_poise_damage(session: "RuleSession", unit: dict | None,
                       amount: int) -> dict | None:
    """削减韧性；归零时进入破韧（或消耗首领护盾并阶段转化）。

    返回 {poise, max, broken, shield_used, phase}；单位没有韧性数据
    （上限 0）时返回 None——不是所有单位都需要第二货币。
    首领阶段转化（docs/system/01 第八节）：第 1 次破韧 → 暴怒形态，
    第 2 次 → 绝望形态，第 3 次 → 崩解形态（真正的破韧，不再恢复韧性）。
    """
    if not unit or int(amount or 0) <= 0 or max_poise(unit) <= 0:
        return None
    cap = max_poise(unit)
    unit["poise"] = max(0, int(unit.get("poise") or 0) - int(amount))
    out = {"poise": int(unit["poise"]), "max": cap, "broken": False,
           "shield_used": 0, "phase": int(unit.get("boss_phase") or 0)}
    if unit["poise"] > 0:
        return out
    # 韧性归零：首领先消耗护盾（每层立即恢复至上限 60%）；护盾耗尽的那
    # 一次破韧即崩解形态——真正的破韧，不再恢复韧性（01 第八节阶段表）。
    shields = int(unit.get("poise_shields") or 0)
    breaks = int(unit.get("boss_breaks") or 0) + 1
    unit["boss_breaks"] = breaks
    if shields > 0:
        unit["poise_shields"] = shields - 1
        out["shield_used"] = 1
        out["phase"] = min(3, breaks)
        unit["boss_phase"] = out["phase"]
        if unit["poise_shields"] == 0:
            unit["poise"] = 0
            unit["poise_broken"] = True
            out["broken"] = True
            out["poise"] = 0
        else:
            unit["poise"] = -(-cap * BOSS_SHIELD_RESTORE // BOSS_SHIELD_DEN)
            out["poise"] = int(unit["poise"])
    else:
        unit["poise_broken"] = True
        unit["poise"] = 0
        if breaks >= 3:
            # 无护盾却累计三次破韧的场合同样进入崩解口径；普通单位
            # （breaks == 1）只是普通破韧，不挂阶段标记。
            out["phase"] = 3
            unit["boss_phase"] = 3
        out["broken"] = True
    session.touch()
    return out


def poise_regen_amount(unit: dict | None) -> int:
    """回合开始的韧性自然恢复量：上限的 25% 向上取整（docs/system/01 第八节）。

    破韧期间与首领崩解形态（不再恢复韧性）不恢复。
    """
    if not unit or unit.get("poise_broken") or int(unit.get("boss_phase") or 0) >= 3:
        return 0
    cap = max_poise(unit)
    if cap <= 0:
        return 0
    return -(-cap * POISE_REGEN_NUM // POISE_REGEN_DEN)


def rally_broken(session: "RuleSession", unit: dict | None, *,
                 rng=None) -> dict:
    """破韧单位的【重整】尝试（docs/system/01 第八节破韧表）。

    d20 + 体魄修正 vs DF 12（与濒危挣扎同构的写法；源文档写作
    「DF 12 + 体魄修正」，此处按挣扎循环的口径实现，见 PR 对照表）。
    成功 → 脱离破韧，韧性恢复至上限的 50%（向上取整）。
    """
    out = {"rallied": False, "total": 0, "df": RALLY_DF}
    if not unit or not unit.get("poise_broken"):
        return out
    if int(unit.get("boss_phase") or 0) >= 3:
        return out    # 崩解形态：就此不再恢复韧性（01 第八节）
    vigor = int((unit.get("attributes") or {}).get("VIG", 4) or 4)
    total = roll("1d20", rng=rng)["total"] + attribute_modifier(vigor)
    out["total"] = total
    if total >= RALLY_DF:
        unit["poise_broken"] = False
        unit["poise"] = -(-max_poise(unit) // 2)
        out["rallied"] = True
        session.touch()
    return out


# 仅本场战斗有效的临时增益字段。TODO(M2)：按 docs/system/01 第七 / 八节与
# 02A 场地要素列出（能力与场地赋予的临时修正）；所有战斗出口都会清理，
# 漏掉任何一条都会让上一场的修正常驻。
COMBAT_BUFF_FIELDS: tuple[str, ...] = ()


def start_combat(session: RuleSession, *, difficulty: str = "",
                 enemies: list[dict] | None = None,
                 strength: str = "") -> dict:
    """开始一场遭遇。**内容由服务端决定**，玩家只能选择「打不打」。

    敌体列表缺省时走遭遇生成（build_encounter，按 docs/system/02A 第十节
    的预算与强度档）；`difficulty` 兼容作为强度档名传入。生成器拿不到
    预算（无队伍）时返回空列表并报错，调用方必须显式传入敌体。

    开局后必须**立刻推进一次**：先攻最高的可能是敌体，如果只顾好顺序就
    返回，没人去跑它的回合，战斗会从第一秒就静止。_advance 会一路推进
    到第一个需要玩家操作的位置才返回。

    开局结算（docs/system/01 第十一节）：创伤 4 层及以上的角色，每次
    进入战斗开局获得 1 层【恐惧】。
    """
    if session.combat and not session.combat.get("over"):
        raise ValueError("战斗还没结束")
    squad = list(enemies) if enemies is not None else build_encounter(
        session, difficulty, strength=strength)
    if not squad:
        raise ValueError("这里没有可交战的东西")
    session.combat = {
        "scene_id": str((session.scene or {}).get("id") or ""),
        "round": 1,
        "difficulty": str(difficulty or ""),
        "enemies": squad,
        "order": [],
        "inits": {},     # 先攻分：一次掷骰，整场战斗保持（01 第六节回合结构）
        "turn_i": 0,
        "acts": {},      # 本回合的去重表：{单位 key: 动作 / 标记}
        "events": [],    # 本回合结算出的事件（前端逐条播报）
        "over": "",      # "" / victory / defeat / fled
    }
    for unit in session.party:
        if int(unit.get("trauma") or 0) >= TRAUMA_FEAR_AT \
                and "恐惧" not in (unit.get("conditions") or []):
            add_condition(session, unit, "恐惧")
    _reorder_initiative(session)
    _advance(session)
    session.add_log("system", f"战斗开始：{len(squad)} 个敌体。", speaker="战斗")
    session.touch()
    return combat_view(session)


def encounter_budget(party_size: int, avg_level: float) -> int:
    """遭遇预算上限 = 队伍人数 × (队伍平均等级 + 2)（docs/system/02A:550）。"""
    size = max(1, int(party_size or 1))
    level = max(1.0, float(avg_level or 1))
    return int(size * (level + 2))


def strength_budget(total: int, strength: str = "standard") -> int:
    """按强度档折算预算（docs/system/02A:563-568）：纠缠 40% / 标准 70% /
    严酷 100% / 致命 140%，向上取整。未知档名直接报错，不静默降档。
    """
    ratio = ENCOUNTER_STRENGTH.get(str(strength or "").strip())
    if ratio is None:
        raise ValueError(f"未知遭遇强度档：{strength}"
                         f"（可用：{', '.join(ENCOUNTER_STRENGTH)}）")
    return -(-round(int(total) * ratio * 100) // 100)


def make_enemy(tier: str, *, level: int = 1, name: str = "",
               affixes: tuple = (), persona: str = "",
               unit_id: str = "") -> dict:
    """按四档模板造一个敌体（docs/system/02A 第九节 + tactics_enemies.json）。

    词缀取白名单（ENEMY_AFFIXES），数量上限 4（02A:530）；数值型词缀
    （迅捷 / 装甲 / 狂暴 / 巨型 / 再生）在此落地，行为型词缀只登记名目。
    敌体没有濒危挣扎：活力归零即被击倒（no_struggle 标记）。
    """
    if tier not in ENEMY_TIERS:
        raise ValueError(f"未知敌体档位：{tier}"
                         f"（可用：{', '.join(ENEMY_TIERS)}）")
    if len(affixes) > AFFIX_LIMIT:
        raise ValueError(f"词缀叠加不得超过 {AFFIX_LIMIT} 个（02A:530）")
    template = ENEMY_TIERS[tier]
    level = max(1, min(12, int(level or 1)))
    scale = next((row for row in ENEMY_SCALING if row[0] <= level <= row[1]),
                 ENEMY_SCALING[0])
    vitality = template["vitality"] + scale[2]
    guard_total = template["guard"] + scale[3]
    attack_bonus = template["attack_bonus"] + scale[4]
    dice, flat = template["damage"]
    flat += scale[5]

    affix_list = [str(a) for a in affixes]
    unknown = [a for a in affix_list if a not in ENEMY_AFFIXES]
    if unknown:
        raise ValueError(f"未知敌体词缀：{', '.join(unknown)}")
    xp = int(template["budget"]) + sum(int(ENEMY_AFFIXES[a]["budget"])
                                       for a in affix_list)

    unit = new_unit(unit_id or f"enemy-{tier}-{name or level}",
                    name or f"{template['name']}（LV{level}）")
    unit["side"] = "enemy"
    unit["no_struggle"] = True        # 敌体无濒危挣扎：活力归零即被击倒
    unit["level"] = level
    unit["tier"] = tier
    unit["attack_bonus"] = attack_bonus
    unit["damage_notation"] = dice
    unit["damage_bonus"] = flat
    unit["ap"] = int(template["ap"])
    unit["df"] = int(template["df"])
    unit["xp"] = xp                   # 经验权重 = 预算（TODO：源文档未写敌体经验）
    unit["persona"] = str(persona or "")
    # 模板防护换算成「基础 10 + 天生护甲」挂进装备，guard_breakdown 同源。
    armor_guard = guard_total - GUARD_BASE
    unit["equipment"] = {"armor": {"name": "天生护甲", "guard": armor_guard}}
    # 韧性直接采用模板数值（02A:483）；首领附带 3 层韧性护盾。
    unit["max_poise"] = int(template["poise"])
    unit["poise"] = int(template["poise"])
    if template.get("shields"):
        unit["poise_shields"] = int(template["shields"])
    # 数值型词缀（02A:515-528）。
    for affix in affix_list:
        if affix == "迅捷":
            unit["init_bonus"] = int(unit.get("init_bonus") or 0) + 3
        elif affix == "装甲":
            unit["equipment"]["armor"]["guard"] += 3
        elif affix == "狂暴":
            unit["damage_bonus"] += 3
            unit["equipment"]["armor"]["guard"] -= 2
        elif affix == "巨型":
            vitality *= 2
        elif affix == "再生":
            unit["regen_amount"] = 3
    if vitality != int(unit.get("max_vitality") or 0):
        unit["max_vitality"] = vitality
        unit["vitality"] = vitality
    unit["affixes"] = affix_list
    unit["guard"] = compute_guard(unit)   # 词缀改过护甲后统一重算
    return unit


def build_encounter(session: RuleSession, difficulty: str = "",
                    *, strength: str = "") -> list[dict]:
    """按遭遇预算生成敌体列表（缺省遭遇生成器）。

    预算与强度档按 docs/system/02A 第九、十节与
    data/system/tactics_encounter.json 落地：
      · 预算上限 = 队伍人数 × (队伍平均等级 + 2)；强度档缺省按「标准 70%」；
      · 组成限制：同类敌人 ≤ max(2, 队伍人数 × 1.5)；单个首领 ≤ 总预算 60%；
        杂兵 ≤ 6；至少两类不同的敌人（不足时给首个敌体挂【迅捷】词缀补足
        「或至少一个带词缀的个体」这一条）。
    填充顺序 首领 → 精锐 → 标准 → 杂兵，确定性可复现（不掷骰）。
    """
    party = session.party or []
    size = max(1, len(party))
    levels = [int(unit.get("level") or 1) for unit in party] or [1]
    avg = sum(levels) / len(levels)
    total = encounter_budget(size, avg)
    key = str(strength or difficulty or "").strip()
    ratio = ENCOUNTER_STRENGTH.get(key, ENCOUNTER_STRENGTH["standard"]) \
        if key else ENCOUNTER_STRENGTH["standard"]
    budget = -(-round(total * ratio * 100) // 100)

    enemies: list[dict] = []
    spent = 0
    counts: dict[str, int] = {}
    same_cap = max(2, int(size * 1.5))          # 同类敌人上限（向下取整，最低 2）
    avg_level = max(1, int(avg))
    for tier in ("boss", "elite", "standard", "minion"):
        template = ENEMY_TIERS[tier]
        cost = int(template["budget"])
        if tier == "boss" and cost > total * BOSS_BUDGET_SHARE:
            continue                             # 首领不得超过总预算的 60%
        while spent + cost <= budget \
                and counts.get(tier, 0) < same_cap \
                and not (tier == "minion" and counts.get("minion", 0) >= MINION_CAP):
            index = counts.get(tier, 0) + 1
            enemies.append(make_enemy(
                tier, level=avg_level,
                name=f"{template['name']}·{index}",
                unit_id=f"{tier}-{index}"))
            counts[tier] = index
            spent += cost
    if enemies and len(counts) < 2 and "迅捷" not in (enemies[0].get("affixes") or []):
        # 组成限制 3：至少两类不同的敌人（或至少一个带词缀的个体）。
        enemies[0]["affixes"] = list(enemies[0].get("affixes") or []) + ["迅捷"]
        enemies[0]["init_bonus"] = int(enemies[0].get("init_bonus") or 0) + 3
        enemies[0]["xp"] = int(enemies[0].get("xp") or 0) \
            + int(ENEMY_AFFIXES["迅捷"]["budget"])
    return enemies


def initiative_adjustment(unit: dict | None) -> int:
    """先攻调整 = 洞察修正 + 灵巧修正的一半（向下取整）（01 第二节 / 02A:275）。"""
    attrs = (unit or {}).get("attributes") or {}
    insight = attribute_modifier(int(attrs.get("INS", 4) or 4))
    finesse = attribute_modifier(int(attrs.get("FIN", 4) or 4))
    return insight + finesse // 2


def roll_initiative(unit: dict | None, *, rng=None) -> int:
    """先攻掷骰 = d20 + 洞察修正 + 灵巧修正的一半（向下取整）（02A:275）。

    词缀 / 职途提供的额外先攻加值经 unit["init_bonus"] 折入。
    """
    return roll("1d20", rng=rng)["total"] + initiative_adjustment(unit) \
        + int((unit or {}).get("init_bonus") or 0)


def _unit_key(side: str, unit_id: str) -> str:
    return f"{side}:{unit_id}"


def _reorder_initiative(session: RuleSession, *, rng=None) -> None:
    """重排行动顺序，并清零本回合的去重表。

    先攻分按 docs/system/02A:275 落地（d20 + 洞察修正 + 灵巧修正的一半）；
    一次掷骰，整场战斗保持这个顺序（docs/system/01 第六节回合结构）——
    掷过的分存在 combat["inits"]，跨回合重排只重算名单不重掷。平手时按
    单位 key 稳定排序（源文档未写先攻平手规则，取确定性顺序，见 PR）。
    """
    combat = session.combat
    if not combat:
        return
    inits = combat.setdefault("inits", {})
    candidates: list[tuple[str, str, str, dict]] = []
    for unit in session.party:
        if unit is not None and not unit.get("dead"):
            candidates.append(("party", str(unit.get("id") or ""),
                               str(unit.get("name") or ""), unit))
    for index, enemy in enumerate(combat.get("enemies") or []):
        if enemy is not None and not enemy.get("dead"):
            candidates.append(("enemy", str(index),
                               str(enemy.get("name") or ""), enemy))
    units: list[dict] = []
    for side, unit_key, name, unit in candidates:
        key = _unit_key(side, unit_key)
        if key not in inits:
            inits[key] = roll_initiative(unit, rng=rng)
        units.append({"key": key, "side": side, "id": unit_key,
                      "name": name, "init": int(inits[key])})
    units.sort(key=lambda entry: (-int(entry.get("init") or 0), entry["key"]))
    combat["order"] = units
    combat["turn_i"] = 0


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
    """胜负检查点：敌体全倒 → 胜利；全队消亡 → 失败。

    这是一个**共用检查点**：任何单位行动完、以及恢复中的战斗每次推进前
    都先过这里。漏掉任何一条出口，战斗就会停在「胜负已分、状态却没标记」
    的死局里——界面还显示进行中，玩家却做什么都不对。

    失败判据用「全部消亡」而不是「全部濒危」：濒危角色还能挣扎稳定、
    还能被救（01 第十一节），全倒即判负会让挣扎循环形同虚设。
    濒危全队被敌体补刀的死亡螺旋由 _pick_enemy_target 的目标池兜底。
    """
    combat = session.combat
    if not combat or combat.get("over"):
        return True
    if all(enemy.get("dead") for enemy in combat.get("enemies") or []):
        _end_combat(session, "victory")
        return True
    if not _alive_party(session):
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
            # 重排是重新赋值 order（先攻分沿用 combat["inits"]，不重掷），
            # 旧下标作废，必须回到 0。
            index = 0
        entry = combat["order"][index]
        unit = _order_unit(session, entry)
        if unit is None or unit.get("dead"):
            index += 1
            continue
        # 去重表：少了它，顺序回绕时同一单位会在同一回合里再动一次。
        if combat["acts"].get(entry["key"]):
            index += 1
            continue
        # 濒危挣扎（01 第十一节）：濒危单位的回合开始时掷 d20 + 体魄修正
        # vs DF 12；3 成稳定 / 3 败消亡。挣扎同样消耗本回合。
        if unit.get("downed"):
            result = downed_struggle(session, unit)
            combat["acts"][entry["key"]] = "struggle"
            combat["events"].append({
                "type": "downed_struggle", "unit": unit.get("name"),
                "success": result["success"], "successes": result["successes"],
                "fails": result["fails"], "stabilized": result["stabilized"],
                "dead": result["dead"]})
            if _check_combat_end(session):
                return
            index += 1
            continue
        # 失衡 3 层 → 自动倒地，失去 1 个回合全部 AP（01 第五节叠加规则 2）。
        layers = int((unit.get("condition_layers") or {}).get("失衡") or 0)
        if layers >= CONDITION_MAX_LAYERS:
            unit.setdefault("condition_layers", {})["失衡"] = 0
            combat["acts"][entry["key"]] = "prone"
            combat["events"].append({"type": "prone", "unit": unit.get("name")})
            session.add_log("system", f"{unit.get('name')} 失衡叠加至 3 层，倒地了。",
                            speaker="战斗")
            index += 1
            continue
        # 回合开始结算：流血 / 熵染伤害、再生、韧性自然恢复、首领阶段维持。
        for event in _turn_start(session, unit):
            combat["events"].append(event)
        # 回合开始的效果可能直接把人打倒 / 打死（流血、熵染）——
        # 该单位本回合就此消耗。
        if unit.get("dead") or unit.get("downed"):
            combat["acts"][entry["key"]] = "turn_start"
            if _check_combat_end(session):
                return
            index += 1
            continue
        if _check_combat_end(session):
            return
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


def _pick_enemy_target(session: RuleSession, enemy: dict | None = None) -> dict | None:
    """敌体的目标选择：战术人格优先（docs/system/04），倒下就换人。

    人格只影响**目标偏好**的数值代理（规则层没有伤害统计与位置数据）：
      · 猛攻 brute → 属性上最具攻击性（MGT + FIN 最高）的目标；
      · 控场 controller → 最灵活（FIN 最高）的目标；
      · 其他人格与缺省：当前行动角色优先（他刚暴露自己）。
    完整的六型人格行为卡（docs/system/04 敌体战术人格 24 张卡）的演出
    归导引者模块（M5），规则层只保证服务端自己选目标。
    """
    persona = str((enemy or {}).get("persona") or "")
    conscious = [unit for unit in session.party if _can_act_unit(unit)]
    if persona == "brute" and conscious:
        return max(conscious, key=lambda unit: int(
            (unit.get("attributes") or {}).get("MGT", 4)) + int(
            (unit.get("attributes") or {}).get("FIN", 4)))
    if persona == "controller" and conscious:
        return max(conscious, key=lambda unit: int(
            (unit.get("attributes") or {}).get("FIN", 4)))
    active = next((unit for unit in session.party
                   if str(unit.get("id") or "") == str(session.active_unit_id)),
                  None)
    if _can_act_unit(active):
        return active
    standing = next((unit for unit in session.party if _can_act_unit(unit)),
                    None)
    if standing:
        return standing
    # 没有站着的角色时也要能继续：濒危角色可以被补刀（挣扎失败的死亡螺旋）。
    return next((unit for unit in session.party
                 if not unit.get("dead") and unit.get("downed")), None)


def _enemy_turn(session: RuleSession, entry: dict) -> None:
    """一个敌体的一次行动：选招 → 掷骰 → 比防护 → deal_damage。"""
    combat = session.combat
    enemy = _order_unit(session, entry)
    if enemy is None or not _can_act_unit(enemy):
        return
    target = _pick_enemy_target(session, enemy)
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
    """敌体行动的结算（docs/system/02A 第五、九节）。

    选招：敌体数据自带 `maneuvers`（[{name, attack_bonus, damage,
    advantage}, ...]）时按回合轮换，否则用基础攻击（模板的攻击加值与
    伤害骰）。人格只影响目标选择（见 _pick_enemy_target）；行为卡的
    叙事演出归导引者模块（docs/system/04，M5 接线）。
    """
    if not _can_act_unit(enemy):
        return {"text": "", "events": []}
    attack_bonus = int(enemy.get("attack_bonus") or 0)
    notation = str(enemy.get("damage_notation") or "1d6")
    damage_bonus = int(enemy.get("damage_bonus") or 0)
    advantage = 0
    maneuver_name = "基础攻击"
    round_no = int((session.combat or {}).get("round", 1) or 1)
    maneuvers = enemy.get("maneuvers") or []
    if maneuvers:
        maneuver = maneuvers[(round_no - 1) % len(maneuvers)]
        if isinstance(maneuver, dict):
            maneuver_name = str(maneuver.get("name") or maneuver_name)
            attack_bonus += int(maneuver.get("attack_bonus") or 0)
            damage_bonus += int(maneuver.get("damage") or 0)
            advantage += int(maneuver.get("advantage") or 0)
    # 首领暴怒形态：攻击 +1 骰（docs/system/01 第八节阶段表）。
    if int(enemy.get("boss_phase") or 0) == 1:
        advantage += 1

    out = resolve_attack(session, enemy, target, notation=notation,
                         damage_bonus=damage_bonus,
                         attack_bonus=attack_bonus, advantage=advantage,
                         tag=str(enemy.get("damage_tag") or ""))
    parts = [f"{enemy.get('name')} 以{maneuver_name}攻击 "
             f"{target.get('name')}：{out['grade_zh']}"]
    if out["grade"] not in ("miss", "fumble"):
        parts.append(f"伤害 {out['damage']}")
    poise = out.get("poise")
    if isinstance(poise, dict):
        if poise.get("shield_used"):
            parts.append(f"破韧！{enemy.get('name')} 进入阶段 {poise['phase']}"
                         "（护盾消耗，韧性回升）")
        elif poise.get("broken"):
            parts.append(f"{enemy.get('name')} 的韧性被打崩了")
    out["text"] = "；".join(parts) + "。"
    return out


def _turn_start(session: RuleSession, unit: dict) -> list[dict]:
    """一个单位回合开始时的结算（docs/system/01 第五、八节）。

      · 【流血】：每回合开始损失 2 点活力；
      · 【熵染】：熵蚀伤害持续 1 点/回合；
      · 再生 / 敌体词缀「再生」：每回合开始恢复 regen_amount 点活力；
      · 韧性自然恢复：上限的 25%（向上取整）；
      · 首领绝望形态：每回合开始消耗 3 点活力换取额外 1 AP
        （AP 经济属回合层，这里只结算活力代价并出事件）。
    """
    events: list[dict] = []
    if unit is None or unit.get("dead"):
        return events
    conditions = unit.get("conditions") or []
    name = str(unit.get("name") or "")
    if "流血" in conditions:
        result = deal_damage(session, unit, 2, label="流血")
        events.append({"type": "bleed", "unit": name,
                       "damage": int(result["lost"]),
                       "downed": bool(result["downed"]),
                       "dead": bool(result["dead"])})
    if "熵染" in conditions:
        result = deal_damage(session, unit, 1, label="熵染", tag="熵蚀")
        events.append({"type": "taint", "unit": name,
                       "damage": int(result["lost"])})
    regen = int(unit.get("regen_amount") or 0)
    if regen > 0 and not unit.get("downed"):
        healed = heal_unit(session, unit, regen)
        if healed:
            events.append({"type": "regen", "unit": name, "healed": healed})
    amount = poise_regen_amount(unit)
    if amount > 0:
        cap = max_poise(unit)
        unit["poise"] = min(cap, int(unit.get("poise") or 0) + amount)
        events.append({"type": "poise_regen", "unit": name,
                       "poise": int(unit["poise"]), "max": cap})
    if int(unit.get("boss_phase") or 0) == 2:
        result = deal_damage(session, unit, BOSS_PHASE2_VITALITY,
                             label="绝望形态")
        events.append({"type": "boss_desperation", "unit": name,
                       "damage": int(result["lost"])})
    return events


def _end_combat(session: RuleSession, outcome: str) -> None:
    """战斗结束。经验结算与压力推进都在这里——玩家不能自己领奖。"""
    combat = session.combat
    if not combat:
        return
    combat["over"] = str(outcome)
    if outcome == "victory":
        # 只给**真正被击倒**的敌体经验；没打倒的不给。经验权重 = 敌体的
        # 遭遇预算（make_enemy 设置；源文档未写敌体经验值，见 PR 对照表）。
        xp_total = sum(int(enemy.get("xp") or 0)
                       for enemy in combat.get("enemies") or []
                       if enemy.get("dead"))
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
# 六·附 战术投影（ATLAS I5，Issue #67）
# ════════════════════════════════════════════════════════════════════════
#
# docs/system/02A 第六节：战斗默认走「心象剧场」（近／中／远、相邻／
# 脱离靠口头维护），战术地图是可选件。本节把「可选件」的插口开在
# 规则核心上：调用方用 attach_tactical_map 把一张**已有战术帧**
# （atlas_gen.generate_tactical 的产物）连同「单位 → 区域」的钉扎关系
# 接到会话上；此后规则核心在需要距离或高地时，向地图询问投影——
# 本层**只调用内核的 range_band 与 high_ground**（ATLAS-DESIGN.md
# §3.3 契约），外加读战术帧自己的 `space` 口径，不碰其他内核函数。
#
# 没接地图时，下面所有查询都返回 None / False，战斗结算与 M2b 默认
# 完全一致；接了地图也不改战斗公式本身——唯一的公式内接线是 02A
# 附录「常用情境修正」里的「高地 = +1 枚助势骰」，见 resolve_attack。

#: metric 战术帧一格约 6 米（ATLAS-DESIGN.md §5.6）；abstract 帧
#: 「一次走位」跨一区，米数不参与换算。
TACTICAL_CELL_METERS = 6


def attach_tactical_map(session: RuleSession, atlas: dict, frame_id: str,
                        unit_zones: dict | None = None) -> dict:
    """把一张已有战术帧接到会话上，返回投影块（同时存于 session.tactical）。

    参数
      atlas      活地图 dict（atlas.py 内核生成，战术帧已由
                 atlas_gen.generate_tactical 投出）；
      frame_id   战术帧 id（形如 "{world}/tactical/{房间}"）；
      unit_zones {unit_id: 区域 place_id}：把战斗单位钉到帧内区域上。

    校验只用内核 range_band：钉住的区域必须真的在本帧里（自问自答 =
    same；帧或区域不存在时 range_band 返回 far）。帧的 `space` 口径
    必须是 metric / abstract 之一（这就是「已有的战术帧」——投影帧
    由 generate_tactical 从地点帧复制口径）。

    地图是运行期状态，不进 session.snapshot()；落盘与恢复归 I4 的
    atlas 存档块，网页层加载后重新 attach。
    """
    frame = atlas.get("frames", {}).get(frame_id) if isinstance(atlas, dict) \
        else None
    if frame is None:
        raise ValueError("战术帧不存在：%s" % frame_id)
    space = str(frame.get("space") or "")
    if space not in ("metric", "abstract"):
        raise ValueError("帧的 space 口径必须是 metric / abstract：%r"
                         % (space,))
    zones = {str(key): str(value)
             for key, value in dict(unit_zones or {}).items()}
    import atlas as atlas_kernel  # 局部导入：保持 import prism_core 无副作用
    for uid, zone in zones.items():
        if atlas_kernel.range_band(atlas, frame_id, zone, zone) != "same":
            raise ValueError("单位 %s 钉住的区域不在帧 %s 内：%s"
                             % (uid, frame_id, zone))
    block = {"atlas": atlas, "frame_id": str(frame_id),
             "space": space, "unit_zones": zones}
    session.tactical = block
    session.touch()
    return block


def detach_tactical_map(session: RuleSession) -> None:
    """摘掉战术投影（战斗结束 / 换场景时调用）；没接地图时是空操作。"""
    if getattr(session, "tactical", None) is not None:
        session.tactical = None
        session.touch()


def tactical_map(session: RuleSession) -> dict | None:
    """会话的战术投影元信息：{frame_id, space, unit_zones}（不含活地图
    本体，深拷贝安全）；没接地图时返回 None。"""
    block = getattr(session, "tactical", None)
    if not block:
        return None
    return {"frame_id": block["frame_id"], "space": block["space"],
            "unit_zones": dict(block["unit_zones"])}


def _tactical_pin(session: RuleSession, unit: dict | None) -> str | None:
    """单位在战术帧里钉住的区域 id；没接地图 / 没钉住返回 None。"""
    block = getattr(session, "tactical", None)
    if not block or unit is None:
        return None
    return block["unit_zones"].get(str(unit.get("id") or "")) or None


def tactical_range_band(session: RuleSession, attacker: dict | None,
                        target: dict | None) -> str | None:
    """两单位在战术帧里的距离档（内核 range_band 的图距离投影）：
    same / adjacent / near / mid / far。

    没接地图、或任一单位没钉住 → None：调用方回到心象剧场默认
    （02A 第六节），不臆造位置。
    """
    za = _tactical_pin(session, attacker)
    zb = _tactical_pin(session, target)
    if not za or not zb:
        return None
    block = session.tactical
    import atlas as atlas_kernel
    return str(atlas_kernel.range_band(block["atlas"], block["frame_id"],
                                       za, zb))


def tactical_high_ground(session: RuleSession, attacker: dict | None,
                         target: dict | None) -> bool:
    """攻击者是否对目标占据高地（内核 high_ground 的投影：目标 z 更低、
    平面切比雪夫距离 ≤ 1、且有上 / 下或相邻连接）。

    没接地图或没钉住一律 False——高地修正消失，行为与默认一致。
    """
    za = _tactical_pin(session, attacker)
    zb = _tactical_pin(session, target)
    if not za or not zb:
        return False
    block = session.tactical
    import atlas as atlas_kernel
    return bool(atlas_kernel.high_ground(block["atlas"], block["frame_id"],
                                         za, zb))


def tactical_move_zones(session: RuleSession, meters: float) -> int | None:
    """把规则里的移动米数换成战术帧的跨区数。

    metric 帧：ceil(米 / TACTICAL_CELL_METERS)（§5.6「一区按 6 米计」）。
    abstract 帧：**不把米换成跨区**——一次走位跨一区，米数不参与，
    返回 None（ATLAS-DESIGN.md §5.6 / §6.3）。没接地图同样返回 None。
    """
    block = getattr(session, "tactical", None)
    if not block:
        return None
    if block["space"] == "abstract":
        return None
    amount = int(meters or 0)
    if amount <= 0:
        return 0
    return -(-amount // TACTICAL_CELL_METERS)   # 整数上取整 ceil


# ════════════════════════════════════════════════════════════════════════
# 七、派生值与明细
# ════════════════════════════════════════════════════════════════════════


def vitality_cap(unit: dict | None) -> int:
    """活力上限（docs/system/01 第二节 / data/system/derived.json）。

    活力上限 = (体魄 × 3) + 职途起始活力 + (角色等级 − 1) × 2。
    职途起始活力（+6 ~ +14）是世界模组数据，经 unit["career_vitality"]
    传入（缺省 0，不臆造具体职途数值）。3 层及以上【创伤】：上限 −5
    （docs/system/01 第十一节创伤表）。
    """
    if not unit:
        return 0
    vigor = int((unit.get("attributes") or {}).get("VIG", 4) or 4)
    level = max(1, int(unit.get("level") or 1))
    cap = vigor * 3 + int(unit.get("career_vitality") or 0) + (level - 1) * 2
    if int(unit.get("trauma") or 0) >= TRAUMA_VITALITY_PENALTY_AT:
        cap -= TRAUMA_VITALITY_PENALTY
    return max(0, cap)


def move_speed(unit: dict | None) -> int:
    """移动速度（docs/system/01 第二节）：6 + 灵巧修正 米，最低 3 米。

    修正来源（均出自源文档）：
      · 态势【游势】+3 米（01 第七节）；【加速】+3 米（01 第五节）；
      · 负载标签（derived.json load_rules）：总重达到「中」以上 −1 米；
        两件以上「重」−2 米（取代前者）；
      · 【负担过重】状态 −2 米；【迟滞】移动减半；【束缚】移动降为 0。
    """
    if not unit:
        return 0
    conditions = unit.get("conditions") or []
    if "束缚" in conditions:
        return 0
    finesse = attribute_modifier(
        int((unit.get("attributes") or {}).get("FIN", 4) or 4))
    speed = 6 + finesse
    if unit.get("stance") == "游势" or "游势" in conditions:
        speed += 3
    if "加速" in conditions:
        speed += 3
    loads = [str(label) for label in (unit.get("load") or [])]
    if loads.count("重") >= 2:
        speed -= 2
    elif "中" in loads:
        speed -= 1
    if "负担过重" in conditions:
        speed -= 2
    if "迟滞" in conditions:
        speed //= 2
    return max(3, speed)


def carry_capacity(unit: dict | None) -> dict:
    """负重上限（docs/system/01 第二节）：舒适 = 力道 × 5，最大 = 力道 × 10。

    超出舒适值后移动 −2 米、敏捷类判定 −1（状态化的【负担过重】由
    调用方施加，本函数只给阈值）。
    """
    might = int(((unit or {}).get("attributes") or {}).get("MGT", 4) or 4)
    return {"comfort": might * 5, "max": might * 10}


def focus_cap(unit: dict | None) -> int:
    """专注上限（docs/system/01 第十二节）：(心智与气场较高一项 × 2) + 职途加成。

    职途加成是世界模组数据，经 unit["career_focus_bonus"] 传入（缺省 0）。
    """
    attrs = (unit or {}).get("attributes") or {}
    best = max(int(attrs.get("MND", 4) or 4), int(attrs.get("PRE", 4) or 4))
    return best * 2 + int((unit or {}).get("career_focus_bonus") or 0)


def tempo_cap(party_size: int) -> int:
    """气势池 = 队伍人数 + 2（docs/system/01 第十二节；队伍共享，不存于单位）。"""
    return max(1, int(party_size or 1)) + 2


def equipped_items(unit: dict | None) -> dict:
    """按装备槽位取当前穿戴（只认白名单槽位）。

    装备条目为 {name, guard, ...} 的纯数据 dict（护甲的 Guard 值 0–6，
    docs/system/01 第二节）；敌体模板把天生护甲也挂成装备条目，保证
    防护展示与结算同源。
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
    """态势对防护的修正（docs/system/01 第七节）：攻势 −2 / 守势 +3 /
    游势 +1 / 专注势 0。
    """
    return int(STANCE_GUARD.get(str((unit or {}).get("stance") or ""), 0))


def _cover_guard_bonus(unit: dict) -> int:
    """掩体对防护的修正（docs/system/02A 第五节掩体表）：轻 +2 / 中 +4 /
    重 +6；「完全」掩体意味着无法被攻击，由调用方处理目标合法性。
    """
    return int(COVER_GUARD.get(str((unit or {}).get("cover") or ""), 0))


def _condition_guard_bonus(unit: dict) -> int:
    """状态与破韧对防护的修正（docs/system/01 第五、八节）。

      · 【护持】+3、【破绽】−3、【过载中】−2（01 第五节）；
      · 破韧：防护 −4（01 第八节破韧效果表）；
      · 首领阶段：暴怒形态 −2；崩解形态（真正的破韧）−6，优先于破韧的 −4。
    """
    if not unit:
        return 0
    total = sum(int(CONDITION_GUARD.get(str(name), 0))
                for name in (unit.get("conditions") or []))
    phase = int(unit.get("boss_phase") or 0)
    if phase >= 3:
        total += BOSS_PHASE3_GUARD
    elif unit.get("poise_broken"):
        total += BREAK_GUARD_PENALTY
    elif phase == 1:
        total += BOSS_PHASE1_GUARD
    return total


def guard_breakdown(unit: dict | None, session: RuleSession | None = None) -> dict:
    """把防护拆成可展示的几段：基础 + 装备 + 态势 + 掩体 + 状态。

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
                         ("掩体修正", _cover_guard_bonus(unit or {})),
                         ("状态修正", _condition_guard_bonus(unit or {}))):
        if value:
            parts.append({"label": label, "value": int(value)})
    total = max(0, sum(int(part["value"]) for part in parts))
    return {"total": total, "base": GUARD_BASE, "parts": parts}


def compute_guard(unit: dict | None, session: RuleSession | None = None) -> int:
    """结算用的防护值（= breakdown 的 total）。"""
    return guard_breakdown(unit, session)["total"]


def recompute_unit(unit: dict | None, session: RuleSession | None = None) -> None:
    """统一重算派生值；凡是会改变属性 / 等级 / 装备 / 创伤的路径都必须走这里。

    覆盖（docs/system/01 第二节与第十二节、data/system/derived.json）：
      · 防护（装备 + 态势 + 掩体 + 状态）；
      · 活力上限（体魄 / 等级 / 职途 / 创伤），并把当前活力夹回上限内；
      · 专注上限（写入 max_resources.focus；气势是队伍共享池，
        上限用 tempo_cap(队伍人数) 由会话层计算，不存于单位）；
      · 韧性上限（敌体模板优先），首次计算时以满韧性开局；
      · 应力夹回上限内（02B 第五节：上限 = 体魄 + 心智 + 5）。
    """
    if unit is None:
        return
    unit["guard"] = compute_guard(unit, session)
    cap = vitality_cap(unit)
    unit["max_vitality"] = cap
    unit["vitality"] = min(max(0, int(unit.get("vitality") or 0)), cap) \
        if cap > 0 else max(0, int(unit.get("vitality") or 0))
    unit.setdefault("max_resources", {})["focus"] = focus_cap(unit)
    poise_cap = max_poise(unit)
    if not int(unit.get("max_poise") or 0):
        # 未初始化（新建角色卡）：以满韧性开局。
        unit["max_poise"] = poise_cap
        if poise_cap > 0:
            unit["poise"] = poise_cap
    else:
        # 敌体模板等调用方已给出韧性数据的，只夹取不重置。
        unit["poise"] = min(int(unit.get("poise") or 0),
                            int(unit["max_poise"] or 0))
    # 应力夹回上限内（02B 第五节；超限触发崩溃只发生在 stress_gain，
    # 这里只做属性变化后的静默夹取）。
    unit["stress"] = min(max(0, int(unit.get("stress") or 0)),
                         stress_cap(unit))
    if session is not None:
        session.touch()


# ════════════════════════════════════════════════════════════════════════
# 八、成长
# ════════════════════════════════════════════════════════════════════════

# 等级门槛表：[(等级, 升入该级所需的累计经验), ...]。
# 经验点法（docs/system/01 第十四节「升级条件」）：积满 [当前等级 × 10]
# 点升级——1 级攒 10 点升 2 级，2 级再攒 20 点升 3 级……累计到 L 级
# 需 10 × (1 + 2 + … + (L−1)) = 5L(L−1) 点。里程碑法（推荐）不经此表，
# 由导引者直接给等级（M3+ 的会话层接口）。
LEVEL_XP_THRESHOLDS: tuple[tuple[int, int], ...] = tuple(
    (level, 5 * level * (level - 1)) for level in range(2, 13))

# 六类成长选择（docs/system/01 第十四节 / data/system/growth.json choices）。
GROWTH_CATEGORIES = ("A", "B", "C", "D", "E", "F")
GROWTH_ATTRIBUTE_CAP = 4     # A 类：通过此方式每项属性最多 +4
GROWTH_CONSECUTIVE_LIMIT = 2  # 同一类别不得连续选择超过 2 次


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

    每级固定收益（docs/system/01 第十四节 per_level）：活力上限 +2、
    成长选择任选 1 项（= 1 点）、熟练加值按量表（自动随等级生效）。
    发放只发生在这里——玩家只能消费已经发放的点数（见 assign_attribute /
    apply_growth_choice），没有「加等级 / 加点数」的入口。
    """
    return 1


def award_xp(session: RuleSession, unit: dict | None, amount: int) -> dict:
    """给经验；跨级时自动升级并发放成长点数。

    升级必须**把等级写回单位**（只算不写会出现「日志显示升级、角色卡
    还是旧等级」）。升级只做派生值重算与上限夹取；活力上限 +2 由
    recompute_unit 按公式（等级项）自动带入（docs/system/01 第十四节）。
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
    """消费成长点数调整属性：**服务端发放、玩家分配**（成长选择 A 类）。

    校验五件事：属性名在白名单内；结果不越 1–10 的界；delta 不为 0；
    手里确实有成长点数；A 类上限——通过成长每项属性最多 +4
    （docs/system/01 第十四节，历史加成记于 unit["growth_attr_gain"]）。
    任何一条不过都直接拒绝。
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
    if delta > 0:
        gains = unit.setdefault("growth_attr_gain", {})
        gained = int(gains.get(key, 0))
        if gained + delta > GROWTH_ATTRIBUTE_CAP:
            raise ValueError(
                f"{ATTRIBUTES[key]['zh']}通过成长已 +{gained}，"
                f"上限 +{GROWTH_ATTRIBUTE_CAP}（01 第十四节 A 类）")
    if int(unit.get("growth_points") or 0) < 1:
        raise ValueError("没有可分配的成长点数（由服务端在升级时发放）")
    unit["growth_points"] = int(unit["growth_points"]) - 1
    before_cap = int(unit.get("max_vitality") or 0)
    unit.setdefault("attributes", {})[key] = new
    if delta > 0:
        gains[key] = int(gains.get(key, 0)) + delta
    else:
        gains[key] = max(0, int(gains.get(key, 0)) + delta)
    history = unit.setdefault("growth_history", [])
    history.append("A")
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


def apply_growth_choice(session: RuleSession, unit_id: str, category: str,
                        payload=None) -> dict:
    """消费 1 点成长点数做一次**成长选择**（docs/system/01 第十四节）。

    六类选择（data/system/growth.json choices）：
      · A 属性强化：一项属性 +1（走 assign_attribute，含 +4 上限）；
      · B 新熟练：获得一项技能熟练；已有熟练则改为专精；
      · C 技能专精：一项已熟练的技能升级为专精（判定额外 +1）；
      · D 职途能力 / E 通用专长 / F 能力深化：效果由世界模组与导引者
        裁定，规则层只把 payload 记入 growth_records。
    限制：同一类别不得连续选择超过 2 次（growth_history 追踪）。
    玩家只能消费服务端已发放的点数；没有直接加点入口。
    """
    unit = next((p for p in session.party
                 if str(p.get("id") or "") == str(unit_id or "")), None)
    if unit is None:
        raise ValueError("单位不存在")
    category = str(category or "").strip().upper()
    if category not in GROWTH_CATEGORIES:
        raise ValueError(f"未知成长类别：{category}"
                         f"（可用：{'/'.join(GROWTH_CATEGORIES)}）")
    history = unit.setdefault("growth_history", [])
    if len(history) >= GROWTH_CONSECUTIVE_LIMIT \
            and all(item == category
                    for item in history[-GROWTH_CONSECUTIVE_LIMIT:]):
        raise ValueError(
            f"同一类别（{category}）不得连续选择超过 "
            f"{GROWTH_CONSECUTIVE_LIMIT} 次（01 第十四节）")
    if int(unit.get("growth_points") or 0) < 1:
        raise ValueError("没有可分配的成长点数（由服务端在升级时发放）")

    if category == "A":
        payload = payload or {}
        result = assign_attribute(session, unit_id,
                                  str(payload.get("attribute") or ""),
                                  int(payload.get("delta", 1) or 1))
        return result

    unit["growth_points"] = int(unit["growth_points"]) - 1
    out = {"category": category, "growth_points": int(unit["growth_points"])}
    if category == "B":
        skill = str((payload or {}).get("skill") or "").strip()
        if not skill:
            raise ValueError("B 类需要 payload.skill（技能名）")
        if skill in unit.get("specializations", []):
            raise ValueError("该技能已是专精")
        if skill in unit.get("proficiencies", []):
            unit.setdefault("specializations", []).append(skill)
            out["effect"] = "specialization"
        else:
            unit.setdefault("proficiencies", []).append(skill)
            out["effect"] = "proficiency"
    elif category == "C":
        skill = str((payload or {}).get("skill") or "").strip()
        if skill not in unit.get("proficiencies", []):
            raise ValueError("C 类专精要求该技能已有熟练")
        if skill in unit.get("specializations", []):
            raise ValueError("该技能已是专精")
        unit.setdefault("specializations", []).append(skill)
        out["effect"] = "specialization"
    else:  # D / E / F：世界模组与导引者裁定，规则层记录。
        if payload is None:
            raise ValueError(f"{category} 类需要 payload（世界模组数据）")
        unit.setdefault("growth_records", []).append(
            {"category": category, "payload": payload})
        out["effect"] = "recorded"
    history.append(category)
    session.add_log("system",
                    f"{unit.get('name', '')} 完成了一次 {category} 类成长选择。",
                    speaker="成长")
    session.touch()
    return out


# ════════════════════════════════════════════════════════════════════════
# 九、构建引擎（docs/system/02B 第一至五节 / data/system/build_*.json）
# ════════════════════════════════════════════════════════════════════════
#
# 第二套引擎：**d10 骰池 + 成功计数**。它与战术引擎（d20 路径，见
# judge_check / resolve_attack）**并列**、不混用——同一个团的所有人
# 应当使用同一套引擎（02B 第十一节「混用限制」）。判定四步（02B
# 第一节）：导引者宣布需求成功数 N → 掷骰池 → 清点成功数 S →
# 结果 = S − N。本节只落 02B 第一至五节；战斗中的应用（第六节）、
# 超载连锁（第七节）、符纹插槽 / 构建点（第八、九节）与双引擎互转
# （第十一节）留给 M2d，TODO 一并标注。

# ── 成功阈值与默认骰（02B 第一节）─────────────────────────────────────
# 骰池里的每一枚骰，点数 ≥ 5 记作 1 次成功；默认骰面 d10，骰面经
# 骰阶升级提升（见 BUILD_DIE_RANKS）。
BUILD_SUCCESS_THRESHOLD = 5
BUILD_DEFAULT_SIDES = 10

# 单骰成功率表（02B 第一节：d6 33% / d8 50% / d10 60% / d12 67%）。
# d20 的 80% 为推导值（16 面达标 / 20），源文档未列表——TODO(M2d)
# 与骰阶 V 一并确认。
BUILD_DIE_RATES = {6: 33, 8: 50, 10: 60, 12: 67}

# ── 骰池的构成（02B 第一节，四部分）──────────────────────────────────
# 骰池 = 主属性枚数 + 技能熟练枚数 + 技能专精 + 临时加成。
#   主属性枚数 = 属性值本身（属性 5 → 5 枚骰）；
#   熟练枚数 = 熟练加值的一半（向上取整）：+2 → +1 枚；+3 → +2 枚；
#   +4 → +2 枚；专精额外 +1 枚；临时加成（协助 / 态势 / 场地要素）
#   通常 1–3 枚——量级为参考值，具体枚数由导引者宣布。
BUILD_SPECIALIZATION_DICE = 1
BUILD_TEMP_DICE_TYPICAL = (1, 3)


def build_proficiency_dice(prof_bonus: int) -> int:
    """熟练加值 → 骰池枚数（02B 第一节）：加值的一半向上取整。"""
    bonus = int(prof_bonus or 0)
    if bonus < 0:
        raise ValueError("熟练加值不能为负")
    return (bonus + 1) // 2


def build_pool(attribute: int, prof_bonus: int, *,
               specialization: bool = False, temp: int = 0) -> dict:
    """构建引擎骰池的组成（02B 第一节，四部分）。

    返回 {attribute, proficiency, specialization, temp, total}；总数
    即掷骰枚数。构建引擎的加值几乎都转换为骰子枚数，而不是固定数字。
    """
    main = max(0, int(attribute or 0))
    prof = build_proficiency_dice(prof_bonus)
    spec = BUILD_SPECIALIZATION_DICE if specialization else 0
    extra = max(0, int(temp or 0))
    return {"attribute": main, "proficiency": prof,
            "specialization": spec, "temp": extra,
            "total": main + prof + spec + extra}


def build_die_rate(sides: int) -> int:
    """骰面 → 单骰成功率（%，02B 第一节表）。"""
    sides = int(sides or 0)
    rate = BUILD_DIE_RATES.get(sides)
    if rate is not None:
        return rate
    if sides == 20:
        return 80  # 推导值（16/20 达标）；源文档未列表，TODO(M2d) 确认。
    raise ValueError(f"未记录的骰面：d{sides}（源文档只列表 d6/d8/d10/d12）")


# ── 难度 = 需求成功数（02B 第一节：8 档 + 战术 DF 对照）──────────────
BUILD_REQUIRED_SUCCESSES = (
    (1, "平凡", 8), (2, "简易", 10), (3, "常规", 12), (4, "有挑战", 14),
    (5, "困难", 16), (6, "严峻", 18), (8, "极限", 20), (10, "传说", 24),
)


def build_difficulty(need: int) -> dict:
    """需求成功数 → 档名与战术引擎对应 DF（02B 第一节对照表）。"""
    need = int(need or 0)
    for value, name, df in BUILD_REQUIRED_SUCCESSES:
        if value == need:
            return {"need": need, "name": name, "tactics_df": df}
    raise ValueError(
        f"未知需求成功数：{need}（源文档只定义 8 档：1/2/3/4/5/6/8/10）")


# ── 五档结果梯度（02B 第二节）────────────────────────────────────────
# 结果 = 成功数 S − 需求成功数 N。档位命名与战术引擎相同，边界不同；
# 动量增益刻意让失败也有进账（灾难 +2、挫败 +1）——资源循环建立在
# 「动量消耗 → 反哺成功」之上，否则玩家会陷入死亡螺旋（02B 第二节
# 设计说明）。
BUILD_OUTCOME_MOMENTUM = {"triumph": 2, "success": 1, "narrow": 1,
                          "failure": 1, "catastrophe": 2}


def build_outcome(margin: int) -> str:
    """成功数差 → 五档结果（02B 第二节）。

    S − N ≥ +3 凯旋；+1 ~ +2 成功；0 险成；−1 挫败；≤ −2 灾难。
    """
    margin = int(margin or 0)
    if margin >= 3:
        return "triumph"
    if margin >= 1:
        return "success"
    if margin == 0:
        return "narrow"
    if margin == -1:
        return "failure"
    return "catastrophe"


# ── 骰阶与专精（02B 第三节：5 阶）────────────────────────────────────
BUILD_DIE_RANKS = (
    ("I", 6, "未受训的属性（新手角色）"),
    ("II", 8, "默认起始值：角色所有骰阶为 d8"),
    ("III", 10, "标准手法：每项 +1 枚对该属性的骰阶提升"),
    ("IV", 12, "需要投入大量构建点"),
    ("V", 20, "极限成就，通常在 8 级以后才可能触及"),
)
BUILD_STARTING_RANK = "II"   # 起始角色的所有骰阶为 d8（骰阶 II）

# 期望成功数速查表（02B 第三节，8 行 × d8/d10/d12）。
BUILD_EXPECTED_SUCCESS_TABLE = (
    (2, 1.0, 1.2, 1.3), (3, 1.5, 1.8, 2.0), (4, 2.0, 2.4, 2.7),
    (5, 2.5, 3.0, 3.3), (6, 3.0, 3.6, 4.0), (7, 3.5, 4.2, 4.7),
    (8, 4.0, 4.8, 5.3), (10, 5.0, 6.0, 6.7),
)


def build_rank_die(rank: str) -> int:
    """骰阶 → 骰面（02B 第三节）。"""
    key = str(rank or "").strip().upper()
    for name, sides, _unlock in BUILD_DIE_RANKS:
        if name == key:
            return sides
    raise ValueError(f"未知骰阶：{rank}（I–V）")


def build_expected_successes(pool: int, sides: int) -> float:
    """骰池枚数 × 骰面 → 期望成功数（02B 第三节速查表）。

    期望 = 枚数 × 达标率（(面值 − 阈值 + 1) / 面值），与速查表逐格
    吻合（见 tests）；表外组合由同一公式推导，不臆造。
    """
    pool = max(0, int(pool or 0))
    sides = int(sides or 0)
    if sides < BUILD_SUCCESS_THRESHOLD + 1:
        raise ValueError(
            f"骰面至少要有 {BUILD_SUCCESS_THRESHOLD + 1} 面（当前 d{sides}）")
    return round(pool * (sides - BUILD_SUCCESS_THRESHOLD + 1) / sides, 1)


def build_roll(pool: int, need: int, *, sides: int = BUILD_DEFAULT_SIDES,
               rng=None) -> dict:
    """掷一次构建引擎判定（02B 第一节判定四步 + 第二节两个特例）。

      · 每枚骰 ≥ 5 记 1 次成功；掷出**最大面值**的骰记 2 次成功
        （最大值爆发：高风险设计的天然回报）；
      · **全骰皆负**（没有任何一枚达到阈值，含空池）时，无论需求
        成功数是多少都至少视为挫败，且获得 1 点额外动量；
      · 动量增益按五档表（BUILD_OUTCOME_MOMENTUM）结算。

    返回纯数据 dict（可直接 JSON 落盘）：{pool, sides, need, dice,
    successes, burst, all_fail, margin, outcome, momentum, detail}。
    `rng` 供测试注入确定性随机（默认 Python 标准库的 random）。
    """
    pool = max(0, int(pool or 0))
    need = int(need or 0)
    source = rng or random
    dice = [int(source.randint(1, sides)) for _ in range(pool)]
    successes = 0
    burst = 0
    for value in dice:
        if value >= BUILD_SUCCESS_THRESHOLD:
            successes += 1
        if value == sides:      # 最大值爆发：该骰记 2 次成功
            successes += 1
            burst += 1
    all_fail = not any(value >= BUILD_SUCCESS_THRESHOLD for value in dice)
    margin = successes - need
    outcome = build_outcome(margin)
    if all_fail and outcome == "catastrophe":
        outcome = "failure"     # 全骰皆负：至少视为 −1（挫败）
    momentum = BUILD_OUTCOME_MOMENTUM[outcome] + (1 if all_fail else 0)
    detail = (f"骰池 {pool} 枚 d{sides}：{dice} → 成功 {successes}"
              f"（爆发 {burst} 枚）vs 需求 {need} → {margin:+d}")
    return {"pool": pool, "sides": sides, "need": need, "dice": dice,
            "successes": successes, "burst": burst, "all_fail": all_fail,
            "margin": margin, "outcome": outcome, "momentum": momentum,
            "detail": detail}


# ── 动量 Momentum（02B 第四节）───────────────────────────────────────
# 动量池 = 共享池（起始 0，上限 10）＋ 每人各自的持有上限 5；可以
# 在任何时候花费（包括他人的回合——时序归调用方处理）。每个场景
# 结束时共享池清零（个人持有源文档未写清零，不动——TODO(M2d) 复核）。
MOMENTUM_SHARED_START = 0
MOMENTUM_SHARED_CAP = 10
MOMENTUM_PERSON_CAP = 5
MOMENTUM_BOOST_MAX = 3    # 补强：单次判定最多追加 3 枚（02B 第四节）

# 获取动量的 7 个来源（02B 第四节；判定档位的三条已并入
# BUILD_OUTCOME_MOMENTUM，其余为叙事触发，由导引者宣布后入账）。
MOMENTUM_GAIN_SOURCES = (
    ("outcome_success", 1, "判定结果为成功"),
    ("outcome_triumph", 2, "判定结果为凯旋"),
    ("outcome_catastrophe", 2, "判定结果为灾难"),
    ("assist", 1, "队友的协助动作生效"),
    ("heavy_damage_taken", 1, "你承受了一次超过 10 点的伤害"),
    ("driven_choice", 1, "你演出了符合角色驱动、且对自己不利的抉择（导引者判定）"),
    ("enemy_morale_break", 2, "敌人发生一次士气崩溃（全队）"),
)

# 花费动量的 7 项（02B 第四节花费表）：id → (费用, 名称, 效果)。
MOMENTUM_SPENDS = {
    "boost": (1, "补强", "向正在进行的判定追加 1 枚骰（可多次，上限 3 枚）"),
    "reroll": (2, "重掷", "重掷池中所有未成功的骰子一次"),
    "upgrade": (2, "升阶", "本次判定中，把所有骰视为高一阶（d8→d10→d12）"),
    "rewrite": (1, "改写失败", "把一次 −1 结果提升为 0（险成）"),
    "extra_action": (3, "额外行动", "在当前时刻获得额外 1 AP"),
    "refresh": (2, "刷新", "一项本场景已消耗的能力可以再用一次"),
    "combo_boost": (1, "连携增强", "下一次连携完成时，额外获得 1 层连锁"),
}


def new_momentum_pool(members=()) -> dict:
    """动量池的纯数据结构：{"shared": 0, "members": {成员 id: 持有}}。

    纯 dict / int，可直接随会话快照落盘。
    """
    return {"shared": int(MOMENTUM_SHARED_START),
            "members": {str(m): 0 for m in (members or ())}}


def momentum_add(pool: dict, member: str | None, amount: int) -> dict:
    """动量入账（02B 第四节 7 源），返回 {shared, member, changed}。

    源文档只写明「共享池 0–10 / 个人持有 5 / 任何时候可花 / 场景结束
    共享池清零」，未写明入账与扣减的先后——本实现采用并已在 PR 中
    列为待复核项：增益先入该成员的持有（上限 5），溢出进共享池
    （上限 10）；`member=None` 表示全队来源（如敌人士气崩溃），直接
    进共享池。TODO(M2d)：导引者接线时复核该解释。
    """
    amount = int(amount or 0)
    if amount < 0:
        raise ValueError("动量入账不能为负（花费走 momentum_spend）")
    shared = int(pool.get("shared", 0) or 0)
    if member is None:
        new_shared = min(shared + amount, MOMENTUM_SHARED_CAP)
        pool["shared"] = new_shared
        return {"shared": new_shared, "member": None,
                "changed": new_shared - shared}
    key = str(member)
    members = pool.setdefault("members", {})
    held = int(members.get(key, 0) or 0)
    # 先填满个人持有（上限 5），溢出进共享池（上限 10），再溢出丢弃。
    to_member = min(amount, max(0, MOMENTUM_PERSON_CAP - held))
    to_shared = min(amount - to_member, max(0, MOMENTUM_SHARED_CAP - shared))
    members[key] = held + to_member
    pool["shared"] = shared + to_shared
    return {"shared": pool["shared"], "member": key,
            "changed": to_member + to_shared}


def momentum_spend(pool: dict, member: str, spend_id: str, *,
                   times: int = 1) -> dict:
    """花费动量（02B 第四节花费表，7 项）。

    先扣成员个人持有，不足部分从共享池补扣；合计不足则整体拒绝
    （不产生部分扣减）。补强（boost）可多次但单次判定最多追加
    MOMENTUM_BOOST_MAX 枚——次数记账归调用方，本函数只按 times 收费。
    """
    if spend_id not in MOMENTUM_SPENDS:
        raise ValueError(f"未知动量花费：{spend_id}"
                         f"（可用：{'/'.join(MOMENTUM_SPENDS)}）")
    times = int(times or 0)
    if times < 1:
        raise ValueError("花费次数至少为 1")
    cost = MOMENTUM_SPENDS[spend_id][0] * times
    key = str(member)
    members = pool.setdefault("members", {})
    held = int(members.get(key, 0) or 0)
    shared = int(pool.get("shared", 0) or 0)
    if held + shared < cost:
        raise ValueError(
            f"动量不足：需要 {cost}，{key} 持有 {held} + 共享 {shared}")
    from_member = min(held, cost)
    from_shared = cost - from_member
    members[key] = held - from_member
    pool["shared"] = shared - from_shared
    return {"spend": spend_id, "name": MOMENTUM_SPENDS[spend_id][1],
            "cost": cost, "member": key,
            "member_left": members[key], "shared_left": pool["shared"]}


def momentum_clear(pool: dict) -> dict:
    """场景结束：共享池中的动量清零（02B 第四节「动量清零」）。

    未使用的动量不会累积到下一幕；个人持有源文档未写清零，保持
    不动（TODO(M2d) 复核）。
    """
    before = int(pool.get("shared", 0) or 0)
    pool["shared"] = 0
    return {"cleared": before}


# ── 应力 Stress 与超载（02B 第五节）──────────────────────────────────
# 应力上限 = 体魄 + 心智 + 5（典型 13–21）；从 0 开始累积，存于
# unit["stress"]（new_unit 已给默认 0，recompute_unit 夹回上限内）。
STRESS_CAP_FLAT = 5

# 获得应力的 5 种方式（02B 第五节）：id → (应力, 行为)。
STRESS_GAINS = (
    ("overload_check", 2, "超载一次判定（宣布后重掷全池，取第二次结果）"),
    ("heavy_hit", 1, "受到一次超过自身活力上限 1/4 的伤害"),
    ("repeat_overload", 3, "连续第二次宣布超载（同一场景内）"),
    ("beyond_ability", 2, "使用超出自身能力等级的资源（强行动用未掌握的能力）"),
    ("desperate", 2, "处于濒危状态"),
)

STRESS_TOLERANCE_NEED = 3   # 80–99% 段：回合开始耐受判定的需求成功数

# 降低应力的 4 种手段（02B 第五节）：id → (降幅, 说明)。
STRESS_REDUCTIONS = (
    ("short_rest", -2, "短歇"),
    ("long_rest", -4, "长歇"),
    ("drive_goal", -2, "完成一次角色驱动相关的目标"),
    ("ability_item", None, "特定能力／道具（见具体描述，由导引者裁定）"),
)
LONG_REST_SAFE_HAVEN = -6   # 长歇在安全据点 −6（02B 第五节）


def stress_cap(unit: dict | None) -> int:
    """应力上限 = 体魄 + 心智 + 5（02B 第五节；典型 13–21）。"""
    attrs = (unit or {}).get("attributes") or {}
    return (int(attrs.get("VIG", 4) or 4) + int(attrs.get("MND", 4) or 4)
            + STRESS_CAP_FLAT)


def stress_penalty(stress: int, cap: int) -> dict:
    """应力惩罚段（02B 第五节，4 段，按占上限的比例分档）。

    返回 {ratio, band, pool_dice, tolerance_need, collapse}：
      0–49%   无；
      50–79%  所有判定骰池 −1 枚；
      80–99%  骰池 −2 枚，且每次开始回合需通过耐受判定（需求成功
              3，失败失去 1 AP——由调用方按骰池结算）；
      100%    崩溃（事件走 stress_overload_event）。
    """
    stress = max(0, int(stress or 0))
    cap = int(cap or 0)
    if cap <= 0:
        raise ValueError("应力上限必须为正")
    ratio = min(100, round(stress * 100 / cap))
    if stress >= cap:
        return {"ratio": 100, "band": "100%", "pool_dice": 0,
                "tolerance_need": None, "collapse": True}
    if ratio >= 80:
        return {"ratio": ratio, "band": "80–99%", "pool_dice": -2,
                "tolerance_need": STRESS_TOLERANCE_NEED, "collapse": False}
    if ratio >= 50:
        return {"ratio": ratio, "band": "50–79%", "pool_dice": -1,
                "tolerance_need": None, "collapse": False}
    return {"ratio": ratio, "band": "0–49%", "pool_dice": 0,
            "tolerance_need": None, "collapse": False}


def stress_overload_event(session: RuleSession, unit: dict | None) -> dict:
    """崩溃 Overload Event（02B 第五节，应力达到上限时立即触发的 4 步）。

      1. 失去本回合所有 AP（战斗中直接清 unit["ap"]；AP 经济归回合
         层，非战斗时由调用方处理），并演出崩溃的具体表现（导引者
         与玩家共同决定，玩家优先——归导引者模块）；
      2. 获得 3 层【疲惫】与 1 点负担；
      3. 所有进行中的超载效果立即中断（此处只记 interrupted 标记；
         能力层接线后由调用方结束【过载中】等效果——TODO(M2d)）；
      4. 应力回落至上限的 50%。

    【疲惫】的「3 层」是 02B 点名的数量；疲惫层数的机械效果源文档
    未写明，本函数只记录层数——TODO(M2d) 复核。
    """
    if unit is None:
        raise ValueError("崩溃事件必须落在某个单位上")
    steps: dict = {"ap_lost": False, "fatigue_layers": 0, "burden": 0,
                   "interrupted": False, "stress_after": 0}
    if "ap" in unit:
        unit["ap"] = 0
    steps["ap_lost"] = True
    add_condition(session, unit, "疲惫")
    unit.setdefault("condition_layers", {})["疲惫"] = 3
    steps["fatigue_layers"] = 3
    if change_resource(session, unit, "strain", 1)["changed"]:
        steps["burden"] = 1
    steps["interrupted"] = True
    cap = stress_cap(unit)
    unit["stress"] = cap // 2      # 回落至上限的 50%（向下取整）
    steps["stress_after"] = int(unit["stress"])
    session.add_log("system", f"{unit.get('name', '')} 应力达到上限，崩溃了。",
                    speaker="应力")
    session.touch()
    return steps


def stress_gain(session: RuleSession, unit: dict | None, amount: int) -> dict:
    """给单位累积应力（02B 第五节 5 种方式由调用方按键名触发）。

    应力夹取 0–上限；**达到上限立即触发崩溃**（stress_overload_event，
    结果挂在返回值的 overload 字段）。返回 {stress, cap, changed,
    overload}。
    """
    if unit is None:
        raise ValueError("应力必须加在某个单位上")
    amount = int(amount or 0)
    cap = stress_cap(unit)
    before = max(0, int(unit.get("stress") or 0))
    after = min(max(0, before + amount), cap)
    unit["stress"] = after
    out: dict = {"stress": after, "cap": cap, "changed": after - before,
                 "overload": None}
    if amount > 0 and after >= cap and before < cap:
        out["overload"] = stress_overload_event(session, unit)
    session.touch()
    return out


def stress_reduce(session: RuleSession, unit: dict | None, method: str, *,
                  safe_haven: bool = False,
                  amount: int | None = None) -> dict:
    """降低应力（02B 第五节 4 种手段），返回 {method, changed, stress}。

    短歇 −2 / 长歇 −4（在安全据点 −6，传 safe_haven=True）/ 完成一
    次角色驱动相关的目标 −2；「特定能力／道具」的降幅源文档未写明
    （「见具体描述」），必须由调用方显式传入 amount，否则 raise——
    不臆造数值。应力不低于 0。
    """
    if unit is None:
        raise ValueError("应力必须落在某个单位上")
    key = str(method or "")
    if key == "ability_item":
        if amount is None:
            raise ValueError(
                "「特定能力／道具」的降幅源文档未写明，须由调用方传入 amount")
        delta = -abs(int(amount))
    else:
        for method_id, value, _label in STRESS_REDUCTIONS:
            if method_id == key:
                delta = int(value)
                break
        else:
            raise ValueError(
                f"未知降低应力手段：{method}"
                f"（可用：{'/'.join(item[0] for item in STRESS_REDUCTIONS)}）")
    if key == "long_rest" and safe_haven:
        delta = LONG_REST_SAFE_HAVEN
    before = max(0, int(unit.get("stress") or 0))
    after = max(0, before + delta)
    unit["stress"] = after
    session.touch()
    return {"method": key, "changed": after - before, "stress": after}


__all__ = [
    # 一、术语与白名单
    "ATTRIBUTES", "ATTRIBUTE_IDS", "ATTRIBUTE_MIN", "ATTRIBUTE_MAX",
    "ATTRIBUTE_MODIFIER_RANGES", "GUARD_BASE", "RESOURCE_POOLS",
    "ACTION_KINDS", "COMBAT_ACTIONS", "EQUIP_SLOTS", "OUTCOMES",
    "PROFICIENCY_LEVELS", "STRAIN_MAX", "RESOLVE_MAX",
    "DEBUFF_CONDITIONS", "KNOWN_CONDITIONS", "register_condition",
    "LAYERED_CONDITIONS", "CONDITION_MAX_LAYERS",
    "DOWNED_STRUGGLE_DF", "DOWNED_STABILIZE_SUCCESSES",
    "DOWNED_DEATH_FAILURES", "MEDICAL_STABILIZE_DF",
    "TRAUMA_VITALITY_PENALTY_AT", "TRAUMA_VITALITY_PENALTY",
    "TRAUMA_FEAR_AT",
    "attribute_modifier", "proficiency_bonus",
    # 二、掷骰器
    "DICE_MAX_COUNT", "DICE_MAX_SIDES", "DEFAULT_CHECK_NOTATION", "roll",
    "EDGE_DIE_SIDES", "EDGE_MAX_LAYERS", "EDGE_BEYOND_FLAT",
    "cancel_edge_layers",
    # 三、会话状态契约
    "new_unit", "RuleSession",
    # 四、结算的单一进出口
    "deal_damage", "heal_unit", "downed_struggle", "stabilize_downed",
    "add_trauma", "change_resource", "add_condition",
    "failure_cost", "apply_failure", "tick_pressure",
    "CATASTROPHE_TABLE", "PRESSURE_MAX", "TENSION_BANDS", "tension_band",
    "RISK_POOL_ZONES", "risk_roll",
    # 五、场景行动单入口
    "perform_action", "resolve_check", "judge_check",
    # 六、战斗状态机
    "CRIT_MARGIN", "SOLID_MARGIN", "GRAZE_MARGIN", "FUMBLE_TABLE",
    "attack_grade", "resolve_attack",
    "STANCE_GUARD", "COVER_GUARD", "CONDITION_GUARD", "BREAK_GUARD_PENALTY",
    "POISE_SIZE_BONUS", "RALLY_DF", "BOSS_SHIELDS",
    "max_poise", "apply_poise_damage", "poise_regen_amount", "rally_broken",
    "COMBAT_BUFF_FIELDS", "start_combat", "build_encounter",
    "encounter_budget", "strength_budget", "make_enemy",
    "ENEMY_TIERS", "ENEMY_SCALING", "ENEMY_AFFIXES", "AFFIX_LIMIT",
    "ENCOUNTER_STRENGTH", "BOSS_BUDGET_SHARE", "MINION_CAP",
    "initiative_adjustment", "roll_initiative",
    "combat_abandon", "combat_view",
    # 六·附 战术投影（ATLAS I5）
    "TACTICAL_CELL_METERS", "attach_tactical_map", "detach_tactical_map",
    "tactical_map", "tactical_range_band", "tactical_high_ground",
    "tactical_move_zones",
    # 七、派生值与明细
    "vitality_cap", "move_speed", "carry_capacity", "focus_cap",
    "tempo_cap",
    "equipped_items", "item_guard_bonus", "guard_breakdown", "compute_guard",
    "recompute_unit",
    # 八、成长
    "LEVEL_XP_THRESHOLDS", "level_for_xp", "growth_points_for", "award_xp",
    "GROWTH_CATEGORIES", "GROWTH_ATTRIBUTE_CAP", "GROWTH_CONSECUTIVE_LIMIT",
    "assign_attribute", "apply_growth_choice",
    # 九、构建引擎（M2c，02B 第一至五节）
    "BUILD_SUCCESS_THRESHOLD", "BUILD_DEFAULT_SIDES", "BUILD_DIE_RATES",
    "BUILD_SPECIALIZATION_DICE", "BUILD_TEMP_DICE_TYPICAL",
    "build_proficiency_dice", "build_pool", "build_die_rate",
    "BUILD_REQUIRED_SUCCESSES", "build_difficulty",
    "BUILD_OUTCOME_MOMENTUM", "build_outcome",
    "BUILD_DIE_RANKS", "BUILD_STARTING_RANK", "BUILD_EXPECTED_SUCCESS_TABLE",
    "build_rank_die", "build_expected_successes", "build_roll",
    "MOMENTUM_SHARED_START", "MOMENTUM_SHARED_CAP", "MOMENTUM_PERSON_CAP",
    "MOMENTUM_BOOST_MAX", "MOMENTUM_GAIN_SOURCES", "MOMENTUM_SPENDS",
    "new_momentum_pool", "momentum_add", "momentum_spend", "momentum_clear",
    "STRESS_CAP_FLAT", "STRESS_GAINS", "STRESS_TOLERANCE_NEED",
    "STRESS_REDUCTIONS", "LONG_REST_SAFE_HAVEN",
    "stress_cap", "stress_penalty", "stress_overload_event",
    "stress_gain", "stress_reduce",
]

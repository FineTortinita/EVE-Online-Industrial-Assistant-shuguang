"""「制造计算」窗口的桥（重构阶段 3，开明网式）。

选成品 + 数量 → 参数条（制造 / 反应机库、ME/TE、价格中心、税率、四个类别造买开关、人物）→
四个页签：原料汇总 / 逐级分解 / 成本与利润 / 采购清单。计算在 `services.industry_calc_service`，
这里只做表单状态、格式化与「加入规划」。

非模态独立窗（与可制造物品浏览器同形），单实例复用。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from core.constants import TRADE_HUBS
from core.logger import log
from domain.industry_calc import ACTIVITY_MANUFACTURING, ACTIVITY_REACTION
from services import industry_calc_service as ics
from services.terminology import term
from ui_qml.bridge.summary_dialog import cell, fmt_isk
from ui_qml.dialog_host import DialogBridge, QmlDialog

__all__ = ["IndustryCalcBridge", "IndustryCalcQmlDialog"]

_QML_FILE = "dialogs/IndustryCalcDialog.qml"
_DEBOUNCE_MS = 250

_TOGGLE_ROWS = [
    (ics.TOGGLE_REACTION, "反应材料", "自己做", "买入"),
    (ics.TOGGLE_MOON, "卫星原料 / 气云", "自己采", "买入"),
    (ics.TOGGLE_PI, "行星产物", "自己种", "买入"),
    (ics.TOGGLE_FUEL, "燃料块", "自己造", "买入"),
]


def _isk(value: float) -> str:
    return f"{fmt_isk(value)} ISK"


def raw_group_rows(raws: list[ics.RawLine]) -> list[dict]:
    """原料按类别分组 → [{title, subtitle, rows:[{cells}], typeIds:[...], copyText}]。纯函数。"""
    order = list(ics.CATEGORY_GROUPS) + ["other"]
    total = sum(r.cost for r in raws) or 1.0
    out: list[dict] = []
    for key in order:
        items = [r for r in raws if r.category == key]
        if not items:
            continue
        cost = sum(r.cost for r in items)
        free = all(r.free for r in items)
        subtitle = "自采 · 不计价" if free else f"{_isk(cost)} · {cost / total * 100:.1f}%"
        out.append(
            {
                "title": f"{ics.category_label(key)} {len(items)} 种",
                "subtitle": subtitle,
                "rows": [
                    {
                        "typeId": r.type_id,
                        "buildable": r.buildable,
                        "cells": [
                            cell(r.name),
                            cell(f"{r.qty:,}"),
                            cell("不计价" if r.free else f"{r.unit_price:,.2f}", "TEXT_SECONDARY" if r.free else ""),
                            cell(
                                f"{r.qty * r.unit_price:,.0f}" if r.free else f"{r.cost:,.0f}",
                                "TEXT_SECONDARY" if r.free else "",
                            ),
                        ],
                    }
                    for r in items
                ],
                "copyText": "\n".join(f"{r.name}\t{r.qty}" for r in items),
            }
        )
    return out


def wave_columns(result: ics.CalcResult) -> list[dict]:
    """逐级分解：每条轨道（制造 / 反应）每层一列 → [{track, title, rows:[{text, color}], footer}]。纯函数。

    颜色规则（与开明网同义）：青 = 制造件；紫 = 反应；明细里「−N」= 整流程多出的边角料。
    颜色经 `token_color` 过对比度校正。
    """
    from ui_qml.theme.registry import token_color

    cols: list[dict] = []
    for act, title in ((ACTIVITY_MANUFACTURING, "制造"), (ACTIVITY_REACTION, "反应")):
        for i, layer in enumerate(result.waves.get(act, [])):
            color = token_color("ACCENT_CYAN" if act == ACTIVITY_MANUFACTURING else "ACCENT_PURPLE")
            rows = []
            for j in layer:
                left = f"  −{j.leftover:,}" if j.leftover else ""
                rows.append(
                    {
                        "typeId": j.type_id,
                        "text": f"{j.name}  ×{j.demand:,}",
                        "detail": f"{j.parallels}×{j.runs} 流程{left}",
                        "color": color,
                    }
                )
            fee = sum(j.fee for j in layer)
            longest = max((j.seconds for j in layer), default=0.0)
            cols.append(
                {
                    "track": act,
                    "title": f"第 {i} 次{title}",
                    "rows": rows,
                    "footer": f"作业费 {fmt_isk(fee)} · 最长 {ics.fmt_duration(longest)}",
                }
            )
    return cols


def job_rows(result: ics.CalcResult) -> list[dict]:
    """各生产步骤费用估算表（按作业费降序，与开明网同）。"""
    rows = []
    for j in sorted(result.jobs, key=lambda j: -j.fee):
        rows.append(
            {
                "cells": [
                    cell(j.name, "ACCENT_CYAN" if j.activity == ACTIVITY_MANUFACTURING else "ACCENT_PURPLE"),
                    cell(f"{j.parallels}×{j.runs}"),
                    cell(fmt_isk(j.eiv)),
                    cell(f"{j.cost_index * 100:.2f}%"),
                    cell(fmt_isk(j.fee)),
                    cell(ics.fmt_duration(j.seconds)),
                    cell(f"{j.leftover:,}" if j.leftover else "—", "ACCENT_GREEN" if j.leftover else ""),
                ]
            }
        )
    return rows


def _field(label: str, value: str, token: str = "", *, strong: bool = False) -> dict:
    from ui_qml.theme import registry as theme

    return {
        "label": label,
        "value": value,
        "color": str(getattr(theme, token, "") or "") if token else "",
        "strong": strong,
    }


def cost_fields(result: ics.CalcResult) -> list[dict]:
    p = result.fee_parts
    return [
        _field("原料", _isk(result.material_cost)),
        _field("星系成本", _isk(p.get("system_cost", 0))),
        _field("设施税", _isk(p.get("facility_tax", 0))),
        _field("SCC 附加费 4%", _isk(p.get("scc", 0))),
        _field(
            "作业费合计",
            f"{_isk(result.fee_total)}（制造 {fmt_isk(result.fee_manufacturing)} · 反应 {fmt_isk(result.fee_reaction)}）",
        ),
        _field("总成本", _isk(result.total_cost), strong=True),
        _field("边角料抵扣", _isk(result.leftover_credit), "ACCENT_GREEN"),
        _field("抵扣后成本", _isk(result.net_cost), strong=True),
    ]


def profit_fields(result: ics.CalcResult) -> list[dict]:
    sell_profit = result.revenue_sell - result.total_cost
    buy_profit = result.revenue_buy - result.total_cost
    return [
        _field("挂单卖出（扣税 + 中介）", _isk(result.revenue_sell)),
        _field("按买价直接卖（扣税）", _isk(result.revenue_buy)),
        _field("挂单现金结余", _isk(sell_profit), "ACCENT_GREEN" if sell_profit >= 0 else "ACCENT_RED", strong=True),
        _field("买价现金结余", _isk(buy_profit), "ACCENT_GREEN" if buy_profit >= 0 else "ACCENT_RED"),
        _field("含边角料抵扣（挂单）", _isk(sell_profit + result.leftover_credit)),
        _field(
            "生产步骤",
            f"{len(result.jobs)} 个 · 最长 {ics.fmt_duration(result.longest_seconds)} · 单线合计 {ics.fmt_duration(result.total_line_seconds)}",
        ),
    ]


class IndustryCalcBridge(DialogBridge):
    """制造计算窗口的 QML 后端。"""

    stateChanged = Signal()
    resultChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.set_title("制造计算")
        from services import inventory_manager
        from services.char_config_resolver import get_character_list

        self._hangars = [{"label": "NPC 空间站（无加成）", "value": 0}] + [
            {"label": h["name"], "value": int(h["id"])} for h in inventory_manager.get_hangars()
        ]
        self._chars = ["（技能全 5）", "（技能全 0）"] + list(get_character_list() or [])
        # 显示中文星系名、值保持英文 hub 键（取价用）
        self._hubs = [{"label": term.system_name(h) or h, "value": h} for h in TRADE_HUBS]
        self._targets: list[dict] = []  # [{type_id, name, qty}]
        self._query = ""
        self._suggestions: list[dict] = []
        self._me, self._te = 10, 20
        self._mfg_index = 0
        self._react_index = 0
        self._char_index = 0
        self._hub_index = 0
        self._mfg_ci = 0.0  # 手填指数（%），0 = 按星系查
        self._react_ci = 0.0
        self._sales_tax = 3.6
        self._broker = 1.5
        self._toggles = dict(ics.DEFAULT_TOGGLES)
        self._overrides: dict[int, bool] = {}
        self._result: ics.CalcResult | None = None
        self._summary = "选一个成品开始计算"
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._search)

    # ── 成品选择 ──────────────────────────────────────────────

    @Property(list, notify=stateChanged)
    def suggestions(self) -> list[dict]:
        return list(self._suggestions)

    @Property(list, notify=stateChanged)
    def targets(self) -> list[dict]:
        return [{"typeId": t["type_id"], "name": t["name"], "qty": t["qty"]} for t in self._targets]

    @Slot(str)
    def setQuery(self, text: str) -> None:
        self._query = str(text).strip()
        self._debounce.start()

    @Slot()
    def _search(self) -> None:
        self._suggestions = find_buildable(self._query) if self._query else []
        self.stateChanged.emit()

    @Slot(int)
    def pickSuggestion(self, index: int) -> None:
        if not 0 <= index < len(self._suggestions):
            return
        s = self._suggestions[index]
        if not any(t["type_id"] == s["type_id"] for t in self._targets):
            self._targets.append({"type_id": s["type_id"], "name": s["name"], "qty": 1})
        self._suggestions = []
        self.stateChanged.emit()
        self.recalc()

    @Slot(int, int)
    def setTargetQty(self, index: int, qty: int) -> None:
        if 0 <= index < len(self._targets) and qty > 0 and self._targets[index]["qty"] != qty:
            self._targets[index]["qty"] = int(qty)
            self.stateChanged.emit()
            self.recalc()

    @Slot(int)
    def removeTarget(self, index: int) -> None:
        if 0 <= index < len(self._targets):
            self._targets.pop(index)
            self.stateChanged.emit()
            self.recalc()

    # ── 参数条 ────────────────────────────────────────────────

    hangarOptions = Property(list, lambda self: [h["label"] for h in self._hangars], constant=True)
    charOptions = Property(list, lambda self: list(self._chars), constant=True)
    hubOptions = Property(list, lambda self: [h["label"] for h in self._hubs], constant=True)
    toggleRows = Property(
        list,
        lambda self: [
            {"key": k, "label": label, "on": on, "off": off, "checked": bool(self._toggles.get(k))}
            for k, label, on, off in _TOGGLE_ROWS
        ],
        notify=stateChanged,
    )
    me = Property(int, lambda self: self._me, notify=stateChanged)
    te = Property(int, lambda self: self._te, notify=stateChanged)
    mfgIndex = Property(int, lambda self: self._mfg_index, notify=stateChanged)
    reactIndex = Property(int, lambda self: self._react_index, notify=stateChanged)
    charIndex = Property(int, lambda self: self._char_index, notify=stateChanged)
    hubIndex = Property(int, lambda self: self._hub_index, notify=stateChanged)
    mfgCostIndex = Property(float, lambda self: self._mfg_ci, notify=stateChanged)
    reactCostIndex = Property(float, lambda self: self._react_ci, notify=stateChanged)
    salesTax = Property(float, lambda self: self._sales_tax, notify=stateChanged)
    brokerFee = Property(float, lambda self: self._broker, notify=stateChanged)

    def _set(self, attr: str, value: Any) -> None:
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            self.stateChanged.emit()
            self.recalc()

    @Slot(int)
    def setMe(self, v: int) -> None:
        self._set("_me", max(0, min(10, int(v))))

    @Slot(int)
    def setTe(self, v: int) -> None:
        self._set("_te", max(0, min(20, int(v))))

    @Slot(int)
    def setMfgIndex(self, v: int) -> None:
        self._set("_mfg_index", int(v))

    @Slot(int)
    def setReactIndex(self, v: int) -> None:
        self._set("_react_index", int(v))

    @Slot(int)
    def setCharIndex(self, v: int) -> None:
        self._set("_char_index", int(v))

    @Slot(int)
    def setHubIndex(self, v: int) -> None:
        self._set("_hub_index", int(v))

    @Slot(float)
    def setMfgCostIndex(self, v: float) -> None:
        self._set("_mfg_ci", max(0.0, float(v)))

    @Slot(float)
    def setReactCostIndex(self, v: float) -> None:
        self._set("_react_ci", max(0.0, float(v)))

    @Slot(float)
    def setSalesTax(self, v: float) -> None:
        self._set("_sales_tax", max(0.0, float(v)))

    @Slot(float)
    def setBrokerFee(self, v: float) -> None:
        self._set("_broker", max(0.0, float(v)))

    @Slot(str, bool)
    def setToggle(self, key: str, value: bool) -> None:
        if self._toggles.get(key) != bool(value):
            self._toggles[key] = bool(value)
            self.stateChanged.emit()
            self.recalc()

    @Slot(int)
    def toggleBuild(self, type_id: int) -> None:
        """逐项「直接买 / 自己做」切换（原料表里点有配方的行、或逐级分解里点中间件）。"""
        tid = int(type_id)
        made = tid in {j.type_id for j in (self._result.jobs if self._result else [])}
        self._overrides[tid] = not made
        self.recalc()

    @Slot()
    def resetDefaults(self) -> None:
        self._toggles = dict(ics.DEFAULT_TOGGLES)
        self._overrides.clear()
        self._me, self._te, self._mfg_ci, self._react_ci = 10, 20, 0.0, 0.0
        self._sales_tax, self._broker = 3.6, 1.5
        self.stateChanged.emit()
        self.recalc()

    # ── 计算 ──────────────────────────────────────────────────

    def build_request(self) -> ics.CalcRequest:
        mfg = ics.Facility.from_hangar(self._hangars[self._mfg_index]["value"] or None, ACTIVITY_MANUFACTURING)
        react = ics.Facility.from_hangar(self._hangars[self._react_index]["value"] or None, ACTIVITY_REACTION)
        if self._mfg_ci > 0:
            mfg.cost_index = self._mfg_ci / 100
        if self._react_ci > 0:
            react.cost_index = self._react_ci / 100
        skills, implants = self._char_skills()
        return ics.CalcRequest(
            targets=[(t["type_id"], t["qty"]) for t in self._targets],
            me_level=self._me,
            te_level=self._te,
            manufacturing=mfg,
            reaction=react,
            toggles=dict(self._toggles),
            overrides=dict(self._overrides),
            price_hub=self._hubs[self._hub_index]["value"],
            sales_tax_pct=self._sales_tax,
            broker_pct=self._broker,
            skills=skills,
            implants=implants,
        )

    def _char_skills(self) -> tuple[dict, list | None]:
        """人物下拉：前两项是伪人物「技能全 5 / 全 0」（T2 商人助手同款，做假设核算）。"""
        from core.skill_keys import ADVANCED_INDUSTRY, INDUSTRY, REACTIONS

        if self._char_index == 0:
            return {INDUSTRY: 5, ADVANCED_INDUSTRY: 5, REACTIONS: 5}, None
        if self._char_index == 1:
            return {}, None
        from services.char_config_resolver import resolve_char_config

        cfg = resolve_char_config(char_name=self._chars[self._char_index]) or {}
        return dict(cfg.get("skills") or {}), cfg.get("implants")

    @Slot()
    def recalc(self) -> None:
        if not self._targets:
            self._result = None
            self._summary = "选一个成品开始计算"
            self.resultChanged.emit()
            return
        try:
            self._result = ics.run(self.build_request())
        except Exception:
            # 吞的是计算链路上的任何异常（库缺表 / 价格表未初始化）：窗口保持可用，状态行报错
            log.exception("制造计算失败")
            self._result = None
            self._summary = "计算失败，详见日志"
            self.resultChanged.emit()
            return
        r = self._result
        self._summary = (
            f"总成本 {_isk(r.total_cost)} · 原料 {_isk(r.material_cost)} · 作业费 {_isk(r.fee_total)} · "
            f"挂单结余 {_isk(r.revenue_sell - r.total_cost)} · 步骤 {len(r.jobs)} 个"
            + (f"　⚠ {'；'.join(r.warnings)}" if r.warnings else "")
        )
        self.resultChanged.emit()

    summaryText = Property(str, lambda self: self._summary, notify=resultChanged)
    rawGroups = Property(
        list, lambda self: raw_group_rows(self._result.raws) if self._result else [], notify=resultChanged
    )
    waveColumns = Property(list, lambda self: wave_columns(self._result) if self._result else [], notify=resultChanged)
    jobRows = Property(list, lambda self: job_rows(self._result) if self._result else [], notify=resultChanged)
    costFields = Property(list, lambda self: cost_fields(self._result) if self._result else [], notify=resultChanged)
    profitFields = Property(
        list, lambda self: profit_fields(self._result) if self._result else [], notify=resultChanged
    )
    multibuyText = Property(
        str, lambda self: ics.multibuy_text(self._result.raws) if self._result else "", notify=resultChanged
    )

    @Slot(str)
    def copyText(self, text: str) -> None:
        cb = QApplication.clipboard()
        if cb is not None and text:
            cb.setText(text)
            self.set_error(f"已复制 {len(text.splitlines())} 行（游戏市场「批量购买」可直接粘贴）")

    # ── 加入规划 ──────────────────────────────────────────────

    @Slot()
    def addToPlans(self) -> None:
        """把成品按当前流程建成母项计划（制造机库 = 材料机库），再按计划口径拆出子项。

        子项的造买以计划侧规则为准（制造件自己造、反应产物买入），与本窗口的类别开关无关 ——
        计划表目前不建反应子项（见 `docs/dev/industry-refactor-plan.md` 阶段 2 说明）。
        """
        if not self._targets or self._result is None:
            self.set_error("先选择成品")
            return
        from core.container import get_container
        from services import user_settings
        from services.industry_dialog_queries import get_max_group_number
        from services.inventory_manager import get_hangar_name, get_hangar_system_id
        from services.plan_rebuild import rebuild_children
        from services.plan_service import insert_plans_batch

        mfg_hangar = self._hangars[self._mfg_index]["value"] or None
        react_hangar = self._hangars[self._react_index]["value"] or None
        hub = self._hubs[self._hub_index]["value"]
        rows = []
        for t in self._result.targets:
            # 反应成品记成反应计划、放反应设施（PR #2 review：曾一律存成制造计划，重建找不到配方）
            activity = t.get("activity") or ACTIVITY_MANUFACTURING
            hangar = react_hangar if activity == ACTIVITY_REACTION else mfg_hangar
            rows.append(
                {
                    "type_id": t["type_id"],
                    "product_name": t["name"],
                    "activity": activity,
                    "data": {
                        "runs": t["runs"],
                        "parallels": 1,
                        "me": self._me if activity == ACTIVITY_MANUFACTURING else 0,
                        "te": self._te if activity == ACTIVITY_MANUFACTURING else 0,
                        "char": "",
                    },
                    "mat_hub": hub,
                    "sell_hub": hub,
                    "facility": get_hangar_name(hangar) if hangar else "",
                    "solar_system_id": get_hangar_system_id(hangar) if hangar else None,
                    "mat_hangar_id": hangar,
                    "deposit_hangar_id": user_settings.get_default_hangar_id("default_deposit_hangar_id"),
                }
            )
        ids = insert_plans_batch(rows)
        db = get_container().db
        gnum = get_max_group_number(db) + 1
        repo = get_container().plan_repo
        for pid in ids:
            repo.update(pid, group_number=gnum, sub_level=0)
        res = rebuild_children(create=True, prune=False, mother_ids=set(ids))
        self.set_error(f"已加入规划：{len(ids)} 个母项（组 {gnum}），子项 {res['created']} 条")
        self.plansAdded.emit()

    plansAdded = Signal()

    @Slot()
    def accept(self) -> None:
        """对话框的主按钮 =「加入规划」，加完**不关窗**（继续算下一个成品）。"""
        self.addToPlans()


def find_buildable(text: str, limit: int = 30) -> list[dict]:
    """名称模糊匹配**有制造 / 反应配方**的物品（排除蓝图本身）。"""
    from core.container import get_container

    like = f"%{text}%"
    with get_container().db.connect("ref", "bp") as conn:
        rows = conn.execute(
            "SELECT DISTINCT i.type_id, COALESCE(NULLIF(i.zh_name, ''), i.en_name) FROM item i "
            "JOIN blueprint_products p ON p.product_type_id = i.type_id AND p.activity IN ('manufacturing', 'reaction') "
            "WHERE i.zh_name LIKE ? OR i.en_name LIKE ? "
            "ORDER BY CASE WHEN i.zh_name LIKE ? OR i.en_name LIKE ? THEN 0 ELSE 1 END, LENGTH(i.zh_name), i.type_id "
            "LIMIT ?",
            (like, like, f"{text}%", f"{text}%", limit),
        ).fetchall()
    return [{"type_id": int(r[0]), "name": str(r[1] or r[0])} for r in rows]


class IndustryCalcQmlDialog(QmlDialog):
    """「制造计算」独立窗（非模态，单实例由调用方 `find_modeless` 复用）。"""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(_QML_FILE, IndustryCalcBridge(), parent=parent, size=(1280, 820), modeless=True)

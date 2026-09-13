"""Power BI domain: validators and skills.

Validators here are *static* checks (no execution), so they need no sandbox.
An executable DAX validator (against a test semantic model) would be a
separate, sandboxed validator added in Phase 4.
"""

from __future__ import annotations

import re
from typing import Any

from knowledge_platform.core.plugins.base import DomainPlugin, Skill, ValidationResult, Validator

# A representative (not exhaustive) list of DAX functions used to flag unknown identifiers.
DAX_FUNCTIONS = {
    "ABS",
    "ADDCOLUMNS",
    "ALL",
    "ALLEXCEPT",
    "ALLNOBLANKROW",
    "ALLSELECTED",
    "AND",
    "AVERAGE",
    "AVERAGEX",
    "BLANK",
    "CALCULATE",
    "CALCULATETABLE",
    "CALENDAR",
    "CALENDARAUTO",
    "CEILING",
    "CLOSINGBALANCEMONTH",
    "COALESCE",
    "COMBINEVALUES",
    "CONCATENATE",
    "CONCATENATEX",
    "CONTAINS",
    "CONTAINSSTRING",
    "COUNT",
    "COUNTA",
    "COUNTAX",
    "COUNTBLANK",
    "COUNTROWS",
    "COUNTX",
    "CROSSFILTER",
    "CROSSJOIN",
    "CURRENCY",
    "DATATABLE",
    "DATE",
    "DATEADD",
    "DATEDIFF",
    "DATESBETWEEN",
    "DATESINPERIOD",
    "DATESMTD",
    "DATESQTD",
    "DATESYTD",
    "DATEVALUE",
    "DAY",
    "DISTINCT",
    "DISTINCTCOUNT",
    "DISTINCTCOUNTNOBLANK",
    "DIVIDE",
    "EARLIER",
    "EARLIEST",
    "EDATE",
    "ENDOFMONTH",
    "ENDOFYEAR",
    "EOMONTH",
    "ERROR",
    "EXCEPT",
    "FALSE",
    "FILTER",
    "FILTERS",
    "FIND",
    "FIRSTDATE",
    "FIRSTNONBLANK",
    "FIXED",
    "FLOOR",
    "FORMAT",
    "GENERATE",
    "GENERATEALL",
    "GENERATESERIES",
    "GROUPBY",
    "HASONEFILTER",
    "HASONEVALUE",
    "IF",
    "IFERROR",
    "IGNORE",
    "INDEX",
    "INT",
    "INTERSECT",
    "ISBLANK",
    "ISCROSSFILTERED",
    "ISEMPTY",
    "ISERROR",
    "ISFILTERED",
    "ISINSCOPE",
    "ISNUMBER",
    "ISONORAFTER",
    "ISSELECTEDMEASURE",
    "ISTEXT",
    "KEEPFILTERS",
    "LASTDATE",
    "LASTNONBLANK",
    "LEFT",
    "LEN",
    "LOOKUPVALUE",
    "LOWER",
    "MAX",
    "MAXA",
    "MAXX",
    "MEDIAN",
    "MEDIANX",
    "MID",
    "MIN",
    "MINA",
    "MINX",
    "MOD",
    "MONTH",
    "MROUND",
    "NATURALINNERJOIN",
    "NATURALLEFTOUTERJOIN",
    "NEXTDAY",
    "NEXTMONTH",
    "NOT",
    "NOW",
    "OFFSET",
    "OPENINGBALANCEMONTH",
    "OR",
    "PARALLELPERIOD",
    "PATH",
    "PATHCONTAINS",
    "PATHITEM",
    "PERCENTILE.INC",
    "PERCENTILEX.INC",
    "POWER",
    "PREVIOUSDAY",
    "PREVIOUSMONTH",
    "PREVIOUSQUARTER",
    "PREVIOUSYEAR",
    "PRODUCT",
    "PRODUCTX",
    "QUARTER",
    "RANK",
    "RANKX",
    "RELATED",
    "RELATEDTABLE",
    "REMOVEFILTERS",
    "REPLACE",
    "REPT",
    "RIGHT",
    "ROLLUP",
    "ROLLUPGROUP",
    "ROUND",
    "ROUNDDOWN",
    "ROUNDUP",
    "ROW",
    "SAMEPERIODLASTYEAR",
    "SEARCH",
    "SELECTCOLUMNS",
    "SELECTEDMEASURE",
    "SELECTEDMEASURENAME",
    "SELECTEDVALUE",
    "SQRT",
    "STARTOFMONTH",
    "STARTOFYEAR",
    "SUBSTITUTE",
    "SUM",
    "SUMMARIZE",
    "SUMMARIZECOLUMNS",
    "SUMX",
    "SWITCH",
    "TODAY",
    "TOPN",
    "TOTALMTD",
    "TOTALQTD",
    "TOTALYTD",
    "TREATAS",
    "TRIM",
    "TRUE",
    "TRUNC",
    "UNICHAR",
    "UNION",
    "UPPER",
    "USERELATIONSHIP",
    "USERNAME",
    "USERPRINCIPALNAME",
    "VALUE",
    "VALUES",
    "VAR",
    "RETURN",
    "WEEKDAY",
    "WEEKNUM",
    "WINDOW",
    "YEAR",
    "YEARFRAC",
    "DEFINE",
    "EVALUATE",
    "MEASURE",
    "ORDER",
    "BY",
    "IN",
}

_DAX_HINT = re.compile(r"\b(CALCULATE|SUMX|FILTER|VAR|RETURN|EVALUATE|MEASURE|ALL|VALUES|RELATED|DIVIDE)\b")
_IDENT = re.compile(r"\b([A-Z][A-Z0-9.]{2,})\s*\(")


def _balanced(code: str) -> tuple[bool, str]:
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    in_str = False
    for ch in code:
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if not stack or stack.pop() != ch:
                return False, f"unexpected '{ch}'"
    if in_str:
        return False, "unterminated string literal"
    if stack:
        return False, f"missing '{stack[-1]}'"
    return True, "balanced"


class DaxSyntaxValidator(Validator):
    """Static DAX sanity check: balanced brackets/strings and known function names."""

    name = "dax-syntax"
    version = "1.0"

    def applies_to(self, item: dict[str, Any]) -> bool:
        code = item.get("code") or ""
        return bool(code) and bool(_DAX_HINT.search(code)) and not code.lstrip().lower().startswith("let")

    def validate(self, item: dict[str, Any]) -> ValidationResult:
        code = item["code"]
        ok, msg = _balanced(code)
        unknown = sorted({m for m in _IDENT.findall(code) if m not in DAX_FUNCTIONS})
        passed = ok and not unknown
        details = {"balanced": ok, "unknown_functions": unknown}
        message = msg if ok else f"syntax: {msg}"
        if unknown:
            message += f"; unknown function(s): {', '.join(unknown[:5])}"
        return ValidationResult(
            validator=self.name, version=self.version, passed=passed, details=details, message=message
        )


class MSyntaxValidator(Validator):
    """Static Power Query M check: let/in structure and balanced brackets."""

    name = "m-syntax"
    version = "1.0"

    def applies_to(self, item: dict[str, Any]) -> bool:
        code = (item.get("code") or "").lstrip().lower()
        return code.startswith("let") or code.startswith("(") and "=>" in code

    def validate(self, item: dict[str, Any]) -> ValidationResult:
        code = item["code"]
        ok, msg = _balanced(code)
        has_in = bool(re.search(r"\bin\b", code)) if code.lstrip().lower().startswith("let") else True
        passed = ok and has_in
        message = msg if ok else f"syntax: {msg}"
        if not has_in:
            message += "; 'let' without 'in'"
        return ValidationResult(
            validator=self.name,
            version=self.version,
            passed=passed,
            details={"balanced": ok, "has_in": has_in},
            message=message,
        )


class Plugin(DomainPlugin):
    def validators(self):
        return [DaxSyntaxValidator(), MSyntaxValidator()]

    def skills(self):
        cite = " Cite knowledge items."
        return [
            Skill(
                "explain_dax",
                "Explain what a DAX expression does, step by step, including filter-context effects.",
                "You are a DAX expert. Explain the given expression precisely, naming filter and row context effects."
                + cite,
            ),
            Skill(
                "write_dax",
                "Write a DAX measure for a described business requirement.",
                "You are a DAX expert. Write a correct, idiomatic measure. Prefer variables, DIVIDE and CALCULATE "
                "with explicit filters." + cite,
            ),
            Skill(
                "review_dax",
                "Review a DAX measure for correctness and performance issues.",
                "You are a DAX reviewer. Identify correctness risks (context transition, ALL vs REMOVEFILTERS) "
                "and performance issues." + cite,
            ),
            Skill(
                "troubleshoot_refresh",
                "Diagnose Power BI refresh and gateway failures.",
                "You are a Power BI administrator. Diagnose the refresh problem using the knowledge items; "
                "list checks in order." + cite,
            ),
        ]

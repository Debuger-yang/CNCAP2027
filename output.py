"""Write the score sheet and its machine-readable audit trail."""
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape, quoteattr


def write_report(report, path):
    write_atomic(report, path, write_excel)


def write_atomic(report, path, excel_writer):
    path = Path(path)
    if path.suffix.lower() not in {".xlsx", ".json"}:
        raise ValueError("结果文件需使用 .xlsx 或 .json 扩展名")
    temporary = NamedTemporaryFile(delete=False, dir=path.parent, suffix=path.suffix)
    temporary.close()
    temp = Path(temporary.name)
    try:
        if path.suffix.lower() == ".xlsx":
            excel_writer(report, temp)
        else:
            with temp.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def flat_rows(obj, prefix=""):
    if isinstance(obj, dict):
        for key, child in obj.items():
            yield from flat_rows(child, f"{prefix}.{key}" if prefix else key)
    elif isinstance(obj, (list, tuple)):
        for i, child in enumerate(obj):
            yield from flat_rows(child, f"{prefix}[{i}]")
    else:
        yield [prefix, obj]


GROUP_NAMES = {"low_beam": "基础近光", "high_beam": "远光", "adb": "自适应远光",
               "bonus": "加分项", "penalty": "扣分项"}


def display_score(value, *, regulation=False):
    if value is None:
        return "未评定"
    text = f"{value:.3f}"
    # Regulation optical scores have two places. A fixed increment may need three.
    return text[:-1] if regulation and text.endswith("0") else text


def summaries(r):
    """Yield paired scores without substituting a partial sum for a missing total."""
    yield "整车得分", r["total"], r["regulation_total"]
    yield "已评项小计", r["known_subtotal"], r["regulation_known_subtotal"]
    for group, score in r["base_subtotals"].items():
        yield GROUP_NAMES[group] + "小计", score, r["regulation_base_subtotals"][group]


def score_table(r):
    yield ["C-NCAP 2027 整车灯光性能", r["project"]]
    yield ["评算方式", r["assessment"], "ADB", "有" if r["has_adb"] else "无"]
    yield ["组别", "评价项", "测量值 (m)", "PPT 三位得分", "规程计分", "分值下限", "分值上限", "状态", "规程条款",
           "均值 (m)", "标准偏差 (m)", "满分界限 (m)", "未评说明", "取值来源"]
    for label, score, regulation in summaries(r):
        state = "待补数据" if score is None else "已评"
        if label == "已评项小计" and r["missing"]:
            state = f"尚有 {len(r['missing'])} 项未评"
        yield ["汇总", label, None, score, regulation, None, 10 if label == "整车得分" else None, state]
    for row in r["items"]:
        reason = row.get("reason") or (", ".join(row.get("missing_scenarios", [])))
        if row["score"] is None and not reason:
            reason = "缺少有效测量值"
        source = row.get("value_source")
        if source is None and row.get("value_m") is not None and row["group"] in {"low_beam", "high_beam"}:
            source = "ies" if row["group"] in r.get("inputs", {}) else "measured"
        yield [GROUP_NAMES[row["group"]], row["label"], row.get("value_m"), row["score"],
               row["regulation_score"], row.get("min_score", 0), row.get("max_score"),
               "已评" if row["score"] is not None else "未评定", row["clause"],
               row.get("mean_m"), row.get("sd_m"), row.get("upper_m"), reason,
               {"measured": "录入实测值", "ies": "IES 工程估算"}.get(source)]


def write_excel(r, path):
    # Inline strings preserve literal input and avoid executing text as Excel formulas.
    tabs = [("评分", list(score_table(r)), [22, 46, 18, 18, 18, 15, 15, 24, 36, 16, 16, 18, 80, 20]),
            ("测量明细", [["字段", "值"], *flat_rows(r["measurement_details"])], [86, 90]),
            ("输入及依据", [["字段", "值"], ["依据", r["basis"]], ["PDF SHA256", r["source_pdf_sha256"]],
                         ["路照接收面", r["projection"]], ["ADB 高层接收面", r["projection_1_5m"]],
                         ["PPT 三位得分", r["score_precision"]["score"]],
                         ["规程计分", r["score_precision"]["regulation_score"]], *flat_rows(r["input_config"]),
                         *flat_rows(r["inputs"], "IES")], [68, 105])]
    write_tabs(tabs, path, number_formats={"评分": {3: "0.000", 4: "0.000", 5: "0.00#",
                                                6: "0.000", 7: "0.000"}})


def _col_name(number):
    text = ""
    while number:
        number, remainder = divmod(number-1, 26)
        text = chr(65+remainder) + text
    return text


def write_tabs(tabs, path, *, header=False, number_formats=None):
    """Write rows as literal text/numbers. Optional table headers support interactive filtering."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    relns = "http://schemas.openxmlformats.org/package/2006/relationships"
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml."
    number_formats = number_formats or {}
    formats = sorted({fmt for cols in number_formats.values() for fmt in cols.values()})
    style_indices = {fmt: i for i, fmt in enumerate(formats, start=2)}
    def xml_text(value):
        value = str(value)
        if len(value.encode("utf-16-le")) // 2 > 32767:
            raise ValueError("单个值超过 Excel 单元格限制，请使用 JSON 导出")
        return escape("".join(c for c in value if (c >= " " and c not in "\ufffe\uffff") or c in "\t\r\n"))
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        sheets, rels, overrides = [], [], []
        for i, (name, rows, widths) in enumerate(tabs, 1):
            target = f"worksheets/sheet{i}.xml"
            columns = number_formats.get(name, {})
            sheets.append(f'<sheet name={quoteattr(name)} sheetId="{i}" r:id="rId{i}"/>')
            rels.append(f'<Relationship Id="rId{i}" Type="{rns}/worksheet" Target="{target}"/>')
            overrides.append(f'<Override PartName="/xl/{target}" ContentType="{ct}worksheet+xml"/>')
            cols = "".join(f'<col min="{j}" max="{j}" width="{w}" customWidth="1"/>'
                           for j, w in enumerate(widths, 1))
            with archive.open(f"xl/{target}", "w") as stream:
                view = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
                        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>') if header else ""
                stream.write(f'<worksheet xmlns="{ns}">{view}<cols>{cols}</cols><sheetData>'.encode())
                index = 0
                for index, values in enumerate(rows, 1):
                    if index > 1048576:
                        raise ValueError("测量明细超过 Excel 行数限制，请使用 JSON 导出")
                    cells = []
                    style = ' s="1"' if header and index == 1 else ""
                    for col, value in enumerate(values, 1):
                        if value is None:
                            continue
                        addr = f"{_col_name(col)}{index}"
                        if type(value) in (int, float):
                            if not math.isfinite(value):
                                raise ValueError(f"{name} {addr} 存在非有限数值")
                            fmt = columns.get(col)
                            numeric_style = f' s="{style_indices[fmt]}"' if fmt and not style else style
                            cells.append(f'<c r="{addr}"{numeric_style}><v>{value}</v></c>')
                        else:
                            cells.append(f'<c r="{addr}"{style} t="inlineStr"><is><t xml:space="preserve">{xml_text(value)}</t></is></c>')
                    stream.write(f'<row r="{index}">{"".join(cells)}</row>'.encode())
                filter_ref = f'<autoFilter ref="A1:{_col_name(len(widths))}{index}"/>' if header and index else ""
                stream.write(f'</sheetData>{filter_ref}</worksheet>'.encode())
        if header or formats:
            rels.append(f'<Relationship Id="rIdStyle" Type="{rns}/styles" Target="styles.xml"/>')
            overrides.append(f'<Override PartName="/xl/styles.xml" ContentType="{ct}styles+xml"/>')
            num_defs = "".join(f'<numFmt numFmtId="{n}" formatCode={quoteattr(fmt)}/>'
                               for n, fmt in enumerate(formats, start=164))
            xf_defs = "".join(f'<xf numFmtId="{n}" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                              for n in range(164, 164 + len(formats)))
            archive.writestr("xl/styles.xml", f'<styleSheet xmlns="{ns}"><numFmts count="{len(formats)}">{num_defs}</numFmts>'
                '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>'
                '<fills count="3"><fill><patternFill patternType="none"/></fill>'
                '<fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid">'
                '<fgColor rgb="FF244869"/><bgColor indexed="64"/></patternFill></fill></fills>'
                '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                f'<cellXfs count="{2+len(formats)}"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                f'<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>{xf_defs}</cellXfs>'
                '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="{rns}"><sheets>{"".join(sheets)}</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="{relns}">{"".join(rels)}</Relationships>')
        archive.writestr("_rels/.rels", f'<Relationships xmlns="{relns}"><Relationship Id="rId1" Type="{rns}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         f'<Override PartName="/xl/workbook.xml" ContentType="{ct}sheet.main+xml"/>{"".join(overrides)}</Types>')

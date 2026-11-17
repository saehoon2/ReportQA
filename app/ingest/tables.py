"""PDF 표를 '행 이름 + 열 제목 = 값' 형태의 글로 재구성 (v1: 표 구조 반영)

PyMuPDF의 표 칸 나누기는 한국 통계표에서 칸이 무너지는 경우가 많아,
표 영역(bbox)만 PyMuPDF로 찾고, 칸은 글자 좌표로 직접 재구성합니다.
  1) 쉼표·소수점 숫자가 처음 나오는 줄부터를 '데이터 행', 그 위를 '열 제목 줄'로 봄
  2) 데이터 숫자의 x 위치를 묶어 열을 찾음
  3) 열마다 위쪽 제목 줄에서 제목을 모음 (여러 열에 걸친 큰 제목, 두 줄짜리 제목 칸 처리)
  4) 각 숫자에 '행 이름 + 열 제목'을 붙여 한 줄씩 씀
"""
import re
import statistics

import pymupdf

_NUM = re.compile(r"^[△▲]?[<(]?[-+]?\d[\d,]*(\.\d+)?%?[)>]?\*?$")
_FOOT = re.compile(r"^\d{1,2}\)$")             # 각주 표시 "1)" 는 숫자로 보지 않음
_DATA = re.compile(r"\d,\d|\d\.\d")             # 쉼표·소수점 숫자 = 데이터 행의 신호
AGE = re.compile(r"\d+\s*[~～]\s*\d+세|\d+세\s*이상")
HEADING = re.compile(r"^\s*((Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ|Ⅶ|Ⅷ)\s*\.|\d{1,2}\.(?!\d))\s*\S")   # "5.0%" 같은 숫자는 제외


def is_num(t: str) -> bool:
    return bool(_NUM.match(t)) and not _FOOT.match(t)


# ---------- 단어 읽기 ----------
def _split_glued(text):
    """'◦비경제활동인구4,030' 처럼 글자와 숫자가 붙은 단어를 나눔"""
    m = re.match(r"^(.*[가-힣)\]>])([-+]?\d[\d,]*(\.\d+)?)$", text)
    return [m.group(1), m.group(2)] if m else [text]


def _join_dates(words):
    """'2025.' + '5' 처럼 나뉜 '연도. 월'을 한 단어로 합침 (월 숫자가 데이터 열로 오인되지 않게)"""
    out = []
    for w in words:
        p = out[-1] if out else None
        if (p and re.fullmatch(r"\d{4}\.", p[4]) and re.fullmatch(r"\d{1,2}", w[4])
                and abs((p[1] + p[3]) / 2 - (w[1] + w[3]) / 2) <= 3 and 0 <= w[0] - p[2] < 8):
            out[-1] = (p[0], p[1], w[2], w[3], p[4] + " " + w[4])
        else:
            out.append(w)
    return out


def _words_in(page, bbox):
    out = []
    raw = [tuple(w[:5]) for w in page.get_text("words", clip=pymupdf.Rect(bbox))]
    for x0, y0, x1, y1, t in _join_dates(raw):
        ages = AGE.findall(t)
        if len(ages) >= 2 and "".join(ages) == t.replace(" ", ""):
            # '15~19세20~24세25~29세' 처럼 붙어 버린 열 제목을 글자 수 비율로 나눔
            pos = x0
            for a in ages:
                w = (x1 - x0) * len(a) / len(t)
                out.append((pos, y0, pos + w, y1, a)); pos += w
            continue
        parts = _split_glued(t)
        if len(parts) == 2:
            cut = x0 + (x1 - x0) * len(parts[0]) / len(t)
            out += [(x0, y0, cut, y1, parts[0]), (cut, y0, x1, y1, parts[1])]
        else:
            out.append((x0, y0, x1, y1, t))
    return out


def _group_lines(words, tol=3):
    lines = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        yc = (w[1] + w[3]) / 2
        if lines and abs(lines[-1]["y"] - yc) <= tol:
            lines[-1]["words"].append(w)
        else:
            lines.append({"y": yc, "words": [w]})
    for l in lines:
        l["words"].sort(key=lambda w: w[0])
    return lines


def _cluster(xs, gap=12):
    xs = sorted(xs)
    cols = [[xs[0]]]
    for x in xs[1:]:
        (cols[-1].append(x) if x - cols[-1][-1] <= gap else cols.append([x]))
    return [sum(c) / len(c) for c in cols]


def _segments(words, join=4):
    segs = []
    for w in words:
        if segs and w[0] - segs[-1]["x1"] < join and not (AGE.fullmatch(w[4]) or AGE.fullmatch(segs[-1]["t"].split()[-1])):
            segs[-1]["t"] += " " + w[4]; segs[-1]["x1"] = w[2]
        else:
            segs.append({"t": w[4], "x0": w[0], "x1": w[2]})
    return segs


# ---------- 표 재구성 ----------
def caption_above(page, bbox):
    """표 위쪽 70pt 안에서 표 제목(< … >, 표 n-n)과 단위 줄을 찾음"""
    clip = pymupdf.Rect(bbox[0] - 40, max(0, bbox[1] - 70), bbox[2] + 40, bbox[1] + 2)
    lines = [re.sub(r"\s+", " ", l).strip() for l in page.get_text(clip=clip, sort=True).splitlines() if l.strip()]
    cap = [l for l in lines if re.match(r"^(<|〈|\[?\s*표\s*\d)", l)]
    unit = [l for l in lines if "단위" in l]
    return (cap[-1] if cap else ""), (unit[-1] if unit else "")


def parse_table(page, bbox):
    """(열 제목 목록, [{label, cells}]) 를 돌려줌. 표가 아니면 None"""
    lines = _group_lines(_words_in(page, bbox))
    first = next((i for i, l in enumerate(lines)
                  if sum(bool(_DATA.search(w[4])) and is_num(w[4]) for w in l["words"]) >= 2), None)
    if first is None:
        return None
    head, body = lines[:first], lines[first:]
    num_x = [(w[0] + w[2]) / 2 for l in body for w in l["words"] if is_num(w[4])]
    cols = _cluster(num_x)
    if len(cols) < 2:
        return None
    gap = statistics.median([b - a for a, b in zip(cols, cols[1:])])
    left = cols[0] - gap / 2

    hlines = []
    for hl in head:
        segs = _segments([w for w in hl["words"] if (w[0] + w[2]) / 2 > left and "단위" not in w[4]])
        if segs:
            hlines.append({"y": hl["y"], "segs": segs})

    def same_cols(a, b):
        return len(a["segs"]) == len(b["segs"]) and all(
            abs((s["x0"] + s["x1"]) / 2 - (t["x0"] + t["x1"]) / 2) < 5 for s, t in zip(a["segs"], b["segs"]))

    # 두 줄짜리 제목 칸 합치기 ("변화"+"없음"), 사이에 다른 줄이 끼어 있어도 처리
    used, merged = set(), []
    for i, a in enumerate(hlines):
        if i in used:
            continue
        partner = next((j for j in (i + 1, i + 2) if j < len(hlines) and j not in used
                        and hlines[j]["y"] - a["y"] <= 26 and same_cols(a, hlines[j])), None)
        if partner is not None:
            b = hlines[partner]; used.add(partner)
            merged.append({"y": (a["y"] + b["y"]) / 2,
                           "segs": [{"t": s["t"] + " " + t["t"], "x0": min(s["x0"], t["x0"]), "x1": max(s["x1"], t["x1"])}
                                    for s, t in zip(a["segs"], b["segs"])]})
        else:
            merged.append(a)
    rows_h = []
    for l in sorted(merged, key=lambda l: l["y"]):
        if rows_h and abs(rows_h[-1]["y"] - l["y"]) <= 6:
            rows_h[-1]["segs"] = sorted(rows_h[-1]["segs"] + l["segs"], key=lambda s: s["x0"])
        else:
            rows_h.append({"y": l["y"], "segs": list(l["segs"])})

    # 열 제목 붙이기 (2단계)
    #  1단계: 제목 조각이 열 바로 위에 있으면(조각 폭 ± 열 간격의 45%) 그 열의 제목
    #  2단계: 여러 열에 걸친 큰 제목 줄에서, 1단계로 제목을 못 받은 열은 가까운 큰 제목을 받음
    #         단, 그 열에 큰 제목과 같은 높이(±8pt)의 자기 제목이 이미 있으면 받지 않음
    #         (예: '모집단'처럼 위아래로 긴 칸은 옆의 큰 제목 '적정 표본 수' 아래가 아님)
    tol = gap * 0.45
    def covers(s, cx):
        return s["x0"] - tol <= cx <= s["x1"] + tol
    def center(s):
        return (s["x0"] + s["x1"]) / 2

    picked = [dict() for _ in cols]          # 열 번호 → {제목 줄 번호: 조각}
    for ri, r in enumerate(rows_h):
        for ci, cx in enumerate(cols):
            hit = [s for s in r["segs"] if covers(s, cx)]
            if hit:
                picked[ci][ri] = hit[0]
    for ri, r in enumerate(rows_h):
        segs = r["segs"]
        if len(segs) >= 0.7 * len(cols):
            continue
        # 열마다 상태: 덮은 조각 번호 / None(아직 없음) / "X"(같은 높이에 자기 제목이 있어 받지 않음)
        owner = []
        for ci in range(len(cols)):
            if ri in picked[ci]:
                owner.append(next(si for si, s in enumerate(segs) if s is picked[ci][ri]))
            elif any(abs(rows_h[rj]["y"] - r["y"]) <= 8 for rj in picked[ci]):
                owner.append("X")
            else:
                owner.append(None)

        # 큰 제목은 자기가 덮는 열들의 가운데에 놓임 → 덮는 범위를 양옆으로 넓혀 가며
        # '조각 가운데 ≈ 덮는 열들의 가운데'가 유지되는 동안만 열을 받음
        def err(si, idxs):
            return abs(center(segs[si]) - sum(cols[i] for i in idxs) / len(idxs)) if idxs else gap

        def near(si, ci):
            s = segs[si]
            return abs(center(s) - cols[ci]) <= max(gap * 2.5, (s["x1"] - s["x0"]) / 2 + gap)

        def free(ci):
            return 0 <= ci < len(cols) and owner[ci] is None

        def wall(i, j):   # 열 사이가 유난히 넓으면(보통 간격의 1.3배 초과) 열 묶음의 경계 → 큰 제목이 넘어가지 않음
            return abs(cols[j] - cols[i]) > gap * 1.3

        def open_(ci, frm):
            return free(ci) and not wall(frm, ci)

        while True:
            best = None
            for si in range(len(segs)):
                mine = [ci for ci in range(len(cols)) if owner[ci] == si]
                if mine:
                    a, b = min(mine), max(mine)
                    # (넓힐 열, 허용 오차 증가). 반대쪽이 막혀 있으면(표 끝·묶음 경계·다른 제목)
                    # 한쪽만 넓혀도 어느 정도 어긋남을 허용
                    left_ok, right_ok = open_(a - 1, a) if a > 0 else False, open_(b + 1, b) if b + 1 < len(cols) else False
                    tries = []
                    if left_ok and right_ok:
                        tries.append(([a - 1, b + 1], gap * 0.25))
                    if left_ok:
                        tries.append(([a - 1], 0 if right_ok else gap * 0.5))
                    if right_ok:
                        tries.append(([b + 1], 0 if left_ok else gap * 0.5))
                else:   # 아무 열도 안 덮은 큰 제목: 가장 가까운 빈 열부터
                    tries = [([ci], gap) for ci in range(len(cols)) if free(ci)]
                for add, allow in tries:
                    if not all(near(si, c) for c in add):
                        continue
                    delta = err(si, mine + add) - err(si, mine)
                    if (delta <= allow if allow > 0 else delta < 0) and (best is None or delta < best[0]):
                        best = (delta, si, add)
            if best is None:
                break
            for c in best[2]:
                owner[c] = best[1]
                picked[c][ri] = segs[best[1]]
    col_names = []
    for ci in range(len(cols)):
        parts = [picked[ci][ri]["t"] for ri in sorted(picked[ci])]
        col_names.append(" ".join(dict.fromkeys(parts)) or f"열{ci + 1}")

    rows, label_only = [], []
    for l in body:
        lab = " ".join(w[4] for w in l["words"] if (w[0] + w[2]) / 2 < left)
        cells = {}
        for w in l["words"]:
            xc = (w[0] + w[2]) / 2
            if xc >= left and is_num(w[4]):
                ci = min(range(len(cols)), key=lambda i: abs(cols[i] - xc))
                cells[ci] = (cells.get(ci, "") + " " + w[4]).strip()
        if cells:
            rows.append({"y": l["y"], "label": lab, "cells": cells})
        elif lab:
            label_only.append({"y": l["y"], "label": lab})
    for r in rows:   # 이름이 없는 행: 가까운(±12pt) 이름 줄 → 없으면 위 행 이름을 이어 받음
        if not r["label"]:
            near = [x for x in label_only if abs(x["y"] - r["y"]) <= 12]
            if near:
                r["label"] = min(near, key=lambda x: abs(x["y"] - r["y"]))["label"]
    last = ""
    for r in rows:
        r["label"] = r["label"] or last
        last = r["label"]
    return col_names, rows


def table_lines(page, bbox, section=""):
    """표 하나를 (머리말, [행 문장들]) 로. 머리말 = [섹션] 표 제목 (단위)"""
    parsed = parse_table(page, bbox)
    if not parsed:
        return None
    col_names, rows = parsed
    cap, unit = caption_above(page, bbox)
    header = " ".join(x for x in [f"[표] [{section}]" if section else "[표]", cap, unit] if x)
    body = [f"- {r['label']}: " + "; ".join(f"{col_names[i]} = {v}" for i, v in sorted(r["cells"].items()))
            for r in rows]
    return header, body


# ---------- 쪽 단위 처리 ----------
def update_sections(page, sections):
    """쪽의 큰 제목(Ⅰ. … / 1. …)을 읽어 현재 섹션을 갱신 (쪽을 넘어가도 이어짐)"""
    for line in page.get_text(sort=True).splitlines()[:12]:
        line = re.sub(r"\s+", " ", line).strip()
        if re.match(r"^제\s*\d*\s*장\s*\d*\s+\S", line) and len(line) <= 60:   # "제1장 …" (추출 시 "제장1"로 깨지기도 함)
            sections[:] = [line]
        elif HEADING.match(line) and len(line) <= 60:
            if re.match(r"^\s*(Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ|Ⅶ|Ⅷ)", line):
                sections[:] = [line]
            else:
                sections[1:] = [line]
    return " > ".join(sections)


def text_without_tables(page, bboxes):
    """표 영역의 단어를 뺀 본문 글 (표 글자가 뒤섞여 들어가는 것을 막음)"""
    rects = [pymupdf.Rect(b) for b in bboxes]
    lines = {}
    for x0, y0, x1, y1, t, b, l, _ in page.get_text("words", sort=True):
        if any(r.contains(pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2)) for r in rects):
            continue
        lines.setdefault((b, l), []).append(t)
    return "\n".join(" ".join(ws) for ws in lines.values())


def split_table(header, body, max_chars=800):
    """긴 표는 행 단위로 나누고, 조각마다 머리말을 반복 (어느 조각만 검색돼도 표 제목을 알 수 있게)"""
    chunks, cur = [], []
    for line in body:
        if cur and len(header) + sum(len(x) + 1 for x in cur) + len(line) > max_chars:
            chunks.append(header + "\n" + "\n".join(cur)); cur = []
        cur.append(line)
    if cur:
        chunks.append(header + "\n" + "\n".join(cur))
    return chunks

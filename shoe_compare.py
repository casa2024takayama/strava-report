#!/usr/bin/env python3
"""
シューズ比較ページ（shoes.html）を生成する。

- シューズは Strava のギア設定（アクティビティ詳細の gear）で判定
- 全期間の詳細キャッシュ（.strava_cache/details/*.json）を横断して集計
- ペースを揃えて比較：1km ラップ単位で、ペース・気温・走行距離（疲労）を補正した
  重回帰でシューズ間の差（心拍・パワー・ピッチ・歩幅）を推定する
- Garmin の「ラップ CSV」（Garmin Connect → アクティビティ → CSV エクスポート）を
  garmin_laps/ に置くと、接地時間・上下動などのランニングダイナミクスも比較する
  （距離と時間から Strava のランへ自動で対応付け）

使い方:  python3 shoe_compare.py
"""

from __future__ import annotations

import csv
import glob
import html
import json
import os
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DETAILS_DIR = os.path.join(ROOT, ".strava_cache", "details")
GARMIN_DIR = os.path.join(ROOT, "garmin_laps")
OUT_FILE = os.path.join(ROOT, "shoes.html")

RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
MIN_LAP_M, MAX_LAP_M = 950, 1050      # 1km オートラップのみ（端数ラップ・手動ラップは除外）
MAX_LAP_ELEV_M = 8                    # 坂のラップは除外（勾配で心拍・パワーが歪むため）
REF_PACES = [(4, 15), (4, 30), (5, 0), (5, 30), (6, 0)]
MIN_LAPS_PER_SHOE = 8                 # これ未満は回帰に使わない
SIMILAR_DIST = 0.15                   # 同距離ラン比較：±15%
MAX_SHOES = 3                         # 散布図の系列上限（色の判別性の都合）

# 指標定義: key, 表示名, 単位, 小数桁, 低いほど良いか（None=良し悪しなし）
METRICS = [
    ("hr", "心拍", "bpm", 1, True),
    ("watts", "パワー", "W", 0, True),
    ("spm", "ピッチ", "spm", 1, None),
    ("stride", "歩幅", "m", 2, None),
]
DYN_METRICS = [
    ("gct", "接地時間", "ms", 0, True),
    ("vo", "上下動", "cm", 1, True),
    ("vr", "上下動比", "%", 1, True),
]


# ── 読み込み ────────────────────────────────────────────────────────────
def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _hms_to_sec(s: str) -> float | None:
    try:
        parts = [float(p) for p in str(s).split(":")]
    except ValueError:
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def load_runs() -> list[dict]:
    runs = []
    for path in glob.glob(os.path.join(DETAILS_DIR, "*.json")):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("sport_type") not in RUN_TYPES and d.get("type") not in RUN_TYPES:
            continue
        gear = d.get("gear") or {}
        run = {
            "id": d["id"],
            "date": d["start_date_local"][:10],
            "name": d.get("name", ""),
            "dist_km": (d.get("distance") or 0) / 1000,
            "moving_s": d.get("moving_time") or 0,
            "elapsed_s": d.get("elapsed_time") or 0,
            "speed": d.get("average_speed") or 0,
            "hr": d.get("average_heartrate"),
            "watts": d.get("average_watts"),
            "temp": d.get("average_temp"),
            "elev": d.get("total_elevation_gain") or 0,
            "gear_id": d.get("gear_id") or "none",
            "gear_name": gear.get("name") or "（シューズ未設定）",
            "gear_km": (gear.get("distance") or 0) / 1000,
            "laps": [],
        }
        km_done = 0.0
        for lap in d.get("laps") or []:
            dist = lap.get("distance") or 0
            mt = lap.get("moving_time") or 0
            spd = lap.get("average_speed") or 0
            cad = lap.get("average_cadence")
            ok = (
                MIN_LAP_M <= dist <= MAX_LAP_M and mt > 0 and spd > 0
                and lap.get("average_heartrate") and cad
                and (lap.get("total_elevation_gain") or 0) <= MAX_LAP_ELEV_M
                and km_done >= 1.0     # 1km 目（ウォームアップ）は除外
            )
            if ok:
                spm = cad * 2           # Strava は片足回転数
                run["laps"].append({
                    "idx": lap.get("lap_index"),
                    "d_end": km_done * 1000 + dist,   # 累積距離（Garmin ラップとの対応付け用）
                    "dist_m": dist,
                    "speed": spd,
                    "hr": lap.get("average_heartrate"),
                    "watts": lap.get("average_watts"),
                    "spm": spm,
                    "stride": spd * 60 / spm,
                    "km_into": km_done,
                    "temp": run["temp"],
                })
            km_done += dist / 1000
        runs.append(run)
    runs.sort(key=lambda r: r["date"])
    return runs


def attach_garmin(runs: list[dict]) -> int:
    """garmin_laps/*.csv（Garmin のラップ CSV）を距離・時間でランに対応付ける。"""
    matched = 0
    for path in sorted(glob.glob(os.path.join(GARMIN_DIR, "*.csv"))):
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        summary = next((r for r in rows if r.get("ラップ数") == "概要"), None)
        if not summary:
            continue
        g_dist = _num(summary.get("距離 km"))
        g_time = _hms_to_sec(summary.get("タイム", ""))
        if g_dist is None or g_time is None:
            continue
        run = next((r for r in runs
                    if abs(r["dist_km"] - g_dist) <= 0.03
                    and min(abs(r["moving_s"] - g_time), abs(r["elapsed_s"] - g_time)) <= 20), None)
        if not run:
            print(f"  ⚠️ {os.path.basename(path)}: 対応する Strava ランが見つかりません")
            continue
        # ラップ番号は Strava 側で極小ラップが落ちてずれ、ラップ時間も秒未満の切り捨てや
        # 停止時間の扱いで食い違うため、「累積距離（±60m）とラップ距離（±30m）」で対応付ける
        g_laps, cum = [], 0.0
        for r in rows:
            if r.get("ラップ数", "").isdigit():
                dm = (_num(r.get("距離 km")) or 0) * 1000
                cum += dm
                g_laps.append((cum, dm, r))
        for lap in run["laps"]:
            g = next((r for c, dm, r in g_laps
                      if abs(c - lap["d_end"]) <= 60 and abs(dm - lap["dist_m"]) <= 30), None)
            if not g:
                continue
            lap["gct"] = _num(g.get("平均接地時間 ms"))
            lap["vo"] = _num(g.get("平均上下動 cm"))
            lap["vr"] = _num(g.get("平均上下動比 %"))
        matched += 1
    return matched


# ── 統計 ────────────────────────────────────────────────────────────────
def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """小さな連立一次方程式（部分ピボット付きガウス消去）。"""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-9:
            return None
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def ols(xs: list[list[float]], ys: list[float]) -> list[float] | None:
    k = len(xs[0])
    if len(ys) <= k + 2:
        return None
    xtx = [[sum(x[i] * x[j] for x in xs) for j in range(k)] for i in range(k)]
    xty = [sum(x[i] * y for x, y in zip(xs, ys)) for i in range(k)]
    return _solve(xtx, xty)


def adjusted_effects(laps_by_shoe: dict, shoes: list[str], key: str) -> dict | None:
    """指標 ~ 速度 + 気温 + 走行距離 + シューズ のプール回帰。基準シューズとの差を返す。"""
    rows = []
    for si, sid in enumerate(shoes):
        for lap in laps_by_shoe[sid]:
            if lap.get(key) is None or lap.get("temp") is None:
                continue
            dummies = [1.0 if si == j else 0.0 for j in range(1, len(shoes))]
            rows.append(([1.0, lap["speed"], lap["temp"], lap["km_into"], *dummies], lap[key]))
    if len(shoes) < 2 or not rows:
        return None
    counts = {sid: sum(1 for l in laps_by_shoe[sid] if l.get(key) is not None) for sid in shoes}
    if min(counts.values()) < MIN_LAPS_PER_SHOE:
        return None
    beta = ols([r[0] for r in rows], [r[1] for r in rows])
    if not beta:
        return None
    return {sid: beta[4 + i - 1] for i, sid in enumerate(shoes) if i > 0}


def per_shoe_fit(laps: list[dict], key: str):
    """シューズ単体の 指標 ~ 速度 の単回帰（参照ペースでの推定値用）。"""
    pts = [(l["speed"], l[key]) for l in laps if l.get(key) is not None]
    if len(pts) < MIN_LAPS_PER_SHOE:
        return None
    beta = ols([[1.0, s] for s, _ in pts], [v for _, v in pts])
    if not beta:
        return None
    lo, hi = min(s for s, _ in pts), max(s for s, _ in pts)
    return beta, lo, hi


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


# ── 表示ヘルパ ──────────────────────────────────────────────────────────
def fmt_pace(speed: float) -> str:
    if not speed:
        return "—"
    sec = round(1000 / speed)
    return f"{sec // 60}:{sec % 60:02d}"


def fmt(v, digits=1, signed=False):
    if v is None:
        return "—"
    s = f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"
    return s.replace("-", "−")


def esc(s) -> str:
    return html.escape(str(s))


def shoe_cell(name, color) -> str:
    return (f'<td class="shoe"><span class="swatch" style="background:{color}"></span>'
            f'<span class="shoe-name">{esc(name)}</span></td>')


def diff_cell(d, digits, lower_better):
    if d is None:
        return '<td class="num">—</td>'
    cls = ""
    if lower_better is not None and abs(d) >= 10 ** -digits:
        good = (d < 0) == lower_better
        cls = " good" if good else " bad"
    return f'<td class="num diff{cls}">{fmt(d, digits, signed=True)}</td>'


# ── セクション ──────────────────────────────────────────────────────────
def section_cards(shoes, runs_by_shoe, laps_by_shoe, colors):
    cards = []
    for sid in shoes:
        rs = runs_by_shoe[sid]
        total = sum(r["dist_km"] for r in rs)
        gear_km = max((r["gear_km"] for r in rs), default=0)
        temp = _mean([l["temp"] for l in laps_by_shoe[sid]])
        cards.append(f"""
      <div class="card">
        <div class="card-title"><span class="swatch" style="background:{colors[sid]}"></span>{esc(rs[0]['gear_name'])}</div>
        <div class="card-big">{len(rs)}<small> ラン</small> · {total:.0f}<small> km</small></div>
        <div class="card-sub">{rs[0]['date']} 〜 {rs[-1]['date']}</div>
        <div class="card-sub">Strava 累計 {gear_km:.0f} km · 比較対象ラップ {len(laps_by_shoe[sid])} 本 · 平均気温 {fmt(temp, 1)}℃</div>
      </div>""")
    return f'<div class="cards">{"".join(cards)}</div>'


def section_adjusted(shoes, laps_by_shoe, names):
    if len(shoes) < 2:
        return ""
    base = shoes[0]
    rows = []
    for key, label, unit, digits, lower in METRICS + DYN_METRICS:
        eff = adjusted_effects(laps_by_shoe, shoes, key)
        if eff is None:
            if key in {m[0] for m in DYN_METRICS}:
                continue
            cells = "".join('<td class="num">—</td>' for _ in shoes[1:])
        else:
            cells = "".join(diff_cell(eff[sid], digits, lower) for sid in shoes[1:])
        rows.append(f"<tr><th>{label}<small> {unit}</small></th>{cells}</tr>")
    head = "".join(f"<th class='num'>{esc(names[s])}</th>" for s in shoes[1:])
    return f"""
    <section>
      <h2>同じ条件なら、どれだけ違うか</h2>
      <p class="lead">基準は <b>{esc(names[base])}</b>。1km ラップごとに「ペース・気温・何 km 目か」を回帰で揃えたうえでの差です。
      心拍・パワー・接地時間・上下動は<span class="good-ink">低いほど楽／効率的</span>、ピッチ・歩幅は走り方の変化として見てください。</p>
      <div class="table-wrap"><table>
        <thead><tr><th>指標（基準との差）</th>{head}</tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table></div>
      <p class="note">各シューズ {MIN_LAPS_PER_SHOE} ラップ以上で算出。サンプルが少ないうちは ±2〜3 bpm 程度は誤差の範囲です。</p>
    </section>"""


def section_ref_paces(shoes, laps_by_shoe, names, colors):
    head = "".join(f"<th class='num'>{p[0]}:{p[1]:02d}</th>" for p in REF_PACES)
    body = []
    for key, label, unit, digits, _ in METRICS + DYN_METRICS:
        fits = {sid: per_shoe_fit(laps_by_shoe[sid], key) for sid in shoes}
        if all(f is None for f in fits.values()):
            continue
        for sid in shoes:
            f = fits[sid]
            cells = []
            for m, s in REF_PACES:
                spd = 1000 / (m * 60 + s)
                if f and f[1] - 0.05 <= spd <= f[2] + 0.05:   # 実走したペース範囲のみ（外挿しない）
                    cells.append(f'<td class="num">{fmt(f[0][0] + f[0][1] * spd, digits)}</td>')
                else:
                    cells.append('<td class="num muted">—</td>')
            body.append(f"<tr><th>{label}<small> {unit}</small></th>{shoe_cell(names[sid], colors[sid])}{''.join(cells)}</tr>")
    return f"""
    <section>
      <h2>ペース別の推定値</h2>
      <p class="lead">シューズごとにラップを「ペース → 指標」で直線近似し、各ペースでの値を推定しました。実際に走ったペース範囲の外は表示しません。</p>
      <div class="table-wrap"><table>
        <thead><tr><th>指標</th><th>シューズ</th>{head}</tr></thead>
        <tbody>{''.join(body)}</tbody>
      </table></div>
    </section>"""


def section_scatter(shoes, laps_by_shoe, names, colors):
    pts = [(sid, l) for sid in shoes for l in laps_by_shoe[sid]]
    if not pts:
        return ""
    W, H, L, R, T, B = 720, 360, 48, 16, 16, 40
    paces = [1000 / l["speed"] for _, l in pts]
    hrs = [l["hr"] for _, l in pts]
    x0, x1 = (min(paces) // 15) * 15, (max(paces) // 15 + 1) * 15
    y0, y1 = (min(hrs) // 10) * 10, (max(hrs) // 10 + 1) * 10
    sx = lambda p: L + (p - x0) / (x1 - x0) * (W - L - R)
    sy = lambda h: T + (y1 - h) / (y1 - y0) * (H - T - B)
    grid = []
    for h in range(int(y0), int(y1) + 1, 10):
        grid.append(f'<line x1="{L}" x2="{W-R}" y1="{sy(h):.1f}" y2="{sy(h):.1f}" class="grid"/>'
                    f'<text x="{L-6}" y="{sy(h)+4:.1f}" class="tick" text-anchor="end">{h}</text>')
    p = x0
    while p <= x1:
        grid.append(f'<text x="{sx(p):.1f}" y="{H-B+18}" class="tick" text-anchor="middle">{int(p)//60}:{int(p)%60:02d}</text>')
        p += 30
    lines, dots = [], []
    for sid in shoes:
        f = per_shoe_fit(laps_by_shoe[sid], "hr")
        if f:
            (a, b), _, _ = f
            spds = sorted(l["speed"] for l in laps_by_shoe[sid])
            lo, hi = spds[int(len(spds) * 0.05)], spds[int(len(spds) * 0.95) - 1]
            # 横軸はペース（速度の逆数）なので、速度に線形な近似は曲線になる → 折れ線で描く
            curve = " ".join(f"{sx(1000 / v):.1f},{sy(a + b * v):.1f}"
                             for v in (lo + (hi - lo) * i / 24 for i in range(25)))
            lines.append(f'<polyline points="{curve}" fill="none" stroke="{colors[sid]}" class="fit"/>')
    for sid, l in pts:
        tip = f"{esc(names[sid])}｜{fmt_pace(l['speed'])}/km · 心拍 {l['hr']:.0f} · {fmt(l.get('watts'), 0)} W · {l['date']} {l['idx']}km目"
        dots.append(f'<circle cx="{sx(1000/l["speed"]):.1f}" cy="{sy(l["hr"]):.1f}" r="5" fill="{colors[sid]}" '
                    f'class="dot" data-tip="{tip}"/>')
    legend = "".join(f'<span class="lg"><span class="swatch" style="background:{colors[s]}"></span>{esc(names[s])}</span>'
                     for s in shoes)
    return f"""
    <section>
      <h2>ペースと心拍（1km ラップ）</h2>
      <p class="lead">右ほど遅いペース。同じペースで点が下にあるほど、楽に走れています。線はシューズごとの近似線です。</p>
      <div class="legend">{legend}</div>
      <div class="chart" id="scatter">
        <svg viewBox="0 0 {W} {H}" role="img" aria-label="ペースと心拍の散布図">
          {''.join(grid)}
          <text x="{(W+L)/2}" y="{H-4}" class="axis-label" text-anchor="middle">ペース（分/km）</text>
          {''.join(dots)}{''.join(lines)}
        </svg>
        <div class="tip" id="tip" hidden></div>
      </div>
    </section>"""


def section_bands(shoes, laps_by_shoe, names, colors):
    bands: dict[int, dict[str, list]] = {}
    for sid in shoes:
        for l in laps_by_shoe[sid]:
            b = int(1000 / l["speed"] // 10 * 10)
            bands.setdefault(b, {}).setdefault(sid, []).append(l)
    has_dyn = any(l.get("gct") for sid in shoes for l in laps_by_shoe[sid])
    metrics = METRICS + (DYN_METRICS if has_dyn else [])
    head = "".join(f"<th class='num'>{label}</th>" for _, label, *_ in metrics)
    body = []
    for b in sorted(bands):
        shared = len(bands[b]) >= 2
        for i, sid in enumerate(shoes):
            laps = bands[b].get(sid)
            if not laps:
                continue
            cells = "".join(f'<td class="num">{fmt(_mean([l.get(k) for l in laps]), d)}</td>'
                            for k, _, _, d, _ in metrics)
            band_label = f"{b//60}:{b%60:02d}〜{(b+9)//60}:{(b+9)%60:02d}"
            body.append(f'<tr class="{"shared" if shared else ""}"><th>{band_label}</th>{shoe_cell(names[sid], colors[sid])}'
                        f'<td class="num">{len(laps)}</td>{cells}</tr>')
    return f"""
    <section>
      <h2>ペース帯ごとのラップ平均</h2>
      <p class="lead">10 秒刻みのペース帯で、補正なしの生の平均です。<span class="shared-ink">色付きの行</span>は複数のシューズで走ったペース帯（直接比べられる行）です。</p>
      <div class="table-wrap"><table>
        <thead><tr><th>ペース帯</th><th>シューズ</th><th class="num">本数</th>{head}</tr></thead>
        <tbody>{''.join(body)}</tbody>
      </table></div>
    </section>"""


def section_similar(shoes, runs_by_shoe, names, colors):
    if len(shoes) < 2:
        return ""
    newest = shoes[-1]
    blocks = []
    for r in list(reversed([r for r in runs_by_shoe[newest] if r["dist_km"] >= 5]))[:5]:
        peers = [p for sid in shoes[:-1] for p in runs_by_shoe[sid]
                 if abs(p["dist_km"] - r["dist_km"]) <= r["dist_km"] * SIMILAR_DIST]
        peers = sorted(peers, key=lambda p: p["date"], reverse=True)[:5]
        rows = []
        for p in [r] + peers:
            ef = p["speed"] * 60 / p["hr"] if p["hr"] else None
            rows.append(
                f'<tr class="{"self" if p is r else ""}"><td>{p["date"]}</td>'
                f'<td class="shoe"><span class="swatch" style="background:{colors.get(p["gear_id"], "#999")}"></span>'
                f'<span class="shoe-name">{esc(names[p["gear_id"]])}</span></td>'
                f'<td class="num">{p["dist_km"]:.1f}</td><td class="num">{fmt_pace(p["speed"])}</td>'
                f'<td class="num">{fmt(p["hr"], 0)}</td><td class="num">{fmt(ef, 2)}</td>'
                f'<td class="num">{fmt(p["watts"], 0)}</td><td class="num">{fmt(p["temp"], 0)}</td>'
                f'<td class="num">{p["elev"]:.0f}</td></tr>')
        blocks.append(f"""
      <h3>{r['date']} {esc(r['name'])}（{r['dist_km']:.1f} km）</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>日付</th><th>シューズ</th><th class="num">距離 km</th><th class="num">ペース</th>
        <th class="num">心拍</th><th class="num">効率</th><th class="num">パワー W</th><th class="num">気温 ℃</th><th class="num">獲得標高 m</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table></div>""")
    if not blocks:
        return ""
    return f"""
    <section>
      <h2>同じくらいの距離のラン</h2>
      <p class="lead">{esc(names[newest])} の直近のラン（5km 以上・最大 5 本）と、距離 ±{int(SIMILAR_DIST*100)}% の他シューズのラン（直近 5 本）。
      「効率」は 速度（m/分）÷ 心拍 で、高いほど同じ心拍で速く走れています。練習の強度が違うと比較しにくいので、上の補正済みの差と合わせて見てください。</p>
      {''.join(blocks)}
    </section>"""


def section_race(runs, colors):
    """フルマラソンの自己ベストを 5km 区間に分け、ペースと走りの崩れ方を示す（新シューズ評価の基準）。"""
    fulls = [r for r in runs if r["dist_km"] >= 40 and r["laps"]]
    if not fulls:
        return ""
    race = min(fulls, key=lambda r: r["moving_s"] / r["dist_km"])
    has_dyn = any(l.get("gct") for l in race["laps"])
    metrics = [("hr", "心拍", 0), ("watts", "パワー W", 0), ("spm", "ピッチ", 0), ("stride", "歩幅 m", 2)]
    if has_dyn:
        metrics += [("gct", "接地時間 ms", 0), ("vo", "上下動 cm", 1), ("vr", "上下動比 %", 1)]
    segs: dict[int, list] = {}
    for l in race["laps"]:
        segs.setdefault(int(l["km_into"] // 5), []).append(l)
    rows = []
    for k in sorted(segs):
        ls = segs[k]
        spd = _mean([l["speed"] for l in ls])
        cells = "".join(f'<td class="num">{fmt(_mean([l.get(m) for l in ls]), d)}</td>' for m, _, d in metrics)
        end = f"{k*5+5} km" if k * 5 + 5 < race["dist_km"] else "ゴール"
        rows.append(f'<tr><th>{k*5} km〜{end}</th><td class="num">{fmt_pace(spd)}</td>{cells}</tr>')
    head = "".join(f"<th class='num'>{label}</th>" for _, label, _ in metrics)
    first = [l for l in race["laps"] if l["km_into"] < 21]
    last = [l for l in race["laps"] if l["km_into"] >= 30]
    def chg(key, digits):
        a, b = _mean([l.get(key) for l in first]), _mean([l.get(key) for l in last])
        return fmt(b - a if a is not None and b is not None else None, digits, signed=True)
    fade = (f"前半（〜21km）→ 30km 以降で、ペース {fmt_pace(_mean([l['speed'] for l in first]))} → "
            f"{fmt_pace(_mean([l['speed'] for l in last]))}/km、歩幅 {chg('stride', 2)} m、ピッチ {chg('spm', 0)} spm")
    if has_dyn:
        fade += f"、接地時間 {chg('gct', 0)} ms、上下動比 {chg('vr', 1)} %"
    net = race["moving_s"]
    return f"""
    <section>
      <h2>レースの基準：{esc(race['name'])}（{race['date']}）</h2>
      <p class="lead"><span class="swatch" style="background:{colors.get(race['gear_id'], '#999')}"></span>
      {esc(race['gear_name'])} で {race['dist_km']:.2f} km・{net//3600}:{net%3600//60:02d}:{net%60:02d}（GPS 距離）。
      5km ごとの平均です（1km 目・坂・端数ラップは除く）。</p>
      <div class="table-wrap"><table>
        <thead><tr><th>区間</th><th class="num">ペース</th>{head}</tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table></div>
      <p class="note">{fade}。レースの崩れ方は強度（心拍 170 台・4:20〜4:30/km）込みの結果なので、新しいシューズで比べるときは同じ強度のラン（マラソンペース走・レース）どうしで見てください。ジョグのロング走とは直接比べられません。</p>
    </section>"""


# ── ページ ──────────────────────────────────────────────────────────────
CSS = """
:root{color-scheme:light;--bg:#fcfcfb;--surface:#ffffff;--ink:#1C1917;--ink2:#52514e;--muted:#8a8984;--line:#e6e4df;
  --good:#0f7a3a;--bad:#b3261e;--shared:#eef4fc;--self:#fff4ec}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#141413;--surface:#1a1a19;
  --ink:#ffffff;--ink2:#c3c2b7;--muted:#8f8e87;--line:#33332f;--good:#5cc98a;--bad:#f28b82;--shared:#1d2a3a;--self:#35251a}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#141413;--surface:#1a1a19;--ink:#ffffff;--ink2:#c3c2b7;--muted:#8f8e87;
  --line:#33332f;--good:#5cc98a;--bad:#f28b82;--shared:#1d2a3a;--self:#35251a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Segoe UI",sans-serif;line-height:1.6;-webkit-font-smoothing:antialiased}
main{max-width:920px;margin:0 auto;padding:24px 16px 64px}
header a{color:var(--ink2);font-size:14px}
h1{font-size:24px;margin:8px 0 4px} h2{font-size:18px;margin:40px 0 6px} h3{font-size:15px;margin:24px 0 6px}
.lead{color:var(--ink2);font-size:14px;margin:0 0 12px} .note{color:var(--muted);font-size:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:16px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px}
.card-title{font-weight:600;display:flex;align-items:center;gap:8px}
.card-big{font-size:22px;font-weight:700;margin:4px 0} .card-big small{font-size:13px;font-weight:400;color:var(--ink2)}
.card-sub{font-size:12px;color:var(--ink2)}
.shoe{display:flex;align-items:center;gap:6px}.shoe-name{max-width:9em;overflow:hidden;text-overflow:ellipsis}
.swatch{display:inline-block;width:10px;height:10px;border-radius:50%;flex:none}
.table-wrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
thead th{color:var(--ink2);font-weight:600;font-size:12px} tbody tr:last-child>*{border-bottom:none}
th small{color:var(--muted);font-weight:400} .num{text-align:right;font-variant-numeric:tabular-nums}
.muted{color:var(--muted)} .diff.good,.good-ink{color:var(--good)} .diff.bad{color:var(--bad)} .diff{font-weight:600}
tr.shared>*{background:var(--shared)} tr.self>*{background:var(--self);font-weight:600}
.shared-ink{background:var(--shared);padding:0 4px;border-radius:4px}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--ink2);margin-bottom:6px}
.lg{display:inline-flex;align-items:center;gap:6px}
.chart{position:relative;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px}
.chart svg{width:100%;height:auto;display:block}
.grid{stroke:var(--line);stroke-width:1} .tick{fill:var(--muted);font-size:14px} .axis-label{fill:var(--ink2);font-size:14px}
.fit{stroke-width:3;stroke-linecap:round;stroke-linejoin:round;pointer-events:none;filter:drop-shadow(0 0 1.5px var(--surface))}
.dot{stroke:var(--surface);stroke-width:2;opacity:.85;cursor:default} .dot:hover{opacity:1;stroke:var(--ink)}
.tip{position:absolute;pointer-events:none;background:var(--ink);color:var(--bg);font-size:12px;padding:6px 8px;border-radius:6px;white-space:nowrap;transform:translate(-50%,-120%)}
.empty{background:var(--surface);border:1px dashed var(--line);border-radius:10px;padding:16px;color:var(--ink2);font-size:14px;margin-top:16px}
"""

JS = """
(function(){var c=document.getElementById('scatter');if(!c)return;var t=document.getElementById('tip');
c.querySelectorAll('.dot').forEach(function(d){
 d.addEventListener('mouseenter',function(){var r=c.getBoundingClientRect(),b=d.getBoundingClientRect();
  t.textContent=d.dataset.tip;t.style.left=(b.left+b.width/2-r.left)+'px';t.style.top=(b.top-r.top)+'px';t.hidden=false;});
 d.addEventListener('mouseleave',function(){t.hidden=true;});});})();
"""

# 系列色（light / dark 共通で判別できる 3 色・固定順）
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]


def build() -> str:
    runs = load_runs()
    n_garmin = attach_garmin(runs)
    for r in runs:
        for l in r["laps"]:
            l["date"] = r["date"]

    runs_by_shoe: dict[str, list] = {}
    for r in runs:
        runs_by_shoe.setdefault(r["gear_id"], []).append(r)
    # 初使用日の順（古い＝基準、新しい＝右端）。系列が多すぎる場合は直近のシューズに絞る
    shoes = sorted(runs_by_shoe, key=lambda s: runs_by_shoe[s][0]["date"])
    shoes = [s for s in shoes if s != "none"][-MAX_SHOES:] or shoes[-MAX_SHOES:]
    names = {s: runs_by_shoe[s][0]["gear_name"] for s in runs_by_shoe}
    colors = {s: SERIES[i] for i, s in enumerate(shoes)}
    laps_by_shoe = {s: [l for r in runs_by_shoe[s] for l in r["laps"]] for s in shoes}

    if len(shoes) < 2:
        waiting = f"""<div class="empty">👟 比較できるシューズはまだ 1 足です。
        Strava で新しいシューズを登録し、そのシューズで走ったランに設定すると、次回のデータ更新から比較が表示されます
        （既に取り込んだランのギアを後から変えた場合も自動で取り直します）。</div>"""
    else:
        waiting = ""

    dyn_note = (f"Garmin ランニングダイナミクス: {n_garmin} ラン分を対応付け済み（garmin_laps/）。"
                if n_garmin else
                "Garmin のラップ CSV を garmin_laps/ に置くと、接地時間・上下動も比較できます。")
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>シューズ比較</title>
<style>{CSS}</style></head>
<body><main>
  <header><a href="index.html">← 月次レポートへ</a>
    <h1>👟 シューズ比較</h1>
    <p class="lead">Strava のギア設定から、全 {len(runs)} ラン・{sum(len(v) for v in laps_by_shoe.values())} ラップ（坂・1km 目・端数ラップを除く 1km ラップ）を集計。
    更新: {datetime.now():%Y-%m-%d %H:%M}</p>
  </header>
  {section_cards(shoes, runs_by_shoe, laps_by_shoe, colors)}
  {waiting}
  {section_adjusted(shoes, laps_by_shoe, names)}
  {section_race(runs, colors)}
  {section_similar(shoes, runs_by_shoe, names, colors)}
  {section_scatter(shoes, laps_by_shoe, names, colors)}
  {section_ref_paces(shoes, laps_by_shoe, names, colors)}
  {section_bands(shoes, laps_by_shoe, names, colors)}
  <p class="note">{dyn_note} 気温は Garmin の手首温度計の値で、実際の気温より高めに出ます（比較には使えます）。</p>
</main><script>{JS}</script></body></html>"""


def main():
    page = build()
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"✓ {os.path.basename(OUT_FILE)} を生成しました（シューズ比較）")


if __name__ == "__main__":
    main()

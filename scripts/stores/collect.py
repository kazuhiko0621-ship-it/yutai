#!/usr/bin/env python3
"""優待が使える店舗を各社サイトから集め、stores.json を作り直す。

GitHub Actions から定期実行される（.github/workflows/update-stores.yml）。
手元で動かす場合:  python3 scripts/stores/collect.py [--prefs 大阪府,京都府] [銘柄コード ...]

対象の都道府県は Supabase の yutai_store_areas で管理する:
  - auto_update = true  … 毎回（定期更新・手動実行）収集し直す
  - auto_update = false … 前回のデータをそのまま残し、収集しない
  - 行がない県           … 店舗データから削除する
--prefs（手動実行の「都道府県」欄）を指定したときは、その県だけを収集し、他の県は前回のデータを残す。

- 各社の取得処理は sources.py の SOURCES に登録する（fetch(県) で1県分を返す）
- 取得に失敗した会社・件数が急減した会社は、その県について前回のデータをそのまま残す
- 座標が取れない店舗は国土地理院の住所検索APIで補う
- 前回との差分（開店・閉店）を stores_changes.md と GitHub のジョブサマリーに出す
"""
import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import geocode, load_areas, load_scopes  # noqa: E402
from prefs import PREFS, find, pref_of_address  # noqa: E402
from sources import SOURCES  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "stores.json"
CHANGES = ROOT / "stores_changes.md"
JST = timezone(timedelta(hours=9))
SHRINK_LIMIT = 0.5  # 前回の半分未満になったら異常とみなして前回分を残す
DEFAULT_AREAS = {"愛知県": True}  # Supabaseが読めないときの扱い


def load_prev():
    if OUT.exists():
        return json.loads(OUT.read_text(encoding="utf-8"))
    return {"companies": {}, "stores": []}


def store_key(s):
    return f'{s["c"]}|{s["id"]}'


def pref_of(s):
    return s.get("p") or pref_of_address(s.get("a", "")) or ""


def plan_areas(areas, forced, prev_prefs):
    """(収集する県, データを残す県) を決める"""
    if areas is None:  # Supabaseが読めない：前回あった県は残し、既定の県だけ収集
        areas = {p: False for p in prev_prefs} | DEFAULT_AREAS
    if forced:
        refresh = [p for p in forced]
        keep = [p for p in areas if p not in refresh] + [p for p in prev_prefs if p not in areas and p not in refresh]
    else:
        refresh = [p for p, auto in areas.items() if auto]
        keep = [p for p, auto in areas.items() if not auto]
    order = list(PREFS)
    return sorted(set(refresh), key=order.index), sorted(set(keep) - set(refresh), key=order.index)


def main(only_codes, forced_prefs):
    prev = load_prev()
    prev_stores = prev.get("stores", [])
    prev_by = {}  # (銘柄コード, 県) → 前回の店舗
    for s in prev_stores:
        s.setdefault("p", pref_of(s))
        prev_by.setdefault((s["c"], s["p"]), []).append(s)
    prev_geo = {store_key(s): (s["lat"], s["lng"]) for s in prev_stores if s.get("lat")}
    prev_companies = prev.get("companies", {})

    areas = load_areas(ROOT / "index.html")
    refresh, keep = plan_areas(areas, forced_prefs, sorted({s["p"] for s in prev_stores if s["p"]}))
    print(f"[info] 収集する県: {'・'.join(refresh) or 'なし'} / データを残す県: {'・'.join(keep) or 'なし'}")
    scopes = load_scopes(ROOT / "index.html")
    print(f"[info] 取得範囲: {len(scopes)}銘柄分をSupabaseから読込")

    companies, stores, problems = {}, [], []
    for src in SOURCES:
        code = src["code"]
        info = {k: src[k] for k in ("name", "brands", "url", "note") if k in src}
        pinfo = prev_companies.get(code, {})
        sc = scopes.get(code.upper(), {"scope": "all", "exclude_brands": []})
        if sc["scope"] == "none":
            companies[code] = info | {"scope": "none", "count": 0}
            print(f"[skip] {code} {src['name']}: 取得範囲「なし」")
            continue
        excluded = set(sc.get("exclude_brands") or []) if sc["scope"] == "some" else set()
        info["scope"] = sc["scope"]
        totals = dict(pinfo.get("totals", {}))          # 県ごとの除外前の件数
        bc_by_pref = dict(pinfo.get("brand_counts_by_pref", {}))
        errors = {}
        rows_out = []

        for pname in keep:
            rows_out += prev_by.get((code, pname), [])
        for pname in refresh:
            old = prev_by.get((code, pname), [])
            if only_codes and code not in only_codes:
                rows_out += old
                continue
            pr = PREFS[pname]
            try:
                got = [s for s in src["fetch"](pr) if s.get("a", "").startswith(pr.name)]
                prev_total = totals.get(pname, len(old) if old else 0)
                if not got and prev_total:
                    raise RuntimeError(f"0件でした（前回{prev_total}件）")
                if prev_total and len(got) < prev_total * SHRINK_LIMIT:
                    raise RuntimeError(f"件数が急減（{prev_total}→{len(got)}）")
                bc = {}
                for s in got:
                    bc[s.get("b", "")] = bc.get(s.get("b", ""), 0) + 1
                totals[pname], bc_by_pref[pname] = len(got), bc
                seen = set()
                for s in got:
                    if s.get("b", "") in excluded:
                        continue
                    s["c"], s["id"], s["p"] = code, str(s["id"]), pname
                    # 「吉野家 栄店」のように店舗名の先頭にブランド名があれば省く（ブランドは別欄に出る）
                    b = s.get("b", "")
                    if b and s["n"].startswith(b) and len(s["n"]) > len(b):
                        s["n"] = s["n"][len(b):].lstrip(" 　")
                    if store_key(s) in seen:
                        continue
                    seen.add(store_key(s))
                    if not s.get("lat"):
                        cached = prev_geo.get(store_key(s))
                        if cached:
                            s["lat"], s["lng"] = cached
                        else:
                            ll = geocode(s["a"])
                            if ll:
                                s["lat"], s["lng"] = ll
                                s["geo"] = "gsi"  # 住所からの推定座標
                    rows_out.append(s)
                print(f"[ok] {code} {src['name']} {pname}: {len(seen)}件")
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                errors[pname] = str(e)
                problems.append(f"{code} {src['name']}（{pname}）: {e}")
                rows_out += old
                print(f"[NG] {code} {src['name']} {pname}: {e}（前回データを維持: {len(old)}件）")

        live = set(refresh) | set(keep)
        totals = {p: n for p, n in totals.items() if p in live}
        bc_by_pref = {p: v for p, v in bc_by_pref.items() if p in live}
        brand_counts = {}
        for v in bc_by_pref.values():
            for b, n in v.items():
                brand_counts[b] = brand_counts.get(b, 0) + n
        info["totals"], info["brand_counts_by_pref"] = totals, bc_by_pref
        info["brand_counts"] = dict(sorted(brand_counts.items(), key=lambda kv: -kv[1]))
        info["total"] = sum(totals.values())
        info["count"] = len(rows_out)
        info["fetched"] = datetime.now(JST).strftime("%Y-%m-%d") if not errors else pinfo.get("fetched")
        if errors:
            info["error"] = "、".join(f"{p}: {m}" for p, m in errors.items())
        companies[code] = info
        stores += [{k: s[k] for k in ("c", "id", "p", "b", "n", "a", "lat", "lng", "x", "geo") if s.get(k) not in (None, "")}
                   for s in rows_out]

    order = list(PREFS)
    stores.sort(key=lambda s: (order.index(s["p"]) if s.get("p") in PREFS else 99, s["c"], s.get("a", ""), s.get("n", "")))
    area_list = sorted(set(refresh) | set(keep), key=order.index)
    area_info = {p: {"count": sum(1 for s in stores if s.get("p") == p),
                     "auto_update": (areas or DEFAULT_AREAS).get(p, False),
                     "updated": datetime.now(JST).strftime("%Y-%m-%d") if p in refresh
                     else prev.get("areas", {}).get(p, {}).get("updated", prev.get("updated"))}
                 for p in area_list}
    data = {
        "updated": datetime.now(JST).strftime("%Y-%m-%d"),
        "area": "・".join(area_list),
        "areas": area_info,
        "companies": companies,
        "stores": stores,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 差分
    old_map = {store_key(s): s for s in prev_stores}
    new_map = {store_key(s): s for s in stores}
    added = [new_map[k] for k in new_map.keys() - old_map.keys()]
    removed = [old_map[k] for k in old_map.keys() - new_map.keys()]
    lines = [f"# 店舗データ更新 {data['updated']}（収集: {'・'.join(refresh) or 'なし'}）", "",
             f"- 合計 {len(stores)}件（前回 {len(old_map)}件）", f"- 新規 {len(added)}件 / 削除 {len(removed)}件",
             "- 県別: " + "、".join(f"{p} {v['count']}件" for p, v in area_info.items()), ""]
    if problems:
        lines += ["## 取得できなかった会社（前回データを維持）", ""] + [f"- {p}" for p in problems] + [""]
    new_prefs = {p for p in refresh if not any(s.get("p") == p for s in prev_stores)}
    if old_map:  # 初回・新しく追加した県は全件が「新規」になるので一覧は出さない
        name = {c: v.get("name", c) for c, v in companies.items()}
        for title, rows in (("新規（開店）", [s for s in added if s.get("p") not in new_prefs]),
                            ("削除（閉店・対象外）", removed)):
            if rows:
                lines += [f"## {title}", ""]
                for s in sorted(rows, key=lambda s: (s["c"], s["a"]))[:300]:
                    lines.append(f"- {name.get(s['c'], s['c'])} {s.get('b', '')} {s['n']}（{s['a']}）")
                lines.append("")
    text = "\n".join(lines)
    CHANGES.write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefs", default=os.environ.get("PREFS", ""), help="収集する県（カンマ・空白区切り）")
    ap.add_argument("codes", nargs="*", help="収集する銘柄コード（省略で全社）")
    args = ap.parse_args()
    forced = []
    for x in args.prefs.replace("、", ",").replace(" ", ",").split(","):
        if x.strip():
            pr = find(x)
            if not pr:
                sys.exit(f"都道府県名が分かりません: {x}")
            forced.append(pr.name)
    main(set(args.codes), forced)

#!/usr/bin/env python3
"""優待が使える店舗を各社サイトから集め、stores.json を作り直す。

GitHub Actions から定期実行される（.github/workflows/update-stores.yml）。
手元で動かす場合:  python3 scripts/stores/collect.py [銘柄コード ...]

- 各社の取得処理は sources.py の SOURCES に登録する
- 取得に失敗した会社・件数が急減した会社は、前回のデータをそのまま残す
- 座標が取れない店舗は国土地理院の住所検索APIで補う
- 前回との差分（開店・閉店）を stores_changes.md と GitHub のジョブサマリーに出す
"""
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import geocode  # noqa: E402
from sources import SOURCES, PREF  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "stores.json"
CHANGES = ROOT / "stores_changes.md"
JST = timezone(timedelta(hours=9))
SHRINK_LIMIT = 0.5  # 前回の半分未満になったら異常とみなして前回分を残す


def load_prev():
    if OUT.exists():
        return json.loads(OUT.read_text(encoding="utf-8"))
    return {"companies": {}, "stores": []}


def store_key(s):
    return f'{s["c"]}|{s["id"]}'


def main(only):
    prev = load_prev()
    prev_by_code = {}
    for s in prev.get("stores", []):
        prev_by_code.setdefault(s["c"], []).append(s)
    prev_geo = {store_key(s): (s["lat"], s["lng"]) for s in prev.get("stores", []) if s.get("lat")}

    companies, stores, problems = {}, [], []
    for src in SOURCES:
        code = src["code"]
        old = prev_by_code.get(code, [])
        if only and code not in only:
            stores.extend(old)
            if code in prev.get("companies", {}):
                companies[code] = prev["companies"][code]
            continue
        info = {k: src[k] for k in ("name", "brands", "url", "note") if k in src}
        try:
            got = src["fetch"]()
            # 住所が対象県のものだけに絞る（サイト側の検索結果に隣県が混ざることがあるため）
            got = [s for s in got if s.get("a", "").startswith(PREF)]
            if not got:
                raise RuntimeError("0件でした")
            if old and len(got) < len(old) * SHRINK_LIMIT:
                raise RuntimeError(f"件数が急減（{len(old)}→{len(got)}）")
            seen = set()
            for s in got:
                s["c"] = code
                s["id"] = str(s["id"])
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
                stores.append({k: s[k] for k in ("c", "id", "b", "n", "a", "lat", "lng", "x", "geo") if s.get(k) not in (None, "")})
            info["count"] = len(seen)
            info["fetched"] = datetime.now(JST).strftime("%Y-%m-%d")
            print(f"[ok] {code} {src['name']}: {len(seen)}件")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            problems.append(f"{code} {src['name']}: {e}")
            stores.extend(old)
            pinfo = prev.get("companies", {}).get(code, info)
            pinfo["error"] = str(e)
            info = pinfo
            print(f"[NG] {code} {src['name']}: {e}（前回データを維持: {len(old)}件）")
        companies[code] = info

    stores.sort(key=lambda s: (s["c"], s["a"], s["n"]))
    data = {
        "updated": datetime.now(JST).strftime("%Y-%m-%d"),
        "area": PREF,
        "companies": companies,
        "stores": stores,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 差分
    old_map = {store_key(s): s for s in prev.get("stores", [])}
    new_map = {store_key(s): s for s in stores}
    added = [new_map[k] for k in new_map.keys() - old_map.keys()]
    removed = [old_map[k] for k in old_map.keys() - new_map.keys()]
    lines = [f"# 店舗データ更新 {data['updated']}（{PREF}）", "",
             f"- 合計 {len(stores)}件（前回 {len(old_map)}件）", f"- 新規 {len(added)}件 / 削除 {len(removed)}件", ""]
    if problems:
        lines += ["## 取得できなかった会社（前回データを維持）", ""] + [f"- {p}" for p in problems] + [""]
    if old_map:  # 初回は全件が「新規」になるので一覧は出さない
        name = {c: v.get("name", c) for c, v in companies.items()}
        for title, rows in (("新規（開店）", added), ("削除（閉店・対象外）", removed)):
            if rows:
                lines += [f"## {title}", ""]
                for s in sorted(rows, key=lambda s: (s["c"], s["a"])):
                    lines.append(f"- {name.get(s['c'], s['c'])} {s.get('b', '')} {s['n']}（{s['a']}）")
                lines.append("")
    text = "\n".join(lines)
    CHANGES.write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main(set(sys.argv[1:]))

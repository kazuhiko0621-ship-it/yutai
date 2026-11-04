#!/usr/bin/env python3
"""地下鉄の路線（ラインカラー付き）と駅を OpenStreetMap から取得し subway.geojson を作る。

地図タイルには路線ごとの色情報がないため、Overpass API で
route=subway のリレーション（colour タグ付き）と地下鉄駅を取得して別ファイルに持つ。
GitHub Actions（update-stores.yml）から店舗データと一緒に定期実行される。
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import UA  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "subway.geojson"
PREF = "愛知県"
ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

# OSMに colour が無い場合の公式カラー（名古屋市営地下鉄）
FALLBACK = {"東山線": "#E3A21A", "名城線": "#7E57A3", "名港線": "#7E57A3",
            "鶴舞線": "#0F78B9", "桜通線": "#D2122F", "上飯田線": "#E85298"}

QUERY = f"""
[out:json][timeout:180];
area["name"="{PREF}"]["admin_level"="4"]->.a;
(
  rel(area.a)["route"="subway"];
)->.r;
.r out tags geom;
node(area.a)["railway"="station"]["station"="subway"];
out body;
"""


def overpass():
    data = urllib.parse.urlencode({"data": QUERY}).encode()
    last = None
    for ep in ENDPOINTS:
        try:
            req = urllib.request.Request(ep, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[warn] {ep}: {e}")
    raise RuntimeError(f"Overpass取得失敗: {last}")


def line_name(tags):
    for k in ("name:ja", "name"):
        v = tags.get(k, "")
        for key in FALLBACK:
            if key in v:
                return key
    return tags.get("name:ja") or tags.get("name") or tags.get("ref") or ""


def main():
    j = overpass()
    feats, seen_lines = [], {}
    for el in j["elements"]:
        if el["type"] == "relation":
            t = el.get("tags", {})
            name = line_name(t)
            color = t.get("colour") or t.get("color") or FALLBACK.get(name) or "#555555"
            segs = []
            for m in el.get("members", []):
                if m["type"] == "way" and m.get("role", "") in ("", "forward", "backward") and m.get("geometry"):
                    segs.append([[round(p["lon"], 5), round(p["lat"], 5)] for p in m["geometry"]])
            if not segs:
                continue
            # 上り・下りで同じ路線のリレーションが2本あるので路線名でまとめる
            key = name or str(el["id"])
            if key in seen_lines:
                seen_lines[key]["geometry"]["coordinates"].extend(segs)
                continue
            f = {"type": "Feature", "properties": {"kind": "line", "name": name, "color": color},
                 "geometry": {"type": "MultiLineString", "coordinates": segs}}
            seen_lines[key] = f
            feats.append(f)
        elif el["type"] == "node":
            t = el.get("tags", {})
            name = t.get("name:ja") or t.get("name")
            if name:
                feats.append({"type": "Feature", "properties": {"kind": "station", "name": name},
                              "geometry": {"type": "Point", "coordinates": [round(el["lon"], 6), round(el["lat"], 6)]}})
    # 同じ区間のwayの重複を除く
    for f in seen_lines.values():
        uniq, s = [], set()
        for seg in f["geometry"]["coordinates"]:
            k = json.dumps(seg)
            if k not in s:
                s.add(k)
                uniq.append(seg)
        f["geometry"]["coordinates"] = uniq
    lines = [f for f in feats if f["properties"]["kind"] == "line"]
    stations = [f for f in feats if f["properties"]["kind"] == "station"]
    if not lines:
        raise RuntimeError("地下鉄路線が0件でした")
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False,
                              separators=(",", ":")), encoding="utf-8")
    print(f"[ok] 地下鉄 路線{len(lines)}（{'・'.join(f['properties']['name'] for f in lines)}） 駅{len(stations)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        # 失敗しても前回のファイルを残し、店舗データの更新は止めない
        print(f"[NG] 地下鉄データ: {e}")

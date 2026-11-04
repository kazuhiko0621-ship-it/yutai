"""会社ごとの店舗取得処理。

1社 = SOURCES の1要素。fetch() は次のキーを持つdictのリストを返す:
  id  : その会社の中で一意な店舗ID
  b   : ブランド名（例: はなまるうどん）
  n   : 店舗名
  a   : 住所（都道府県から）
  lat, lng : 緯度経度（取れなければ省略。国土地理院APIで補完される）
  x   : 注意書き（任意。例: 優待利用可否が不明）
"""
from common import get

PREF = "愛知県"
PREF_CODE = "23"


# ---------------------------------------------------------------------------
# NAVITIME 店舗検索サービス（吉野家など多くのチェーンが利用）
# ---------------------------------------------------------------------------
def navitime(base, brand_of=None, extra=None):
    """base 例: https://stores.yoshinoya.com/yoshinoya"""
    def fetch():
        rows, offset, total = [], 0, 1
        while offset < total:
            params = {"datum": "wgs84", "limit": 50, "offset": offset, "address": PREF_CODE, "sort": "admin_jis"}
            if extra:
                params.update(extra)
            j = get(f"{base}/api/proxy2/shop/list", params)
            total = j["count"]["total"]
            for it in j["items"]:
                cats = {c["level"]: c["name"] for c in it.get("categories", [])}
                rows.append({
                    "id": it["code"],
                    "b": brand_of(it, cats) if brand_of else cats.get("middle", ""),
                    "n": it["name"],
                    "a": it.get("address_name", ""),
                    "lat": it.get("coord", {}).get("lat"),
                    "lng": it.get("coord", {}).get("lon"),
                })
            offset += 50
        return rows
    return fetch


# ---------------------------------------------------------------------------
# コメダ（自社API。KOMECA取扱店のみ優待が使える）
# ---------------------------------------------------------------------------
def komeda():
    api = "https://eu.komeda.co.jp/v1/hp/shop"
    # 8=コメダ珈琲店（海外）は対象外。ブランド名はサイトのメタデータから取る
    meta = get("https://eu.komeda.co.jp/v1/hp/shop-meta-data")
    bt = next(f for f in meta["fields"] if f["name"] == "brand_type")
    brands = {int(v["id"]): v["display_name"] for v in bt["values"] if v["id"] != "8"}
    rows = []
    for bt, bname in brands.items():
        lst = get(api, {"brand_type": bt, "all": "true", "prefecture": PREF})["items"]
        for it in lst:
            s = get(f"{api}/{it['id']}")["shop"]
            pay = next((d for d in s.get("details", []) if d.get("id") == "207-4"), None)
            komeca = None if pay is None else (19 in pay.get("values", []))
            if komeca is False:
                continue  # KOMECA非対応店は優待（電子マネー）が使えない
            row = {"id": it["id"], "b": bname, "n": it["name"], "a": it["address"],
                   "lat": s.get("shop_latitude"), "lng": s.get("shop_longitude")}
            if komeca is None:
                row["x"] = "KOMECA対応か未掲載"
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# マクドナルド（地図範囲指定のAPI。県の範囲を格子に分けて取得）
# ---------------------------------------------------------------------------
AICHI_BOUNDS = (34.55, 136.65, 35.45, 137.85)  # 南, 西, 北, 東


def mcdonalds():
    s, w, n, e = AICHI_BOUNDS
    grid, rows = 6, {}
    for i in range(grid):
        for k in range(grid):
            b = (s + (n - s) * i / grid, w + (e - w) * k / grid,
                 s + (n - s) * (i + 1) / grid, w + (e - w) * (k + 1) / grid)
            for p in get("https://map.mcdonalds.co.jp/api/poi", {"bounds": ",".join(f"{v:.5f}" for v in b)}):
                rows[p["key"]] = {"id": p["key"], "b": "マクドナルド", "n": p["name"], "a": p["address"],
                                  "lat": p["latitude"], "lng": p["longitude"]}
    return list(rows.values())


# ---------------------------------------------------------------------------
# 登録一覧（code は優待手帳の銘柄マスタのコードと一致させる）
# ---------------------------------------------------------------------------
SOURCES = [
    {
        "code": "9861", "name": "吉野家ホールディングス", "brands": ["吉野家"],
        "url": "https://stores.yoshinoya.com/yoshinoya/",
        "note": "はなまるうどん等のグループ店舗でも使えます（地図は未収録）",
        "fetch": navitime("https://stores.yoshinoya.com/yoshinoya",
                          brand_of=lambda it, c: "吉野家",
                          extra={"exclude-category": "0101003.02"}),
    },
    {
        "code": "3543", "name": "コメダホールディングス", "brands": ["おかげ庵", "KOMEDA is □", "BAKERY ADEMOK", "大餡吉日", "ジェリコ堂", "米屋の太郎"],
        "url": "https://www.komeda.co.jp/shop/",
        "note": "KOMECA取扱店のみ",
        "fetch": komeda,
    },
    {
        "code": "2702", "name": "日本マクドナルドホールディングス", "brands": ["マクドナルド"],
        "url": "https://map.mcdonalds.co.jp/",
        "note": "一部の店舗（球場・空港内など）では利用できない場合があります",
        "fetch": mcdonalds,
    },
]

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
# Canly（店舗ページCMS。あみやき亭グループなどが利用）: 全国分が1リクエストで返る
# ---------------------------------------------------------------------------
def canly(directory_id):
    def fetch():
        j = get(f"https://api.site.can-ly.com/v2/directories/{directory_id}/shops/search", {"sort": "area:asc"})
        return [{"id": s["id"], "b": (s.get("brand") or {}).get("name", ""), "n": s["nameKanji"],
                 "a": s.get("address", ""), "lat": float(s["latitude"]) if s.get("latitude") else None,
                 "lng": float(s["longitude"]) if s.get("longitude") else None}
                for s in j["shops"]]
    return fetch


# ---------------------------------------------------------------------------
# ジェイグループ（自社サイト。「株主優待券使用可」で絞り込んだ一覧をページ送りで取得）
# ---------------------------------------------------------------------------
def jgroup():
    import html as H
    import re
    rows, page = [], 1
    while True:
        base = "https://www.jgroup.jp/brand/" + (f"page/{page}/" if page > 1 else "")
        body = get(base, {"store_area_term[]": "27", "store_service[]": "株主優待券使用可"}, as_json=False)
        arts = re.findall(r'<article class="c-card_brand.*?</article>', body, re.S)
        for art in arts:
            m_id = re.search(r'/brand/(\d+)/', art)
            ttl = re.search(r'<h2 class="c-card_brand_ttl">(.*?)</h2>', art, re.S)
            addr = re.search(r'<div class="c-card_brand_txt">.*?<p>(.*?)<br', art, re.S)
            if not (m_id and ttl and addr):
                continue
            name = " ".join(x.strip() for x in re.split(r"<br\s*/?>", H.unescape(re.sub(r"<(?!br)[^>]+>", "", ttl.group(1)))) if x.strip())
            a = re.sub(r"\s+", "", H.unescape(re.sub(r"<[^>]+>", "", addr.group(1))))
            a = re.sub(r"〒?\d{3}-\d{4}", "", a)
            if not re.match(r"^.{2,3}[都道府県]", a):
                a = PREF + a  # 県名が省略されている住所（愛知県で絞り込み済み）
            rows.append({"id": m_id.group(1), "b": "", "n": name, "a": a})
        if not arts or f"page/{page + 1}/" not in body:
            break
        page += 1
    return rows


# ---------------------------------------------------------------------------
# フジオフード（自社サイトの都道府県別一覧）
# ---------------------------------------------------------------------------
FUJIO_BRANDS = ["まいどおおきに食堂", "さち福や", "串家物語", "つるまる", "かっぽうぎ", "天麩羅 えびのや", "ピノキオ",
                "デリス タルト＆カフェ", "フジオ軒", "うちの食堂", "かつ満", "ハニーミツバチ珈琲", "はらドーナッツ",
                "土山人", "SAM'S", "天鳳"]


def fujio():
    import html as H
    import re
    body = get("https://www.fujiofood.com/shop_search/index.php", {"prefName": "愛知", "brandName": ""}, as_json=False)
    rows = []
    for li in re.findall(r'<li class="prefSec-store">(.*?)</li>', body, re.S):
        ttl = re.search(r'<p class="ttl">(.*?)</p>', li, re.S)
        addr = re.search(r"<th>住所</th>\s*<td>(.*?)<span", li, re.S)
        sid = re.search(r"shop_(\d+)\.php", li)
        if not (ttl and addr and sid):
            continue
        name = re.sub(r"[\s　]+", " ", H.unescape(ttl.group(1))).strip()
        a = re.sub(r"<[^>]+>", " ", H.unescape(addr.group(1)))
        a = re.sub(r"〒\d{3}-\d{4}", "", a)
        a = re.sub(r"\s+", "", a)
        if not a.startswith(PREF):
            a = PREF + a
        brand = next((b for b in FUJIO_BRANDS if name.startswith(b)), name.split(" ")[0])
        rows.append({"id": sid.group(1), "b": brand, "n": name[len(brand):].strip() or name, "a": a})
    return rows


# ---------------------------------------------------------------------------
# 物語コーポレーション（自社API。全国分をページ送りで取得し、県で絞る）
# ---------------------------------------------------------------------------
MONOGATARI_BRANDS = {1: "焼肉きんぐ", 2: "丸源ラーメン", 3: "お好み焼本舗", 4: "ゆず庵", 5: "きゃべとん",
                     6: "牛たん大好き焼肉はっぴぃ", 7: "焼きたてのかるび", 8: "NIKUGEN", 9: "しゃぶとかに源氏",
                     10: "果実屋珈琲", 11: "濃厚中華そば餃子 丸福", 12: "二代目丸源", 13: "熟成肉とんかつロース堂",
                     14: "魚貝三昧げん屋", 15: "町中華 丸福飯店", 16: "肉讃岐 もっちりうどん源次郎"}


def monogatari():
    api = "https://shop.monogatari.co.jp/api/v1/shops/"
    rows, page, last = [], 1, 1
    while page <= last:
        j = get(api, {"page": page})
        last = j.get("last_page", 1)
        for s in j["data"]:
            if s.get("prefecture_id") != int(PREF_CODE) or s.get("not_public_flag"):
                continue
            rows.append({"id": s["shop_code"], "b": MONOGATARI_BRANDS.get(s["brand_id"], f"ブランド{s['brand_id']}"),
                         "n": s["name"], "a": s.get("address", ""), "lat": s.get("latitude"), "lng": s.get("longitude")})
        page += 1
    return rows


# ---------------------------------------------------------------------------
# ブロンコビリー（店舗一覧ページ → 各店舗ページの住所・地図座標）
# ---------------------------------------------------------------------------
def bronco():
    import html as H
    import re
    top = get("https://www.bronco.co.jp/shop/", as_json=False)
    urls = []
    for area in ("nagoya", "aichi"):
        m = re.search(r'<li class="shop_contents_list_item[^"]*" data-id="%s">(.*?)</ul>' % area, top, re.S)
        if m:
            urls += re.findall(r'href="(https://www\.bronco\.co\.jp/shop/[^"]+)"', m.group(1))
    rows = []
    for u in dict.fromkeys(urls):
        page = get(u, as_json=False)
        # 店舗名：<title>「七宝店（愛知県あま市） ステーキ…」 → なければ <h1>
        name = re.search(r"<title[^>]*>\s*([^<（(]+?)\s*[（(]", page, re.S) or re.search(r"<h1[^>]*>\s*([^<]+?)\s*<", page, re.S)
        addr = re.search(r'住所</div>\s*<div class="info_content_item_text">(.*?)</div>', page, re.S)
        ll = re.search(r"maps[^\"']*?(3[45]\.\d{3,})(?:,|%2C)\s*(13[67]\.\d{3,})", page)
        if not addr:
            continue
        rows.append({"id": u.rstrip("/").split("/")[-1], "b": "ブロンコビリー",
                     "n": H.unescape(name.group(1)).strip() if name else u.rstrip("/").split("/")[-1],
                     "a": re.sub(r"\s+", "", H.unescape(re.sub(r"<[^>]+>", "", addr.group(1)))),
                     "lat": float(ll.group(1)) if ll else None, "lng": float(ll.group(2)) if ll else None})
    return rows


# ---------------------------------------------------------------------------
# コシダカ：カラオケまねきねこ（WordPressの「もっと見る」と同じ呼び出しで県内全件を取得）
# ---------------------------------------------------------------------------
def manekineko():
    import html as H
    import json as J
    import re
    import urllib.parse
    import urllib.request
    from common import UA
    page = get("https://www.karaokemanekineko.jp/locations/aichi/", as_json=False)
    cfg = J.loads(re.search(r"LOC_AJAX\s*=\s*(\{[^;]+\})", page).group(1))
    data = urllib.parse.urlencode({"action": "loc_load_more", "nonce": cfg["nonce"], "pref": "aichi", "city": "",
                                   "q": "", "mode": "", "lat": "", "lng": "", "page": 1, "per_page": 200}).encode()
    req = urllib.request.Request(cfg["ajax_url"], data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        j = J.loads(r.read().decode("utf-8"))
    body = j["data"]["html"]
    rows = []
    for unit in re.findall(r'<div class="content__unit js-store".*?(?=<div class="content__unit js-store"|$)', body, re.S):
        br = re.search(r'content__unit-ttl__branch">(.*?)<', unit, re.S)
        ad = re.search(r'content__unit-address">(.*?)<', unit, re.S)
        href = re.search(r'href="([^"]*/locations/aichi/[^"]+)"', unit)
        if br and ad:
            rows.append({"id": (href.group(1) if href else br.group(1)).rstrip("/").split("/")[-1],
                         "b": "カラオケまねきねこ", "n": H.unescape(br.group(1)).strip(),
                         "a": re.sub(r"\s+", "", H.unescape(ad.group(1)))})
    return rows


# ---------------------------------------------------------------------------
# 第一興商：ビッグエコー（地図範囲指定のAPI。マクドナルドと同じ方式）
# ---------------------------------------------------------------------------
def bigecho():
    s, w, n, e = AICHI_BOUNDS
    grid, rows = 3, {}
    for i in range(grid):
        for k in range(grid):
            b = (s + (n - s) * i / grid, w + (e - w) * k / grid,
                 s + (n - s) * (i + 1) / grid, w + (e - w) * (k + 1) / grid)
            j = get("https://shop.big-echo.jp/api/point", {"b": ",".join(f"{v:.5f}" for v in b), "np": "{}"})
            for p in j["items"]:
                rows[p["key"]] = {"id": p["key"], "b": "ビッグエコー",
                                  "n": p["name"].replace("カラオケ ビッグエコー", "").strip() or p["name"],
                                  "a": p["address"].strip(), "lat": p["latitude"], "lng": p["longitude"]}
    return list(rows.values())


# ---------------------------------------------------------------------------
# Yext の店舗ページ（一風堂など）：県ページ → 市ページ → 店舗ページの公開HTMLをたどる
# ---------------------------------------------------------------------------
def yext_pages(base, country="日本"):
    import html as H
    import json as J
    import re
    import urllib.parse as U

    def links(path):
        body = get(base + "/" + path, as_json=False)
        return [x for x in re.findall(r'href="([^"]+)"', body) if x.startswith("../")]

    def fetch():
        pref_path = U.quote(country) + "/" + U.quote(PREF)
        store_ids, seen_city = set(), set()
        for href in links(pref_path):
            tail = href.replace("../", "")
            if re.fullmatch(r"\d+", tail):
                store_ids.add(tail)
            elif tail.startswith(country + "/" + PREF + "/") and tail not in seen_city:
                seen_city.add(tail)
                for h2 in links("/".join(U.quote(x) for x in tail.split("/"))):
                    t2 = h2.replace("../", "")
                    if re.fullmatch(r"\d+", t2):
                        store_ids.add(t2)
        rows = []
        for sid in sorted(store_ids):
            body = get(f"{base}/{sid}", as_json=False)
            for ld in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S):
                try:
                    j = J.loads(ld)
                except ValueError:
                    continue
                for node in j.get("@graph", [j]):
                    if node.get("@type") == "LocalBusiness" and node.get("address"):
                        a = node["address"]
                        geo = re.search(r'"latitude"\s*:\s*([\d.]+)\s*,\s*"longitude"\s*:\s*([\d.]+)', body)
                        name = H.unescape(node["name"])
                        rows.append({"id": sid, "b": name.split(" ")[0], "n": " ".join(name.split(" ")[1:]) or name,
                                     "a": a.get("addressRegion", "") + a.get("addressLocality", "") + a.get("streetAddress", ""),
                                     "lat": float(geo.group(1)) if geo else None, "lng": float(geo.group(2)) if geo else None})
                        break
                else:
                    continue
                break
        return rows
    return fetch


# ---------------------------------------------------------------------------
# トリドール（グループ店舗検索の検索結果JSON。県内の2地点から半径検索して重複を除く）
# ---------------------------------------------------------------------------
TORIDOLL_BRANDS = {"Marugame Seimen": "丸亀製麺", "Zundo-ya": "ずんどう屋", "Konas Coffee": "コナズ珈琲",
                   "Niku no Yama Gyu": "肉のヤマ牛"}


def toridoll():
    rows = {}
    for q in ("愛知県", "豊橋市"):
        offset, count = 0, 1
        while offset < count:
            j = get("https://stores.toridoll.com/searchapi", {"q": q, "r": 100, "per": 50, "offset": offset, "l": "ja"},
                    headers={"Accept": "application/json"})["response"]
            count = j["count"]
            for e in j["entities"]:
                p = e["profile"]
                if p.get("address", {}).get("region") != PREF:
                    continue
                a = p["address"]
                c = p.get("yextDisplayCoordinate") or p.get("displayCoordinate") or {}
                brand = TORIDOLL_BRANDS.get(p.get("c_cp_BrandName"), p.get("c_cp_BrandName") or "")
                rows[p["meta"]["id"]] = {"id": p["meta"]["id"], "b": brand, "n": p.get("geomodifier") or p["name"],
                                         "a": PREF + (a.get("city") or "") + (a.get("sublocality") or "") + (a.get("line1") or ""),
                                         "lat": c.get("lat"), "lng": c.get("long")}
            offset += 50
    return list(rows.values())


# ---------------------------------------------------------------------------
# クリエイト・レストランツ（レストランサーチのページに埋め込まれた全店舗データ。
# 「shareholder」が available の店舗だけを優待対象として収録）
# ---------------------------------------------------------------------------
def create_restaurants():
    import json as J
    import re
    page = get("https://www.create-restaurants.co.jp/", as_json=False)
    i = page.index("'rest_api_country_5':") + len("'rest_api_country_5':")
    shops, _ = J.JSONDecoder().raw_decode(page[i:])
    rows = []
    for x in shops:
        if x.get("pref_name") != PREF or x.get("shareholder") != "available" or str(x.get("status")) != "1":
            continue
        name = re.sub(r"[ 　]+", " ", x["name_jp"]).strip()
        brand = name.split(" ")[0]
        rows.append({"id": x["store_id"], "b": brand, "n": name[len(brand):].strip() or name,
                     "a": PREF + x.get("address1_jp", "") + re.sub(r"[ 　].*$", "", x.get("address2_jp", "")),
                     "lat": float(x["latitude"]) if x.get("latitude") else None,
                     "lng": float(x["longitude"]) if x.get("longitude") else None})
    return rows


# ---------------------------------------------------------------------------
# Canly の店舗検索サイト（ブランドごとのサイト）：/愛知県/ ページの埋め込みデータを読む
# ---------------------------------------------------------------------------
def canly_sites(sites):
    """sites: [(サイトのURL, ブランド名), ...]"""
    import json as J
    import re
    import urllib.parse as U

    def fetch():
        rows = []
        for base, brand in sites:
            page, maxp = 1, 1
            while page <= maxp:
                body = get(f"{base}/{U.quote(PREF)}/" + (f"?page={page}" if page > 1 else ""), as_json=False)
                pp = J.loads(re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S).group(1))["props"]["pageProps"]
                maxp = pp.get("maxPage") or 1
                for x in pp.get("shops", []):
                    name = x["nameKanji"]
                    rows.append({"id": x["id"], "b": brand, "n": name.replace(brand, "").strip() or name,
                                 "a": x.get("address", ""),
                                 "lat": float(x["latitude"]) if x.get("latitude") else None,
                                 "lng": float(x["longitude"]) if x.get("longitude") else None})
                page += 1
        return rows
    return fetch


# ---------------------------------------------------------------------------
# ホットランド（グループ店舗検索のAPI。座標が不正確なため住所から位置を求める）
# ---------------------------------------------------------------------------
def hotland():
    rows, page, total = [], 1, 1
    while page <= total:
        j = get("https://search.hotland.co.jp/api/search",
                {"limit": 100, "web_gyotai_id": "", "sub_gyotai_id": "", "prefecture_id": PREF_CODE,
                 "country_id": "", "keyword": "", "page": page})
        total = j.get("total_page") or 1
        for x in j["result"]:
            rows.append({"id": x["id"], "b": x.get("brand_name", ""), "n": x.get("shop_name", ""),
                         "a": (x.get("address") or "").replace(" ", "").replace("　", "")})
        page += 1
    return rows


# ---------------------------------------------------------------------------
# 光フードサービス（店舗検索ページのHTML。優待が使える「大黒」「大国」「魚椿」の店舗のみ）
# ---------------------------------------------------------------------------
def hikari_food():
    import html as H
    import re
    body = get("https://shop.hikari-food-service.jp/stores", {"prefecture": PREF_CODE}, as_json=False)
    rows = []
    for sec in re.findall(r'<section class="_stores__item[^"]*">(.*?)</section>', body, re.S):
        name = re.search(r"<h2[^>]*>(.*?)</h2>", sec, re.S)
        addr = re.search(r'<p class="_stores__address[^"]*">(.*?)</p>', sec, re.S)
        sid = re.search(r'href="/stores/(\d+)"', sec)
        if not (name and addr and sid):
            continue
        n = re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", "", name.group(1)))).strip()
        a = re.sub(r"\s+", "", H.unescape(re.sub(r"<[^>]+>", "", addr.group(1))))
        a = re.sub(r"〒?\d{3}-\d{4}", "", a)
        brand = next((b for b in ("大黒", "大国", "魚椿") if b in n), None)
        if brand and a.startswith(PREF):
            rows.append({"id": sid.group(1), "b": brand, "n": n, "a": a})
    return rows


# ---------------------------------------------------------------------------
# きちり（店舗検索ページの一覧 → 各店舗ページの住所）
# ---------------------------------------------------------------------------
KICHIRI_BRANDS = ["ハンバーグ＆ステーキ いしがま工房", "いしがまやハンバーグ", "とんかつとん久", "とん久", "VEGEGO", "KICHIRI", "KI・CHI・RI"]


def kichiri():
    import html as H
    import re
    body = get("https://kichiri.jp/search/", as_json=False)
    rows = []
    for li in re.findall(r"<li>\s*<a href=\"(https://kichiri\.jp/[^\"]+)\">(.*?)</a>\s*</li>", body, re.S):
        url, inner = li
        pref = re.search(r'class="txt">\s*([^<]+?)\s*<', inner)
        name = re.search(r'class="name">\s*([^<]+?)\s*<', inner)
        if not (pref and name and pref.group(1) == PREF):
            continue
        n = re.sub(r"\s+", " ", H.unescape(name.group(1))).strip()
        page = get(url, as_json=False)
        text = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        addr = re.search(r"(%s[^\s　]+)" % PREF, H.unescape(text))
        if not addr:
            continue
        brand = next((b for b in KICHIRI_BRANDS if n.startswith(b)), n.split(" ")[0])
        rows.append({"id": url.rstrip("/").split("/", 3)[-1], "b": brand, "n": n[len(brand):].strip() or n, "a": addr.group(1)})
    return rows


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
    {
        "code": "2753", "name": "あみやき亭", "brands": ["あみやき亭", "あみやき亭Plus", "美濃路", "みの路", "感動の肉と米", "ほるたん屋", "しゃぶ亭 ふふふ"],
        "url": "https://shop.amiyakitei.co.jp/all/",
        "fetch": canly(136),
    },
    {
        "code": "3063", "name": "ジェイグループホールディングス", "brands": ["ジェイグループの各店"],
        "url": "https://www.jgroup.jp/brand/",
        "note": "公式サイトで「株主優待券使用可」の店舗のみ",
        "fetch": jgroup,
    },
    {
        "code": "2752", "name": "フジオフードグループ本社", "brands": ["まいどおおきに食堂", "串家物語", "さち福や", "うちの食堂"],
        "url": "https://www.fujiofood.com/shop_search/",
        "fetch": fujio,
    },
    {
        "code": "3097", "name": "物語コーポレーション", "brands": ["焼肉きんぐ", "丸源ラーメン", "ゆず庵", "焼きたてのかるび", "お好み焼本舗"],
        "url": "https://www.monogatari.co.jp/brand/search/",
        "fetch": monogatari,
    },
    {
        "code": "3091", "name": "ブロンコビリー", "brands": ["ブロンコビリー"],
        "url": "https://www.bronco.co.jp/shop/",
        "fetch": bronco,
    },
    {
        "code": "2157", "name": "コシダカホールディングス", "brands": ["カラオケまねきねこ"],
        "url": "https://www.karaokemanekineko.jp/locations/",
        "fetch": manekineko,
    },
    {
        "code": "7458", "name": "第一興商", "brands": ["ビッグエコー"],
        "url": "https://shop.big-echo.jp/",
        "fetch": bigecho,
    },
    {
        "code": "3561", "name": "力の源ホールディングス", "brands": ["一風堂"],
        "url": "https://stores.ippudo.com/",
        "fetch": yext_pages("https://stores.ippudo.com"),
    },
    {
        "code": "3397", "name": "トリドールホールディングス", "brands": ["丸亀製麺", "ずんどう屋", "コナズ珈琲"],
        "url": "https://stores.toridoll.com/search",
        "note": "券売機を使う一部店舗では利用不可（愛知県内は該当なし）",
        "fetch": toridoll,
    },
    {
        "code": "3387", "name": "クリエイト・レストランツ・ホールディングス", "brands": ["磯丸水産", "しゃぶ菜", "菜菜麻辣湯"],
        "url": "https://www.create-restaurants.co.jp/",
        "note": "公式の店舗データで「株主優待利用可」の店舗のみ",
        "fetch": create_restaurants,
    },
    {
        "code": "9279", "name": "ギフトホールディングス", "brands": ["ラーメン豚山"],
        "url": "https://shop.butayama.com/",
        "note": "町田商店（設定で除外）以外はラーメン豚山のみ収録",
        "fetch": canly_sites([("https://shop.butayama.com", "ラーメン豚山")]),
    },
    {
        "code": "3196", "name": "ホットランドホールディングス", "brands": ["築地銀だこハイボール酒場", "昇家", "李昇", "銀のあん"],
        "url": "https://hotland.co.jp/store/",
        "fetch": hotland,
    },
    {
        "code": "138A", "name": "光フードサービス", "brands": ["大黒", "魚椿", "大国"],
        "url": "https://shop.hikari-food-service.jp/stores",
        "note": "優待が使える大黒・大国・魚椿のみ（金山家・焼肉デラックスは対象外）",
        "fetch": hikari_food,
    },
    {
        "code": "3547", "name": "ユニシアホールディングス", "brands": ["串カツ田中"],
        "url": "https://restaurant.kushi-tanaka.com/",
        "fetch": canly_sites([("https://restaurant.kushi-tanaka.com", "串カツ田中")]),
    },
    {
        "code": "3082", "name": "きちりホールディングス", "brands": ["いしがまやハンバーグ", "VEGEGO", "とん久"],
        "url": "https://kichiri.jp/search/",
        "fetch": kichiri,
    },
]

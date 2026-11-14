"""会社ごとの店舗取得処理。

1社 = SOURCES の1要素。fetch(pr) は都道府県 pr（prefs.Pref）の店舗を、次のキーを持つdictのリストで返す:
  id  : その会社の中で一意な店舗ID
  b   : ブランド名（例: はなまるうどん）
  n   : 店舗名
  a   : 住所（都道府県から）
  lat, lng : 緯度経度（取れなければ省略。国土地理院APIで補完される）
  x   : 注意書き（任意。例: 優待利用可否が不明）
"""
import functools

from common import get


def once(fn):
    """全国分をまとめて返す取得処理を、1回の実行中は使い回す（複数県を収集するとき用）"""
    return functools.lru_cache(maxsize=None)(fn)


# ---------------------------------------------------------------------------
# NAVITIME 店舗検索サービス（吉野家など多くのチェーンが利用）
# ---------------------------------------------------------------------------
def navitime(base, brand_of=None, extra=None):
    """base 例: https://stores.yoshinoya.com/yoshinoya"""
    def fetch(pr):
        rows, offset, total = [], 0, 1
        while offset < total:
            params = {"datum": "wgs84", "limit": 50, "offset": offset, "address": pr.code2, "sort": "admin_jis"}
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
def komeda(pr):
    api = "https://eu.komeda.co.jp/v1/hp/shop"
    # 8=コメダ珈琲店（海外）は対象外。ブランド名はサイトのメタデータから取る
    meta = get("https://eu.komeda.co.jp/v1/hp/shop-meta-data")
    bt = next(f for f in meta["fields"] if f["name"] == "brand_type")
    brands = {int(v["id"]): v["display_name"] for v in bt["values"] if v["id"] != "8"}
    rows = []
    for bt, bname in brands.items():
        lst = get(api, {"brand_type": bt, "all": "true", "prefecture": pr.name})["items"]
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
def grid_boxes(bounds, step=0.25):
    """県の範囲を約0.25度（約25km）四方の格子に分ける"""
    s, w, n, e = bounds
    ny, nx = max(1, round((n - s) / step)), max(1, round((e - w) / step))
    for i in range(ny):
        for k in range(nx):
            yield (s + (n - s) * i / ny, w + (e - w) * k / nx, s + (n - s) * (i + 1) / ny, w + (e - w) * (k + 1) / nx)


def mcdonalds(pr):
    rows = {}
    for b in grid_boxes(pr.bounds, 0.15):
        for p in get("https://map.mcdonalds.co.jp/api/poi", {"bounds": ",".join(f"{v:.5f}" for v in b)}):
            rows[p["key"]] = {"id": p["key"], "b": "マクドナルド", "n": p["name"], "a": p["address"],
                              "lat": p["latitude"], "lng": p["longitude"]}
    return list(rows.values())


# ---------------------------------------------------------------------------
# Canly（店舗ページCMS。あみやき亭グループなどが利用）: 全国分が1リクエストで返る
# ---------------------------------------------------------------------------
def canly(directory_id):
    @once
    def fetch_all():
        j = get(f"https://api.site.can-ly.com/v2/directories/{directory_id}/shops/search", {"sort": "area:asc"})
        return [{"id": s["id"], "b": (s.get("brand") or {}).get("name", ""), "n": s["nameKanji"],
                 "a": s.get("address", ""), "lat": float(s["latitude"]) if s.get("latitude") else None,
                 "lng": float(s["longitude"]) if s.get("longitude") else None}
                for s in j["shops"]]

    def fetch(pr):
        return [r for r in fetch_all() if r["a"].startswith(pr.name)]
    return fetch


# ---------------------------------------------------------------------------
# ジェイグループ（自社サイト。「株主優待券使用可」で絞り込んだ一覧をページ送りで取得）
# ---------------------------------------------------------------------------
@once
def jgroup_area_terms():
    """店舗検索フォームの「エリア」から 都道府県名→番号 の対応を作る"""
    import html as H
    import re
    body = get("https://www.jgroup.jp/brand/", as_json=False)
    terms = {}
    for value, label in re.findall(r'name="store_area_term\[\]"\s+value="(\d+)"\s*>\s*<span>([^<]+)</span>', body):
        terms.setdefault(H.unescape(label).strip(), value)
    return terms


def jgroup(pr):
    import html as H
    import re
    term = jgroup_area_terms().get(pr.name) or jgroup_area_terms().get(pr.short)
    if not term:
        return []  # その県に店舗がない
    rows, page = [], 1
    while True:
        base = "https://www.jgroup.jp/brand/" + (f"page/{page}/" if page > 1 else "")
        body = get(base, {"store_area_term[]": term, "store_service[]": "株主優待券使用可"}, as_json=False)
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
                a = pr.name + a  # 県名が省略されている住所（県で絞り込み済み）
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


def fujio(pr):
    import html as H
    import re
    body = get("https://www.fujiofood.com/shop_search/index.php", {"prefName": pr.short, "brandName": ""}, as_json=False)
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
        if not a.startswith(pr.name):
            a = pr.name + a
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


@once
def monogatari_all():
    api = "https://shop.monogatari.co.jp/api/v1/shops/"
    rows, page, last = [], 1, 1
    while page <= last:
        j = get(api, {"page": page})
        last = j.get("last_page", 1)
        for s in j["data"]:
            if s.get("not_public_flag"):
                continue
            rows.append({"pid": s.get("prefecture_id"), "id": s["shop_code"], "b": MONOGATARI_BRANDS.get(s["brand_id"], f"ブランド{s['brand_id']}"),
                         "n": s["name"], "a": s.get("address", ""), "lat": s.get("latitude"), "lng": s.get("longitude")})
        page += 1
    return rows


def monogatari(pr):
    return [{k: v for k, v in r.items() if k != "pid"} for r in monogatari_all() if r["pid"] == pr.code]


# ---------------------------------------------------------------------------
# ブロンコビリー（店舗一覧ページ → 各店舗ページの住所・地図座標）
# ---------------------------------------------------------------------------
BRONCO_EXTRA_AREAS = {"愛知県": ["名古屋"], "大阪府": ["大阪市"]}  # 県名を含まない地域見出し


def bronco(pr):
    import html as H
    import re
    top = get("https://www.bronco.co.jp/shop/", as_json=False)
    urls = []
    keys = [pr.name] + BRONCO_EXTRA_AREAS.get(pr.name, [])  # 「愛知県郊外」「名古屋」など
    for title, block in re.findall(r'<li class="shop_contents_list_item[^"]*" data-id="[^"]*">\s*<h3[^>]*>(.*?)</h3>(.*?)</ul>', top, re.S):
        title = re.sub(r"<[^>]+>", "", title)
        if any(k in title for k in keys):
            urls += re.findall(r'href="(https://www\.bronco\.co\.jp/shop/[^"]+)"', block)
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
@once
def manekineko_slugs():
    """店舗一覧ページの都道府県リンク（例：/locations/aichi/）から 県名→英字 を作る"""
    import re
    body = get("https://www.karaokemanekineko.jp/locations/", as_json=False)
    return {name: slug for slug, name in re.findall(r'href="[^"]*/locations/([a-z-]+)/"[^>]*>\s*(北海道|[^<\s(（]+?[都府県])', body)}


def manekineko(pr):
    import html as H
    import json as J
    import re
    import urllib.parse
    import urllib.request
    from common import UA
    slug = manekineko_slugs().get(pr.name) or pr.romaji
    try:
        page = get(f"https://www.karaokemanekineko.jp/locations/{slug}/", as_json=False, retries=0)
    except RuntimeError as e:
        if "404" in str(e):
            return []  # その県に店舗がない
        raise
    cfg = J.loads(re.search(r"LOC_AJAX\s*=\s*(\{[^;]+\})", page).group(1))
    data = urllib.parse.urlencode({"action": "loc_load_more", "nonce": cfg["nonce"], "pref": slug, "city": "",
                                   "q": "", "mode": "", "lat": "", "lng": "", "page": 1, "per_page": 200}).encode()
    req = urllib.request.Request(cfg["ajax_url"], data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        j = J.loads(r.read().decode("utf-8"))
    body = j["data"]["html"]
    rows = []
    for unit in re.findall(r'<div class="content__unit js-store".*?(?=<div class="content__unit js-store"|$)', body, re.S):
        br = re.search(r'content__unit-ttl__branch">(.*?)<', unit, re.S)
        ad = re.search(r'content__unit-address">(.*?)<', unit, re.S)
        href = re.search(r'href="([^"]*/locations/%s/[^"]+)"' % slug, unit)
        if br and ad:
            rows.append({"id": (href.group(1) if href else br.group(1)).rstrip("/").split("/")[-1],
                         "b": "カラオケまねきねこ", "n": H.unescape(br.group(1)).strip(),
                         "a": re.sub(r"\s+", "", H.unescape(ad.group(1)))})
    return rows


# ---------------------------------------------------------------------------
# 第一興商：ビッグエコー（地図範囲指定のAPI。マクドナルドと同じ方式）
# ---------------------------------------------------------------------------
def bigecho(pr):
    rows = {}
    for b in grid_boxes(pr.bounds, 0.3):
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

    def fetch(pr):
        PREF = pr.name
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


TORIDOLL_EXTRA_QUERIES = {"愛知県": ["豊橋市"], "北海道": ["旭川市", "函館市", "釧路市"]}  # 県の端までカバーする追加地点


def toridoll(pr):
    PREF = pr.name
    rows = {}
    for q in [pr.name] + TORIDOLL_EXTRA_QUERIES.get(pr.name, []):
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
@once
def create_restaurants_all():
    import json as J
    page = get("https://www.create-restaurants.co.jp/", as_json=False)
    i = page.index("'rest_api_country_5':") + len("'rest_api_country_5':")
    shops, _ = J.JSONDecoder().raw_decode(page[i:])
    return shops


def create_restaurants(pr):
    """株主優待のページにある「優待利用可能店舗の一覧」PDFを正とする。
    座標は公式サイトの店舗データ（電話番号で照合）から取り、なければ住所から求める。"""
    import hashlib
    import re
    tel_geo = {}
    for x in create_restaurants_all():
        tel = re.sub(r"\D", "", x.get("tel_number") or "")
        if tel and x.get("latitude"):
            tel_geo[tel] = (float(x["latitude"]), float(x["longitude"]))
    rows = []
    for r in create_restaurants_pdf():
        if r["pref"] != pr.name:
            continue
        ll = tel_geo.get(re.sub(r"\D", "", r["tel"]))
        key = hashlib.md5((r["b"] + "|" + r["n"] + "|" + r["a"]).encode("utf-8")).hexdigest()[:10]
        row = {"id": "pdf-" + key,
               "b": r["b"], "n": r["n"], "a": pr.name + r["a"].split(" ")[0],
               "lat": ll[0] if ll else None, "lng": ll[1] if ll else None}
        # 注記で一部コーナーが対象外とされている店舗
        if re.search(r"上郷SA|長篠設楽原PA|遠州森町PA|賤ヶ岳SA|龍野西SA|三芳PA|牧之原SA|神田PA|関SA", r["a"] + r["b"]):
            row["x"] = "一部コーナーは優待対象外"
        rows.append(row)
    return rows


@once
def create_restaurants_pdf():
    """優待利用可能店舗一覧PDFを読み、[{pref, b, n, a, tel}] を返す（全国分）"""
    import json as J
    import re
    import subprocess
    import tempfile
    import urllib.request
    from pathlib import Path
    from common import UA
    from prefs import PREFS
    page = get("https://www.createrestaurants.com/ir/stock/shareholder/", as_json=False)
    m = (re.search(r'href="([^"]+\.pdf)"[^>]*>(?:\s*<[^>]+>)*\s*優待利用可能店舗', page)
         or re.search(r'href="([^"]*/wp-content/uploads/[^"]*%E5%BA%97%E8%88%97%E4%B8%80%E8%A6%A7[^"]*\.pdf)"', page))
    if not m:
        raise RuntimeError("優待利用可能店舗一覧のPDFが見つかりません")
    import html
    import urllib.parse
    url = html.unescape(m.group(1))
    url = url if url.startswith("http") else "https://www.createrestaurants.com" + url
    url = urllib.parse.quote(url, safe=":/%?=&")  # ファイル名の日本語をURLエンコード
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as r:
            f.write(r.read())
        pdf_path = f.name
    here = Path(__file__).resolve().parent
    out_path = pdf_path + ".json"
    r = subprocess.run(["node", str(here / "pdf_items.mjs"), pdf_path, out_path], capture_output=True, text=True, cwd=here)
    if r.returncode != 0 or not Path(out_path).exists():
        raise RuntimeError(f"PDFの読み取りに失敗: {(r.stderr or r.stdout)[-300:]}")
    items = J.loads(Path(out_path).read_text(encoding="utf-8"))
    print(f"[info] クリエイト・レストランツ PDF: {Path(pdf_path).stat().st_size}バイト・文字要素{len(items)}個")
    # 同じ行（y座標の差2以内）をまとめ、x座標で 店名(〜230) / 住所(230〜630) / 電話(630〜690) に分ける
    lines = []
    for it in sorted(items, key=lambda i: (i["p"], -i["y"], i["x"])):
        if lines and lines[-1]["p"] == it["p"] and abs(lines[-1]["y"] - it["y"]) <= 2:
            lines[-1]["its"].append(it)
        else:
            lines.append({"p": it["p"], "y": it["y"], "its": [it]})
    rows, pref = [], None
    for ln in lines:
        its = sorted(ln["its"], key=lambda i: i["x"])
        text = "".join(i["s"].strip() for i in its)
        if len(its) == 1 and its[0]["x"] < 40:  # 見出し（都道府県名。それ以外の見出しでは収録しない）
            pref = text if text in PREFS else None
            continue
        if not pref:
            continue
        name = [i["s"].strip() for i in its if i["x"] < 230]
        addr = " ".join(i["s"].strip() for i in its if 230 <= i["x"] < 630)
        tel = "".join(i["s"].strip() for i in its if 630 <= i["x"] < 690)
        if name and addr:
            rows.append({"pref": pref, "b": name[0], "n": " ".join(name[1:]) or name[0], "a": addr, "tel": tel})
    if len(rows) < 100:
        raise RuntimeError(f"PDFの読み取り件数が少なすぎます（{len(rows)}件）")
    print(f"[info] クリエイト・レストランツ 優待店舗PDF: {url.rsplit('/', 1)[-1]} から {len(rows)}件")
    return rows


# ---------------------------------------------------------------------------
# Canly の店舗検索サイト（ブランドごとのサイト）：/愛知県/ ページの埋め込みデータを読む
# ---------------------------------------------------------------------------
def canly_sites(sites):
    """sites: [(サイトのURL, ブランド名), ...]"""
    import json as J
    import re
    import urllib.parse as U

    def fetch(pr):
        rows = []
        for base, brand in sites:
            page, maxp = 1, 1
            while page <= maxp:
                try:
                    body = get(f"{base}/{U.quote(pr.name)}/" + (f"?page={page}" if page > 1 else ""), as_json=False, retries=0)
                except RuntimeError as e:
                    if "404" in str(e):
                        break  # その県に店舗がない
                    raise
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
def hotland(pr):
    rows, page, total = [], 1, 1
    while page <= total:
        j = get("https://search.hotland.co.jp/api/search",
                {"limit": 100, "web_gyotai_id": "", "sub_gyotai_id": "", "prefecture_id": pr.code2,
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
def hikari_food(pr):
    import html as H
    import re
    body = get("https://shop.hikari-food-service.jp/stores", {"prefecture": pr.code2}, as_json=False)
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
        if brand and a.startswith(pr.name):
            rows.append({"id": sid.group(1), "b": brand, "n": n, "a": a})
    return rows


# ---------------------------------------------------------------------------
# きちり（店舗検索ページの一覧 → 各店舗ページの住所）
# ---------------------------------------------------------------------------
@once
def kichiri_page():
    return get("https://kichiri.jp/search/", as_json=False)


KICHIRI_BRANDS = ["ハンバーグ＆ステーキ いしがま工房", "いしがまやハンバーグ", "とんかつとん久", "とん久", "VEGEGO", "KICHIRI", "KI・CHI・RI"]


def kichiri(pr):
    import html as H
    import re
    PREF = pr.name
    body = kichiri_page()
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
# FOOD & LIFE：鮨 酒 肴 杉玉（公式の店舗一覧ページ）
# ---------------------------------------------------------------------------
@once
def sugidama_page():
    return get("https://www.sugidama-sushiizakaya.jp/shop/", as_json=False)


def sugidama(pr):
    import html as H
    import re
    PREF = pr.name
    body = sugidama_page()
    rows = {}
    for sid, name, addr in re.findall(r'href="/shop/(\d+)/" class="s_name">(.*?)</a>.*?class="s_address">(.*?)</p>', body, re.S):
        a = re.sub(r"\s+", " ", H.unescape(addr)).strip()
        if a.startswith(PREF):
            n = H.unescape(name).strip()
            rows[sid] = {"id": sid, "b": "杉玉", "n": n.replace("鮨 酒 肴 杉玉", "").strip() or n, "a": a.split(" ")[0]}
    return list(rows.values())


# ---------------------------------------------------------------------------
# テンアライド（公式の店舗検索：県ページ → 市区町村ページの店舗一覧）
# ---------------------------------------------------------------------------
TENG_BRANDS = ["旬鮮酒場天狗", "テング酒場", "和食れすとらん天狗", "ミートキッチンlog50", "神田屋", "てんぐ大ホール",
               "湊や磯吉食堂", "湊や磯吉", "磯吉食堂"]


def teng(pr):
    import html as H
    import re
    base = "https://www.teng.co.jp"
    try:
        top = get(f"{base}/shopsearch/shops/{pr.romaji}/", as_json=False, retries=0)
    except RuntimeError as e:
        if "404" in str(e):
            return []  # その県に店舗がない
        raise
    cities = sorted(set(re.findall(r'href="(/shopsearch/shops/%s/[^"/]+/)"' % pr.romaji, top)))
    rows = {}
    for c in cities:
        body = get(base + c, as_json=False)
        for box in re.findall(r'<div class="list-shop-box">(.*?)<div class="list-icons">', body, re.S):
            link = re.search(r'<a href="(/shopsearch/shops/[^"]+\.html)">(.*?)</a>', box, re.S)
            addr = re.search(r'<div class="list-adress">(.*?)</div>', box, re.S)
            if not (link and addr):
                continue
            name = re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", "", link.group(2)))).strip()
            a = H.unescape(re.split(r"<br\s*/?>", addr.group(1))[0]).strip()
            brand = next((b for b in TENG_BRANDS if name.startswith(b)), name.split(" ")[0])
            rows[link.group(1)] = {"id": link.group(1).rsplit("/", 1)[-1].replace(".html", ""), "b": brand,
                                   "n": name[len(brand):].strip(" ・") or name, "a": re.sub(r"\s+", "", a)}
    return list(rows.values())


# ---------------------------------------------------------------------------
# コシダカ：カラオケショップJOYSOUND（地方別の店舗一覧）・カラオケGLANZA（店舗一覧）
# ---------------------------------------------------------------------------
@once
def joysound_all():
    import html as H
    import re
    rows = []
    for area in ("hokkaido-tohoku", "kanto", "koshinetsu-hokuriku", "tokai", "kansai", "chugoku", "shikoku", "kyushu-okinawa"):
        body = get(f"https://shop.joysound.com/area/{area}/", as_json=False)
        for dl in re.findall(r"<dl>(.*?)</dl>", body, re.S):
            link = re.search(r'class="shopList-link" href="https://shop\.joysound\.com/shop/([^/"]+)/?">(.*?)</a>', dl, re.S)
            addr = re.search(r'<dd[^>]*>\s*<p>(.*?)</p>', dl, re.S)
            if not (link and addr):
                continue
            name = H.unescape(re.sub(r"<[^>]+>", "", link.group(2))).strip()
            if not name.startswith("JOYSOUND"):
                continue  # ゆう遊空間など、優待対象外の業態
            a = re.sub(r"\s+", " ", H.unescape(addr.group(1)).replace("\xa0", " ")).strip()
            rows.append({"id": "js-" + link.group(1), "b": "カラオケJOYSOUND",
                         "n": name.replace("JOYSOUND", "").strip() or name, "a": a.split(" ")[0]})
    return rows


@once
def glanza_all():
    import html as H
    import re
    body = get("https://glanza-k.com/shop/", as_json=False)
    rows = []
    for href, name, addr in re.findall(r'<h3 class="card-shop__name"[^>]*><a href="(/shop/[^"]+)">(.*?)</a></h3>.*?'
                                       r'<address class="card-shop__address"[^>]*>\s*<p class="card-shop__text">(.*?)</p>', body, re.S):
        n = H.unescape(name).strip()
        rows.append({"id": "gz-" + href.strip("/").replace("/", "-"), "b": "カラオケGLANZA",
                     "n": n.replace("カラオケ GLANZA", "").strip() or n, "a": H.unescape(addr).split(" ")[0].split("　")[0]})
    return rows


def koshidaka(pr):
    rows = manekineko(pr)
    rows += [r for r in joysound_all() if r["a"].startswith(pr.name)]
    rows += [r for r in glanza_all() if r["a"].startswith(pr.name)]
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
        "code": "2157", "name": "コシダカホールディングス", "brands": ["カラオケまねきねこ", "カラオケJOYSOUND", "カラオケGLANZA"],
        "url": "https://www.karaokemanekineko.jp/locations/",
        "fetch": koshidaka,
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
        "note": "公式の「優待利用可能店舗の一覧」（PDF）に載っている店舗",
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
    {
        "code": "3563", "name": "FOOD & LIFE COMPANIES", "brands": ["杉玉"],
        "url": "https://www.sugidama-sushiizakaya.jp/shop/",
        "note": "杉玉のみ収録（京樽は未収録）",
        "fetch": sugidama,
    },
    {
        "code": "8207", "name": "テンアライド", "brands": ["テング酒場", "旬鮮酒場天狗", "神田屋", "湊や磯吉食堂"],
        "url": "https://www.teng.co.jp/shopsearch/",
        "fetch": teng,
    },
]

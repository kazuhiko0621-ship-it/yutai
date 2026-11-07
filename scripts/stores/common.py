"""収集処理の共通部品（HTTP取得・住所→座標変換）"""
import json
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; yutai-store-map/1.0; personal use)"
WAIT = 0.5  # 1リクエストごとの待ち時間（相手サイトへの負荷を抑える）


def get(url, params=None, headers=None, as_json=True, retries=2):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = {"User-Agent": UA, "Accept-Language": "ja"}
    if headers:
        h.update(headers)
    last = None
    for i in range(retries + 1):
        try:
            time.sleep(WAIT)
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
                body = r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
            return json.loads(body) if as_json else body
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"取得失敗 {url}: {last}")


def geocode(address):
    """国土地理院の住所検索API。見つからなければ None。"""
    q = address.replace("　", " ").split(" ")[0]
    try:
        res = get("https://msearch.gsi.go.jp/address-search/AddressSearch", {"q": q})
    except Exception:  # noqa: BLE001
        return None
    if not res:
        return None
    lon, lat = res[0]["geometry"]["coordinates"]
    return round(lat, 6), round(lon, 6)


def _supabase(index_html):
    import re
    html = index_html.read_text(encoding="utf-8")
    url = re.search(r'SUPABASE_URL\s*=\s*"([^"]+)"', html).group(1)
    key = re.search(r'SUPABASE_ANON_KEY\s*=\s*"([^"]+)"', html).group(1)
    return url, {"apikey": key, "Authorization": f"Bearer {key}"}


def load_areas(index_html):
    """店舗マップの対象都道府県（yutai_store_areas）を {県名: 定期更新するか} で返す。読めなければ None。"""
    try:
        url, headers = _supabase(index_html)
        rows = get(f"{url}/rest/v1/yutai_store_areas", {"select": "pref,auto_update"}, headers=headers)
        return {r["pref"]: bool(r["auto_update"]) for r in rows}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 対象の都道府県を読めませんでした: {e}")
        return None


def load_scopes(index_html):
    """優待手帳の Supabase から店舗マップの取得範囲（yutai_store_scope）を読む。
    接続先は index.html に書かれた URL・公開キーを使う。読めなければ空（＝全て扱い）。"""
    import re
    try:
        html = index_html.read_text(encoding="utf-8")
        url = re.search(r'SUPABASE_URL\s*=\s*"([^"]+)"', html).group(1)
        key = re.search(r'SUPABASE_ANON_KEY\s*=\s*"([^"]+)"', html).group(1)
        rows = get(f"{url}/rest/v1/yutai_store_scope", {"select": "code,scope,exclude_brands"},
                   headers={"apikey": key, "Authorization": f"Bearer {key}"})
        return {str(r["code"]).upper(): r for r in rows}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 取得範囲を読めませんでした（全て扱い）: {e}")
        return {}

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

import os, io, time, html, hashlib, httpx
from PIL import Image

E = os.environ.get
BASE = "https://api.ebay.com"
SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
])
_tok = {"v": None, "exp": 0}


def token():
    if _tok["v"] and time.time() < _tok["exp"] - 60:
        return _tok["v"]
    r = httpx.post(
        f"{BASE}/identity/v1/oauth2/token",
        auth=(E("EBAY_CLIENT_ID", ""), E("EBAY_CLIENT_SECRET", "")),
        data={"grant_type": "refresh_token", "refresh_token": E("EBAY_REFRESH_TOKEN", "")},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    _tok["v"], _tok["exp"] = j["access_token"], time.time() + j["expires_in"]
    return _tok["v"]


def _h(extra=None):
    h = {"Authorization": f"Bearer {token()}", "Content-Language": E("EBAY_LANG", "en-GB"),
         "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def find_cover(artist, title):
    """MusicBrainz -> Cover Art Archive. Returns image bytes or None."""
    try:
        q = f'artist:"{artist}" AND release:"{title}"'
        r = httpx.get("https://musicbrainz.org/ws/2/release/", params={"query": q, "fmt": "json", "limit": 3},
                      headers={"User-Agent": "cd-lister/1.0 (personal use)"}, timeout=20)
        for rel in r.json().get("releases", []):
            img = httpx.get(f"https://coverartarchive.org/release/{rel['id']}/front-500",
                            follow_redirects=True, timeout=30)
            if img.status_code == 200:
                return img.content
    except Exception:
        pass
    return None


def upload_image(data: bytes) -> str:
    """Upload bytes to eBay's Media API, return hosted image URL."""
    r = httpx.post(
        "https://apim.ebay.com/commerce/media/v1_beta/image/create_image_from_file",
        headers={"Authorization": f"Bearer {token()}"},
        files={"image": ("cover.jpg", data, "image/jpeg")}, timeout=60,
    )
    r.raise_for_status()
    try:
        return r.json()["imageUrl"]
    except Exception:
        g = httpx.get(r.headers["Location"], headers={"Authorization": f"Bearer {token()}"}, timeout=30)
        return g.json()["imageUrl"]


DATA_DIR = os.path.dirname(E("DB_PATH", "/data/cds.db")) or "."
COND = {
    "LIKE_NEW": ("Like New", "Near-perfect disc and case with minimal signs of use."),
    "USED_VERY_GOOD": ("Very Good", "Light signs of use only. Case and booklet may show minor wear."),
    "USED_GOOD": ("Good", "Visible signs of use such as light surface marks. Case or booklet may show wear."),
    "USED_ACCEPTABLE": ("Acceptable", "Noticeable wear. Please check the photos carefully."),
}


def logo_url():
    """Optional: drop logo.png/.jpg in the data folder. Uploaded to eBay once, then cached."""
    for n in ("logo.png", "logo.jpg", "logo.jpeg"):
        p = os.path.join(DATA_DIR, n)
        if os.path.exists(p):
            raw = open(p, "rb").read()
            h = hashlib.sha256(raw).hexdigest()[:16]
            cache = os.path.join(DATA_DIR, "logo_url.txt")
            if os.path.exists(cache):
                ch, url = open(cache).read().split(" ", 1)
                if ch == h:
                    return url.strip()
            im = Image.open(io.BytesIO(raw))
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
            bg.thumbnail((400, 160))
            out = io.BytesIO()
            bg.save(out, "JPEG", quality=90)
            url = upload_image(out.getvalue())
            open(cache, "w").write(f"{h} {url}")
            return url
    return None


def build_description(it, logo):
    x = html.escape
    accent = E("SHOP_COLOR", "#2a5bd7")
    shop = x(E("SHOP_NAME", "Our Shop"))
    ship = x(E("SHIPPING_TEXT", "Dispatched promptly from the UK and carefully packed in protective packaging."))
    ret = x(E("RETURNS_TEXT", "Not as described? Get in touch and we will put it right."))
    cname, cdesc = COND.get(it["condition"], COND["USED_VERY_GOOD"])
    rows = [("Artist", it["artist"]), ("Title", it["title"]), ("Format", "CD"), ("Year", it.get("year")),
            ("Label", it.get("label")), ("Catalogue No.", it.get("catno")), ("Genre", it.get("genre"))]
    rows = "".join(
        f'<tr><td style="padding:10px 14px;color:#777;width:38%;border-bottom:1px solid #eee">{l}</td>'
        f'<td style="padding:10px 14px;font-weight:600;border-bottom:1px solid #eee">{x(str(v))}</td></tr>'
        for l, v in rows if v)
    head = (f'<img src="{logo}" alt="{shop}" style="max-height:60px;max-width:220px;vertical-align:middle">' if logo
            else f'<span style="font-size:22px;font-weight:700;letter-spacing:.5px;color:#fff">{shop}</span>')
    notes = (f'<div style="margin:18px 0 0;padding:14px 16px;background:#fffbe6;border-left:4px solid #f5c518;'
             f'border-radius:6px;font-size:14px"><b>Seller notes:</b> {x(it["notes"])}</div>' if it.get("notes") else "")
    box = ('<td style="width:33.33%;padding:6px;vertical-align:top"><div style="background:#f6f7f9;border-radius:10px;'
           'padding:16px;height:100%"><div style="font-weight:700;margin-bottom:6px;color:#111">{t}</div>'
           '<div style="font-size:13px;color:#555;line-height:1.5">{b}</div></div></td>')
    return f"""<div style="max-width:820px;margin:0 auto;font-family:Helvetica,Arial,sans-serif;color:#222;line-height:1.5">
<div style="background:#111;padding:18px 24px;border-radius:12px 12px 0 0;text-align:center">{head}</div>
<div style="border:1px solid #e5e5e5;border-top:0;border-radius:0 0 12px 12px;padding:28px 24px">
<div style="text-align:center;margin-bottom:22px">
<div style="font-size:13px;letter-spacing:2px;text-transform:uppercase;color:{accent};font-weight:700">{x(it['artist'])}</div>
<h1 style="font-size:28px;margin:6px 0 10px;color:#111">{x(it['title'])}</h1>
<span style="display:inline-block;background:{accent};color:#fff;padding:5px 14px;border-radius:20px;font-size:13px;font-weight:600">CD &bull; {cname}</span>
</div>
<table style="width:100%;border-collapse:collapse;border:1px solid #eee;border-radius:8px;font-size:15px">{rows}</table>
<h2 style="font-size:17px;margin:26px 0 8px;color:#111">Condition</h2>
<p style="margin:0;font-size:14px;color:#444"><b>{cname}.</b> {cdesc} Please see the photos for the exact item you will receive.</p>
{notes}
<table style="width:100%;border-collapse:collapse;margin-top:26px"><tr>
{box.format(t="Postage", b=ship)}
{box.format(t="Secure packing", b="Packed to arrive in the condition described.")}
{box.format(t="Returns", b=ret)}
</tr></table>
<p style="text-align:center;font-size:12px;color:#999;margin:24px 0 0">Thanks for shopping with {shop}. Check out my other listings for more CDs &mdash; combined postage may be available.</p>
</div></div>"""


def make_title(it):
    t = f"{it['artist']} - {it['title']}"
    if it.get("year"):
        t += f" ({it['year']})"
    t += " CD"
    return t[:80]


def push(it, image_urls, publish):
    sku = it["sku"]
    try:
        logo = logo_url()
    except Exception:
        logo = None
    desc = build_description(it, logo)
    aspects = {"Artist": [it["artist"]], "Release Title": [it["title"]], "Format": ["CD"]}
    if it.get("genre"): aspects["Genre"] = [it["genre"]]
    if it.get("label"): aspects["Record Label"] = [it["label"]]
    item = {"availability": {"shipToLocationAvailability": {"quantity": 1}}, "condition": it["condition"],
            "product": {"title": make_title(it), "description": desc, "aspects": aspects, "imageUrls": image_urls}}
    r = httpx.put(f"{BASE}/sell/inventory/v1/inventory_item/{sku}", headers=_h(), json=item, timeout=30)
    if r.status_code >= 300: raise RuntimeError(f"inventory_item: {r.text}")

    offer = {"sku": sku, "marketplaceId": E("EBAY_MARKETPLACE", "EBAY_GB"), "format": "FIXED_PRICE",
             "availableQuantity": 1, "categoryId": "176984", "listingDescription": desc,
             "merchantLocationKey": E("EBAY_LOCATION_KEY"),
             "listingPolicies": {"fulfillmentPolicyId": E("EBAY_FULFILLMENT_POLICY"),
                                 "paymentPolicyId": E("EBAY_PAYMENT_POLICY"),
                                 "returnPolicyId": E("EBAY_RETURN_POLICY")},
             "pricingSummary": {"price": {"value": f"{it['price']:.2f}", "currency": E("EBAY_CURRENCY", "GBP")}}}
    mp = E("EBAY_MARKETPLACE", "EBAY_GB")
    r = httpx.get(f"{BASE}/sell/inventory/v1/offer", headers=_h(), params={"sku": sku, "marketplace_id": mp}, timeout=30)
    existing = r.json().get("offers", []) if r.status_code == 200 else []
    if existing:  # retry: update the offer already on eBay instead of creating a duplicate
        offer_id = existing[0]["offerId"]
        r = httpx.put(f"{BASE}/sell/inventory/v1/offer/{offer_id}", headers=_h(), json=offer, timeout=30)
        if r.status_code >= 300: raise RuntimeError(f"update offer: {r.text}")
    else:
        r = httpx.post(f"{BASE}/sell/inventory/v1/offer", headers=_h(), json=offer, timeout=30)
        if r.status_code >= 300: raise RuntimeError(f"offer: {r.text}")
        offer_id = r.json()["offerId"]
    listing_id = None
    if publish:
        r = httpx.post(f"{BASE}/sell/inventory/v1/offer/{offer_id}/publish", headers=_h(), timeout=30)
        if r.status_code >= 300: raise RuntimeError(f"publish: {r.text}")
        listing_id = r.json().get("listingId")
    return offer_id, listing_id

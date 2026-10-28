import os, time, httpx

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
        data={"grant_type": "refresh_token", "refresh_token": E("EBAY_REFRESH_TOKEN", ""), "scope": SCOPES},
        timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"eBay OAuth token request ({r.status_code}): {r.text[:500]}")
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


def make_title(it):
    t = f"{it['artist']} - {it['title']}"
    if it.get("year"):
        t += f" ({it['year']})"
    t += " CD"
    return t[:80]


def push(it, image_url, publish):
    sku = it["sku"]
    desc = (f"<p><b>{it['artist']} - {it['title']}</b></p>"
            f"<p>Format: CD<br>{('Label: ' + it['label'] + '<br>') if it.get('label') else ''}"
            f"{('Cat No: ' + it['catno'] + '<br>') if it.get('catno') else ''}"
            f"{('Year: ' + str(it['year']) + '<br>') if it.get('year') else ''}</p>"
            f"<p>{it.get('notes') or 'Disc and case in good condition. Tested and plays well.'}</p>")
    aspects = {"Artist": [it["artist"]], "Release Title": [it["title"]], "Format": ["CD"]}
    if it.get("genre"): aspects["Genre"] = [it["genre"]]
    if it.get("label"): aspects["Record Label"] = [it["label"]]
    item = {"availability": {"shipToLocationAvailability": {"quantity": 1}}, "condition": it["condition"],
            "product": {"title": make_title(it), "description": desc, "aspects": aspects, "imageUrls": [image_url]}}
    r = httpx.put(f"{BASE}/sell/inventory/v1/inventory_item/{sku}", headers=_h(), json=item, timeout=30)
    if r.status_code >= 300: raise RuntimeError(f"inventory_item: {r.text}")

    offer = {"sku": sku, "marketplaceId": E("EBAY_MARKETPLACE", "EBAY_GB"), "format": "FIXED_PRICE",
             "availableQuantity": 1, "categoryId": "176984", "listingDescription": desc,
             "merchantLocationKey": E("EBAY_LOCATION_KEY"),
             "listingPolicies": {"fulfillmentPolicyId": E("EBAY_FULFILLMENT_POLICY"),
                                 "paymentPolicyId": E("EBAY_PAYMENT_POLICY"),
                                 "returnPolicyId": E("EBAY_RETURN_POLICY")},
             "pricingSummary": {"price": {"value": f"{it['price']:.2f}", "currency": E("EBAY_CURRENCY", "GBP")}}}
    r = httpx.post(f"{BASE}/sell/inventory/v1/offer", headers=_h(), json=offer, timeout=30)
    if r.status_code >= 300: raise RuntimeError(f"offer: {r.text}")
    offer_id = r.json()["offerId"]
    listing_id = None
    if publish:
        r = httpx.post(f"{BASE}/sell/inventory/v1/offer/{offer_id}/publish", headers=_h(), timeout=30)
        if r.status_code >= 300: raise RuntimeError(f"publish: {r.text}")
        listing_id = r.json().get("listingId")
    return offer_id, listing_id

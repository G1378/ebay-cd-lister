"""Lists your eBay inventory locations + business policies, and creates a location if you have none.
Usage (inside the container):  python -m app.setup_ebay "YOUR POSTCODE"
"""
import sys, httpx
from . import ebay

B, MP = ebay.BASE, ebay.E("EBAY_MARKETPLACE", "EBAY_GB")


def show(r):
    return f"{r.status_code} {r.text[:400]}"


print("== Inventory locations ==")
r = httpx.get(f"{B}/sell/inventory/v1/location", headers=ebay._h(), timeout=30)
locs = r.json().get("locations", []) if r.status_code == 200 else []
if r.status_code != 200:
    print("Error:", show(r))
for l in locs:
    print("  merchantLocationKey =", l["merchantLocationKey"], "|", l.get("name", ""))

if not locs and r.status_code == 200:
    if len(sys.argv) < 2:
        print("  None found. Re-run with your postcode to create one:\n  python -m app.setup_ebay \"AB1 2CD\"")
    else:
        body = {"location": {"address": {"postalCode": sys.argv[1], "country": ebay.E("EBAY_COUNTRY", "GB")}},
                "name": "Home", "merchantLocationStatus": "ENABLED", "locationTypes": ["WAREHOUSE"]}
        c = httpx.post(f"{B}/sell/inventory/v1/location/home", headers=ebay._h(), json=body, timeout=30)
        print("  Created location, key = home" if c.status_code in (200, 201, 204) else "  Create failed: " + show(c))

print("\n== Business policies (marketplace %s) ==" % MP)
for kind, listkey, idkey in [("fulfillment_policy", "fulfillmentPolicies", "fulfillmentPolicyId"),
                             ("payment_policy", "paymentPolicies", "paymentPolicyId"),
                             ("return_policy", "returnPolicies", "returnPolicyId")]:
    r = httpx.get(f"{B}/sell/account/v1/{kind}", headers=ebay._h(), params={"marketplace_id": MP}, timeout=30)
    print(f"\n{kind}:")
    if r.status_code != 200:
        print("  Error:", show(r))
        continue
    items = r.json().get(listkey, [])
    if not items:
        print("  none found - create one in Seller Hub > Account > Business policies")
    for p in items:
        print(f"  {p['name']}  ->  {p[idkey]}")

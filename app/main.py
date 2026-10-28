import os, io, json, base64, hashlib, sqlite3, uuid, time, glob
import httpx
from typing import List
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from . import ebay

DB = os.environ.get("DB_PATH", "cds.db")
MODEL = os.environ.get("AI_MODEL", "anthropic/claude-haiku-4.5")
AI_BASE = os.environ.get("AI_BASE_URL", "https://openrouter.ai/api/v1")
PROMPT = ("List every CD visible in this photo (covers, spines or cases). Return ONLY a JSON array, "
          'each item: {"artist":"","title":"","year":null,"label":"","catno":"","genre":"","confidence":0.0}. '
          "Only include text you can actually read or confidently recognise. No guessing, no commentary.")

app = FastAPI()
IMG_DIR = os.path.join(os.path.dirname(DB) or ".", "img")
os.makedirs(IMG_DIR, exist_ok=True)


def img_files(iid):
    return sorted(glob.glob(os.path.join(IMG_DIR, f"{iid}_*.jpg")))


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


with db() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS photos(hash TEXT PRIMARY KEY, result TEXT);
    CREATE TABLE IF NOT EXISTS items(
      id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT, artist TEXT, title TEXT, year TEXT, label TEXT,
      catno TEXT, genre TEXT, notes TEXT, price REAL DEFAULT 3.99, condition TEXT DEFAULT 'USED_VERY_GOOD',
      confidence REAL, status TEXT DEFAULT 'draft', offer_id TEXT, listing_id TEXT, error TEXT);
    """)


def prep(raw: bytes) -> bytes:
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    im.thumbnail((1568, 1568))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=80)
    return out.getvalue()


def identify(jpg: bytes):
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpg).decode()
    r = httpx.post(f"{AI_BASE}/chat/completions", timeout=120,
                   headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]},
                   json={"model": MODEL, "max_tokens": 4000, "messages": [{"role": "user", "content": [
                       {"type": "image_url", "image_url": {"url": data_url}},
                       {"type": "text", "text": PROMPT}]}]})
    r.raise_for_status()
    j = r.json()
    if "choices" not in j:
        raise RuntimeError(f"OpenRouter error: {str(j)[:300]}")
    ch = j["choices"][0]
    txt = ch["message"].get("content") or ""
    if isinstance(txt, list):  # some providers return content parts
        txt = "".join(p.get("text", "") for p in txt)
    if "[" not in txt or "]" not in txt:
        raise RuntimeError(f"model returned no list (finish={ch.get('finish_reason')}, model={j.get('model')}): "
                           f"{txt[:200] or '<empty>'}")
    return json.loads(txt[txt.index("["): txt.rindex("]") + 1])


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    jpg = prep(await file.read())
    h = hashlib.sha256(jpg).hexdigest()
    with db() as c:
        row = c.execute("SELECT result FROM photos WHERE hash=?", (h,)).fetchone()
        if row:
            return {"added": 0, "cached": True}
        try:
            found = identify(jpg)
        except Exception as e:
            raise HTTPException(502, f"AI call failed: {e}")
        c.execute("INSERT INTO photos VALUES(?,?)", (h, json.dumps(found)))
        for d in found:
            if not d.get("artist") or not d.get("title"):
                continue
            c.execute("INSERT INTO items(sku,artist,title,year,label,catno,genre,confidence) VALUES(?,?,?,?,?,?,?,?)",
                      (uuid.uuid4().hex[:12], d["artist"], d["title"], d.get("year") or "", d.get("label") or "",
                       d.get("catno") or "", d.get("genre") or "", d.get("confidence")))
    return {"added": len(found), "cached": False}


@app.get("/api/items")
def items():
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM items ORDER BY id DESC")]
    for r in rows:
        r["photos"] = len(img_files(r["id"]))
    return rows


@app.patch("/api/items/{iid}")
def patch(iid: int, body: dict):
    allowed = {"artist", "title", "year", "label", "catno", "genre", "notes", "price", "condition"}
    body = {k: v for k, v in body.items() if k in allowed}
    if not body:
        return {}
    with db() as c:
        c.execute(f"UPDATE items SET {','.join(k + '=?' for k in body)} WHERE id=?", (*body.values(), iid))
    return {"ok": True}


@app.delete("/api/items/{iid}")
def delete(iid: int):
    with db() as c:
        c.execute("DELETE FROM items WHERE id=?", (iid,))
    for f in img_files(iid):
        os.remove(f)
    return {"ok": True}


@app.post("/api/items/{iid}/images")
async def add_images(iid: int, files: List[UploadFile] = File(...)):
    for f in files:
        if len(img_files(iid)) >= 12:
            break
        jpg = prep(await f.read())
        with open(os.path.join(IMG_DIR, f"{iid}_{uuid.uuid4().hex[:8]}.jpg"), "wb") as o:
            o.write(jpg)
    return {"photos": len(img_files(iid))}


@app.delete("/api/items/{iid}/images")
def clear_images(iid: int):
    for f in img_files(iid):
        os.remove(f)
    return {"photos": 0}


@app.post("/api/push")
def push(body: dict):
    publish = True
    results = []
    for iid in body.get("ids", []):
        with db() as c:
            it = c.execute("SELECT * FROM items WHERE id=? AND status!='listed'", (iid,)).fetchone()
        if not it:
            continue
        it = dict(it)
        try:
            datas = [open(f, "rb").read() for f in img_files(iid)]
            if not datas:  # fall back to cover art
                cover = ebay.find_cover(it["artist"], it["title"])
                time.sleep(1.1)  # MusicBrainz rate limit
                if not cover:
                    raise RuntimeError("no photos attached and no cover art found")
                datas = [cover]
            urls = [ebay.upload_image(d) for d in datas]
            offer_id, listing_id = ebay.push(it, urls, publish)
            status, err = ("listed" if publish else "offer"), None
        except Exception as e:
            offer_id = listing_id = None
            status, err = "error", str(e)[:500]
        with db() as c:
            c.execute("UPDATE items SET status=?,offer_id=?,listing_id=?,error=? WHERE id=?",
                      (status, offer_id, listing_id, err, iid))
        results.append({"id": iid, "status": status, "error": err})
    return results


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True))

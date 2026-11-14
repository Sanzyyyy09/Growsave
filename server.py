import os, re, shutil, tempfile, threading
from urllib.parse import urlparse, quote
from flask import Flask, request, jsonify, send_file, send_from_directory, after_this_request
import yt_dlp

app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWED = ("facebook.com", "fb.watch", "fb.com", "instagram.com", "instagr.am",
           "tiktok.com", "youtube.com", "youtu.be", "twitter.com", "x.com")
MAX_SECONDS = 20 * 60              # video maksimal 20 menit
MAX_BYTES = 150 * 1024 * 1024      # file maksimal 150 MB
slots = threading.BoundedSemaphore(2)  # maksimal 2 unduhan bersamaan


def ok_url(u):
    try:
        p = urlparse(u)
    except Exception:
        return False
    h = (p.hostname or "").lower()
    return p.scheme in ("http", "https") and any(h == d or h.endswith("." + d) for d in ALLOWED)


def opts(**kw):
    o = {"quiet": True, "noplaylist": True, "socket_timeout": 20}
    cookies = os.path.join(HERE, "cookies.txt")  # opsional, untuk Instagram/Facebook
    if os.path.exists(cookies):
        o["cookiefile"] = cookies
    o.update(kw)
    return o


@app.get("/")
def home():
    return send_from_directory(HERE, "index.html")


@app.get("/healthz")
def health():
    return "ok"


@app.get("/api/info")
def info():
    u = request.args.get("url", "").strip()
    if not ok_url(u):
        return jsonify(error="Link tidak didukung."), 400
    try:
        with yt_dlp.YoutubeDL(opts()) as y:
            d = y.extract_info(u, download=False)
    except Exception:
        return jsonify(error="Video tidak bisa diambil. Pastikan videonya publik."), 422
    if (d.get("duration") or 0) > MAX_SECONDS:
        return jsonify(error="Video terlalu panjang (maksimal 20 menit)."), 413
    q = quote(u, safe="")
    return jsonify(
        title=d.get("title") or "Video",
        thumbnail=d.get("thumbnail"),
        links=[{"label": "Video MP4", "url": "/api/file?type=video&url=" + q},
               {"label": "Audio MP3", "url": "/api/file?type=audio&url=" + q}])


@app.get("/api/file")
def file():
    u = request.args.get("url", "").strip()
    kind = request.args.get("type", "video")
    if not ok_url(u):
        return jsonify(error="Link tidak didukung."), 400
    if not slots.acquire(blocking=False):
        return jsonify(error="Server sedang sibuk, coba lagi sebentar."), 503
    tmp = tempfile.mkdtemp()

    def cleanup():
        shutil.rmtree(tmp, ignore_errors=True)
        slots.release()

    try:
        o = opts(outtmpl=os.path.join(tmp, "v.%(ext)s"), max_filesize=MAX_BYTES)
        if kind == "audio":
            o.update(format="bestaudio/best", postprocessors=[
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}])
        else:
            o.update(format="bv*[height<=720]+ba/b[height<=720]/b", merge_output_format="mp4")
        with yt_dlp.YoutubeDL(o) as y:
            d = y.extract_info(u, download=True)
        files = os.listdir(tmp)
        if not files:
            raise RuntimeError("kosong")
        path = os.path.join(tmp, files[0])
        ext = os.path.splitext(files[0])[1]
        name = re.sub(r"[^\w\- ]", "", d.get("title") or "video").strip()[:60] or "video"
    except Exception:
        cleanup()
        return jsonify(error="Gagal mengunduh video."), 422

    @after_this_request
    def _done(resp):
        resp.call_on_close(cleanup)
        return resp

    return send_file(path, as_attachment=True, download_name=name + ext)

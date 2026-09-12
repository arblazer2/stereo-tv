"""Manual override page: search the collection, tap a release to set NowPlaying.

Stdlib only (ThreadingHTTPServer). Runs in a background thread on the LAN.
    GET  /                  -> page
    GET  /api/now           -> NowPlaying JSON
    GET  /api/search?q=...  -> [{release_id, artist, title, year, cover}]
    POST /api/play          {"release_id": N}   -> set + pause auto-ID
    POST /api/clear                             -> resume auto-ID
    GET  /api/channel / POST {"channel": N}     -> read / change the channel remotely
    GET  /api/audio         -> line-in level (rms, dB, peak) for gain tuning
    GET  /api/theme / POST ?theme=name -> read / switch the theme (cable88, prevue, teletext, phosphor)
    POST /api/screensaver {"mode": "flying"|"weather"|"bounce"|null} -> start the screensaver now
    GET  /covers/<id>.jpg   -> cached cover art
"""
from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from stereotv import config, discogs
from stereotv.state import NowPlaying

log = logging.getLogger("stereotv.web")

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>stereo-tv</title>
<style>
body{font-family:system-ui,sans-serif;background:#0c1848;color:#eee;margin:0;padding:12px}
h1{font-size:18px;margin:0 0 8px;color:#ffd83c}
#now{background:#1e3ca0;border:2px solid #5adcf0;padding:10px;border-radius:6px;display:flex;gap:12px;align-items:center;margin-bottom:10px}
#now img{width:72px;height:72px;object-fit:cover;background:#000}
#now .t{flex:1;min-width:0}#now b{display:block;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#now small{color:#9cf}
input{width:100%;box-sizing:border-box;font-size:18px;padding:10px;border-radius:6px;border:1px solid #5adcf0;background:#000;color:#fff}
ul{list-style:none;padding:0;margin:10px 0}
li{display:flex;gap:10px;align-items:center;padding:8px;border-bottom:1px solid #234;cursor:pointer}
li:active{background:#1e3ca0}
li img{width:56px;height:56px;object-fit:cover;background:#000;flex:none}
li b{display:block}li span{color:#aaa;font-size:13px}
button{font-size:15px;padding:8px 12px;border-radius:6px;border:0;background:#ffaa28;color:#000}
</style></head><body>
<h1>STEREO-TV &middot; manual override</h1>
<div id=now><img id=nimg><div class=t><b id=nt>&nbsp;</b><small id=ns></small></div><button onclick="clr()">Auto</button></div>
<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px"><small style="color:#9cf">THEME</small><select id=theme onchange="setTheme(this.value)" style="flex:1;font-size:15px;padding:6px;border-radius:6px;background:#000;color:#fff;border:1px solid #5adcf0"></select></div>
<input id=q placeholder="Search artist or album" autofocus autocomplete=off>
<ul id=r></ul>
<script>
const $=s=>document.querySelector(s);
async function now(){const j=await (await fetch('/api/now')).json();
 if(j.release){$('#nt').textContent=j.release.artist+' — '+j.release.title;
  $('#nimg').src=j.release.cover_path?'/covers/'+j.release.release_id+'.jpg?'+j.version:'';}
 else{$('#nt').textContent='(nothing)';}
 let s=j.source.toUpperCase();if(j.status)s+=' · '+j.status;
 if(j.override_remaining>0)s+=' · auto in '+Math.ceil(j.override_remaining/60)+' min';
 if(j.side||j.track)s+=' · '+(j.side?'side '+j.side:'')+(j.track?' trk '+j.track:'');
 $('#ns').textContent=s;}
let tm;$('#q').addEventListener('input',()=>{clearTimeout(tm);tm=setTimeout(search,200)});
async function search(){const q=$('#q').value.trim();const j=await (await fetch('/api/search?q='+encodeURIComponent(q))).json();
 $('#r').innerHTML=j.map(r=>`<li onclick="play(${r.release_id})"><img src="${r.cover?'/covers/'+r.release_id+'.jpg':''}"><div><b>${esc(r.artist)}</b>${esc(r.title)}<br><span>${r.year||''} ${esc(r.label||'')}</span></div></li>`).join('');}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function play(id){await fetch('/api/play',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({release_id:id})});now();window.scrollTo(0,0)}
async function clr(){await fetch('/api/clear',{method:'POST'});now()}
async function themes(){const j=await (await fetch('/api/theme')).json();const s=$('#theme');s.innerHTML=Object.entries(j.themes).map(([k,v])=>`<option value="${k}"${k===j.theme?' selected':''}>${v}</option>`).join('')}
async function setTheme(n){await fetch('/api/theme?theme='+encodeURIComponent(n),{method:'POST'})}
now();search();themes();setInterval(now,5000);
</script></body></html>"""


def make_handler(now: NowPlaying, override_minutes: float, channel_ctl=None, audio=None):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            log.debug(fmt, *args)

        def _json(self, obj, status=HTTPStatus.OK):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/api/now":
                self._json(now.as_dict())
            elif u.path == "/api/theme":
                from stereotv import themes
                self._json({"theme": channel_ctl.theme() if channel_ctl else None,
                            "themes": {k: v["label"] for k, v in themes.THEMES.items()}})
            elif u.path == "/api/audio":
                self._json(audio.stats() if audio else {"alive": False, "device": None})
            elif u.path == "/api/channel":
                self._json({"channel": channel_ctl.get() if channel_ctl else None,
                            "fps": round(channel_ctl.fps(), 1) if channel_ctl else None})
            elif u.path == "/api/search":
                q = parse_qs(u.query).get("q", [""])[0]
                con = discogs.open_db()
                try:
                    self._json(discogs.search(con, q))
                finally:
                    con.close()
            elif u.path.startswith("/covers/"):
                name = Path(u.path).name
                p = config.COVERS_DIR / name
                if not p.is_file() or ".." in name:
                    self.send_error(404)
                    return
                data = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "max-age=86400")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("object expected")
            except ValueError as e:
                self._json({"error": f"bad JSON body: {e}"}, HTTPStatus.BAD_REQUEST)
                return
            body.update({k: v[0] for k, v in parse_qs(u.query).items()})   # ?mode=x also works
            if u.path == "/api/play":
                try:
                    rid = int(body.get("release_id"))
                except (ValueError, TypeError):
                    self._json({"error": "release_id required"}, HTTPStatus.BAD_REQUEST)
                    return
                con = discogs.open_db()
                try:
                    rel = discogs.get_release(con, rid)
                finally:
                    con.close()
                if not rel:
                    self._json({"error": "unknown release"}, HTTPStatus.NOT_FOUND)
                    return
                now.set(rel, source="manual", confidence=1.0, override_minutes=override_minutes)
                log.info("manual: %s - %s", rel.artist, rel.title)
                self._json(now.as_dict())
            elif u.path == "/api/clear":
                now.clear_override()
                self._json(now.as_dict())
            elif u.path == "/api/theme":
                from stereotv import themes
                name = str(body.get("theme") or body.get("name") or "")
                if name not in themes.THEMES or not channel_ctl:
                    self._json({"error": "unknown theme", "themes": list(themes.THEMES)}, HTTPStatus.BAD_REQUEST)
                    return
                channel_ctl.theme(name)
                self._json({"theme": name})
            elif u.path == "/api/screensaver":
                mode = body.get("mode")
                ok = channel_ctl.saver(mode) if channel_ctl else False
                self._json({"ok": ok, "mode": mode}, HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST)
            elif u.path == "/api/channel":
                try:
                    ch = int(body.get("channel"))
                except (ValueError, TypeError):
                    self._json({"error": "channel required"}, HTTPStatus.BAD_REQUEST)
                    return
                if not channel_ctl:
                    self._json({"error": "no channel control"}, HTTPStatus.NOT_IMPLEMENTED)
                    return
                channel_ctl.request(ch)
                self._json({"channel": ch})
            else:
                self.send_error(404)

    return H


class WebServer(threading.Thread):
    def __init__(self, now: NowPlaying, host: str = "0.0.0.0", port: int = 8080,
                 override_minutes: float = 30, channel_ctl=None, audio=None):
        super().__init__(name="web", daemon=True)
        self.httpd = ThreadingHTTPServer((host, port), make_handler(now, override_minutes, channel_ctl, audio))
        self.httpd.daemon_threads = True

    def run(self) -> None:
        log.info("manual override page on http://%s:%d/", *self.httpd.server_address)
        self.httpd.serve_forever()

    def stop(self) -> None:
        self.httpd.shutdown()

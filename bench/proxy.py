# -*- coding: utf-8 -*-
"""pi -> llama-server arasina girip GERCEK HTTP govdesini diske doken proxy.

Amac: pi'nin modele gonderdigi sistem promptunun TAMAMINI (pi taban prompt'u +
--append-system-prompt ile eklenen AGENTS.md/persona/family/soul) birebir yakalamak.
Yakalanan prompt A/B kalite kosularinda kullanilir.
"""
import http.server
import json
import pathlib
import urllib.request

HEDEF = "http://192.168.0.25:8082/v1/chat/completions"
CIKTI = pathlib.Path(__file__).parent / "yakalanan_govde.json"


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        ham = self.rfile.read(n)
        if not CIKTI.exists():
            CIKTI.write_bytes(ham)
            print(f"[proxy] govde yakalandi -> {CIKTI} ({len(ham)} bayt)")
        req = urllib.request.Request(HEDEF, data=ham,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                veri = r.read()
                kod = r.status
        except Exception as e:
            veri, kod = json.dumps({"error": str(e)}).encode(), 500
        self.send_response(kod)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(veri)))
        self.end_headers()
        self.wfile.write(veri)

    def do_GET(self):
        with urllib.request.urlopen("http://192.168.0.25:8082" + self.path, timeout=30) as r:
            veri = r.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(veri)))
        self.end_headers()
        self.wfile.write(veri)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("[proxy] 127.0.0.1:9099 -> " + HEDEF)
    http.server.HTTPServer(("127.0.0.1", 9099), H).serve_forever()

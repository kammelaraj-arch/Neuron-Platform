"""
Neuron webhook deployer.

Listens on :9090 inside the container. Bound to 172.17.0.1:8089 on the
host so the host's reverse proxy can reach it but the public internet
cannot. The reverse proxy exposes it at
https://neuron.shital.org.uk/deploy.

Validates X-Deploy-Secret against NEURON_DEPLOY_SECRET, then spawns
/app/deploy.sh in the background. That script only ever touches the
Neuron stack (master_platform/docker-compose.yml).
"""
import os
import threading
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

DEPLOY_SECRET = os.environ.get("NEURON_DEPLOY_SECRET", "")
PORT = int(os.environ.get("NEURON_DEPLOYER_PORT", "9090"))


def run_deploy():
    subprocess.Popen(
        ["/bin/bash", "/app/deploy.sh"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"neuron-deployer"}')
            return
        self.send_response(403)
        self.end_headers()

    def do_POST(self):
        if self.path == "/deploy" and DEPLOY_SECRET and \
                self.headers.get("X-Deploy-Secret") == DEPLOY_SECRET:
            threading.Thread(target=run_deploy, daemon=True).start()
            self.send_response(202)
            self.end_headers()
        else:
            self.send_response(403)
            self.end_headers()


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

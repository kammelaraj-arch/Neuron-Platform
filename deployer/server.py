"""
Neuron-only webhook deployer.

Listens on :9090 inside the container; bound to 172.17.0.1:8089 on the
host so the shared nginx can reach it but the public internet cannot.
The shared nginx exposes it at https://neuron.shital.org.uk/deploy.

This deployer is fully independent from the ShitalEco /deploy endpoint:
 - separate image, separate container, separate Docker volume / socket
 - separate webhook secret (NEURON_DEPLOY_SECRET)
 - only ever invokes /app/deploy.sh inside this container, which only
   touches the Neuron stack. It cannot start or stop a ShitalEco service.
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
        # Tiny liveness ping for the Neuron healthcheck — never deploys.
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

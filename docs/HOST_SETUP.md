# Host setup (one-time, operator-managed)

The Neuron Platform stack runs entirely inside Docker out of
`/opt/neuron-platform/`. The two things the host itself must provide
are:

1. A Linux user `neuron` that owns `/opt/neuron-platform/` and is in
   the `docker` group.
2. Some L7 reverse proxy that terminates TLS for
   `https://neuron.shital.org.uk/` and forwards to `127.0.0.1:8088`
   (or to the docker0 bridge gateway, typically `172.17.0.1:8088`).

That's it. After this one-time setup, the Neuron stack updates itself
via the webhook in `.github/workflows/deploy.yml` and never asks the
host to do anything else.

## 1. Create the `neuron` user

```bash
# As root on the VPS:
sudo adduser --disabled-password --gecos "" neuron
sudo usermod -aG docker neuron
sudo mkdir -p /opt/neuron-platform
sudo chown -R neuron:neuron /opt/neuron-platform
```

## 2. Clone the repo

```bash
# As the neuron user:
sudo -iu neuron
cd /opt/neuron-platform
git clone https://github.com/kammelaraj-arch/neuron-platform.git .
# (or `git clone … /tmp/x && mv /tmp/x/{.[!.],}* .` if you want the
#  checkout *at* /opt/neuron-platform rather than inside a subdir)
```

## 3. Set the webhook secret

```bash
# As the neuron user, in /opt/neuron-platform:
openssl rand -hex 32 > master_platform/.deploy_secret
chmod 600 master_platform/.deploy_secret

cat > master_platform/.env <<EOF
NEURON_DEPLOY_SECRET=$(cat master_platform/.deploy_secret)
NEURON_DEPLOY_BRANCH=main
NEURON_REPO_PATH=/opt/neuron-platform
EOF
chmod 600 master_platform/.env
```

Stash the same `NEURON_DEPLOY_SECRET` value as a **repository secret**
in GitHub: Settings → Secrets and variables → Actions →
`NEURON_DEPLOY_SECRET`.

## 4. Bring up the stack

```bash
# As the neuron user, in /opt/neuron-platform:
docker compose -f master_platform/docker-compose.yml up -d --build

# Verify both containers:
docker compose -f master_platform/docker-compose.yml ps
curl -sS http://127.0.0.1:8088/healthz
curl -sS http://172.17.0.1:8089/healthz
```

## 5. Configure the host reverse proxy

Whatever reverse proxy is already on the VPS (nginx, caddy, traefik,
etc.) needs **one vhost** for `neuron.shital.org.uk` that forwards two
locations to the Neuron stack. Once this is in place it never needs to
change again — the Neuron deploy pipeline doesn't touch it.

Reference nginx config (adapt path and cert references to your host):

```nginx
# HTTP → HTTPS
server {
    listen 80;
    server_name neuron.shital.org.uk;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

# HTTPS — terminate TLS, forward to the Neuron stack on the docker0 bridge
server {
    listen 443 ssl;
    http2 on;
    server_name neuron.shital.org.uk;

    ssl_certificate     /etc/letsencrypt/live/neuron.shital.org.uk/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/neuron.shital.org.uk/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    client_max_body_size 32M;

    # Webhook listener (only POST /deploy; GET /healthz also works)
    location = /deploy {
        proxy_pass         http://172.17.0.1:8089;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_connect_timeout 5s;
        proxy_read_timeout    10s;
    }

    # Everything else → the Master app
    location / {
        proxy_pass         http://172.17.0.1:8088;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Upgrade           $http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_connect_timeout 10s;
        proxy_read_timeout    120s;
    }
}
```

## 6. Issue the TLS certificate (one-time)

```bash
# Example using certbot in standalone mode (adapt as needed):
sudo certbot certonly --webroot -w /var/www/certbot \
  -d neuron.shital.org.uk
```

Then reload the reverse proxy once. After that the deploy pipeline
runs end-to-end without any further host involvement.

## 7. Smoke-test the webhook

From any workstation:

```bash
curl -sS -i -X POST https://neuron.shital.org.uk/deploy \
  -H "X-Deploy-Secret: <the-hex-from-step-3>"
# Expect: HTTP/1.1 202 Accepted
```

A `403` means the secret on the VPS doesn't match the one you sent. A
`502/504` means the reverse proxy can't reach `172.17.0.1:8089` — the
deployer container isn't up.

## First-time admin login

After the first deploy, copy the bootstrap admin key and delete the
file:

```bash
docker compose -f master_platform/docker-compose.yml \
  exec neuron-master cat master_platform/data/bootstrap_admin.txt
# paste the non-comment line at https://neuron.shital.org.uk/login, then:
docker compose -f master_platform/docker-compose.yml \
  exec neuron-master rm master_platform/data/bootstrap_admin.txt
```

From there on, every API key is managed in the UI at `/ui/secrets`.

## Rollback

```bash
docker compose -f master_platform/docker-compose.yml down neuron-master
# Re-tag a previous image and bring it back up, or push the previous
# commit to main and let the webhook redeploy.
docker compose -f master_platform/docker-compose.yml up -d neuron-master
```

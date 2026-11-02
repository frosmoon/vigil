# Vigil

A lightweight, self-hosted status page and infrastructure monitor. Vigil tracks the health of your services and containers and shows them on a minimal dashboard, so you can check uptime and performance at a glance.

## Features

- Minimal footprint, runs as a single container
- Monitors services and Docker containers
- Clean status page you can expose publicly
- Configured entirely through environment variables
- Works behind Traefik or Nginx Proxy Manager

## Prerequisites

- Docker and Docker Compose
- A reverse proxy (Traefik or Nginx Proxy Manager) for external access
- An external Docker network shared with the reverse proxy
- A domain or subdomain for the status page (e.g. `status.yourdomain.com`)

## Quick start

### 1. Clone the repo

```bash
git clone https://github.com/<your-username>/vigil.git
cd vigil
```

### 2. Configure the environment

```bash
cp .env.example .env
```

Open `.env` and fill in the required values:

| Variable | Description |
| --- | --- |
| `PORT` | Host port the web interface is mapped to (e.g. `8080`) |
| `AUTH_KEY` | Authentication key / secret for the app |
| `MONITORED_CONTAINERS` | Containers or services to monitor |

> Variable names above are examples. Check `.env.example` for the exact list your version expects.

Make sure `.env` is never committed:

```bash
grep -q "^\.env$" .gitignore && echo "ok" || echo ".env is NOT ignored"
```

### 3. Start Vigil

```bash
docker compose up -d
```

Check that the container is running and the logs are clean:

```bash
docker compose ps
docker compose logs -f
```

The container should show `Up` with no startup or database connection errors.

### 4. Local health check

Before exposing anything, confirm it works on your local network:

```bash
curl -I http://<server-ip>:<PORT>
```

You should get `HTTP/1.1 200 OK`. Open the same address in a browser and check that:

- the page loads with all assets
- the services you configured show the correct online/offline status

## Reverse proxy

Vigil must be attached to the same Docker network as your reverse proxy.

### Traefik

Add the network and labels to your `docker-compose.yml`. Adjust the network name, entrypoint, and cert resolver to match your Traefik setup:

```yaml
services:
  vigil:
    # ...
    networks:
      - proxy
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=proxy"
      - "traefik.http.routers.vigil.rule=Host(`status.yourdomain.com`)"
      - "traefik.http.routers.vigil.entrypoints=websecure"
      - "traefik.http.routers.vigil.tls.certresolver=letsencrypt"
      - "traefik.http.services.vigil.loadbalancer.server.port=<container-port>"

networks:
  proxy:
    external: true
```

### Nginx Proxy Manager

1. Create a new **Proxy Host** for `status.yourdomain.com`.
2. Set the forward hostname to the Vigil container name and the port to its internal port.
3. Under the **SSL** tab, request a Let's Encrypt certificate and enable **Force SSL**.

### Verify public access

From an external network (e.g. mobile data):

- `https://status.yourdomain.com` loads with a valid SSL certificate
- No Vigil ports are reachable directly from the internet. Only the reverse proxy should be exposed. If you don't need local access anymore, remove the `ports:` mapping from `docker-compose.yml`.

## Updating

```bash
git pull
docker compose pull
docker compose up -d
```

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Container exits on startup | `docker compose logs` for missing or invalid `.env` values |
| Page doesn't load locally | Port mapping in `docker-compose.yml` and firewall rules |
| 502 / 404 from the proxy | Vigil and the proxy are on the same Docker network, and the forwarded port is correct |
| SSL certificate not issued | DNS record points to your server, ports 80/443 are open |
| Service shows wrong status | Monitored names/targets in `.env` match your actual containers |

## Contributing

Issues and pull requests are welcome. For larger changes, open an issue first to discuss what you'd like to change.

## License

MIT, see [LICENSE](LICENSE).


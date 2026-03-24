# tusd Server Setup (Ubuntu + nginx)

This guide covers installing and configuring [tusd](https://github.com/tus/tusd) on an Ubuntu VPS with nginx as a reverse proxy.

## 1. Install tusd

Download the latest release:

```bash
# Check https://github.com/tus/tusd/releases for the latest version
TUSD_VERSION="2.6.0"
wget "https://github.com/tus/tusd/releases/download/v${TUSD_VERSION}/tusd_linux_amd64.tar.gz"
tar xzf tusd_linux_amd64.tar.gz
sudo mv tusd_linux_amd64/tusd /usr/local/bin/
sudo chmod +x /usr/local/bin/tusd
```

Verify:

```bash
tusd --version
```

## 2. Create storage directory

```bash
sudo mkdir -p /srv/tusd-data
sudo chown www-data:www-data /srv/tusd-data
```

## 3. Create systemd service

```bash
sudo tee /etc/systemd/system/tusd.service > /dev/null << 'EOF'
[Unit]
Description=tusd - tus resumable upload server
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data

ExecStart=/usr/local/bin/tusd \
    --host 127.0.0.1 \
    --port 1080 \
    --upload-dir /srv/tusd-data \
    --hooks-http http://127.0.0.1:8000/tusd-hook \
    --hooks-enabled-events pre-create,post-finish

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tusd
sudo systemctl start tusd
```

Check status:

```bash
sudo systemctl status tusd
sudo journalctl -u tusd -f
```

## 4. Configure nginx reverse proxy

Add a location block to your existing nginx server config (e.g., `/etc/nginx/sites-available/overwatch-mcp`):

```nginx
server {
    listen 443 ssl;
    server_name overwatch-mcp.example.com;

    # ... existing SSL and MCP config ...

    # tusd upload endpoint
    location /files/ {
        proxy_pass http://127.0.0.1:1080/files/;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Required for tus — allow large uploads and don't buffer
        client_max_body_size 0;
        proxy_request_buffering off;
        proxy_buffering off;

        # Required for tus — forward the protocol headers
        proxy_set_header Upload-Offset $http_upload_offset;
        proxy_set_header Upload-Length $http_upload_length;
        proxy_set_header Tus-Resumable $http_tus_resumable;
        proxy_set_header Upload-Metadata $http_upload_metadata;

        # Long timeout for large uploads
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    # File download endpoint (direct nginx serving, no tusd needed)
    location /files/download/ {
        alias /srv/tusd-data/;
        internal;  # only accessible via X-Accel-Redirect from the app
    }
}
```

Test and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 5. Environment variables

Add these to the MCP server's `.env`:

```bash
# Auth key that clients must send as Bearer token — generate with:
# python3 -c "import secrets; print(secrets.token_urlsafe(32))"
TUSD_AUTH_KEY=your-secret-key-here

# Path where tusd stores uploaded files (must match tusd --upload-dir)
TUSD_DATA_DIR=/srv/tusd-data

# Optional: max number of matches that can have files attached.
# When exceeded, files from the oldest match are deleted from disk and DB.
# 0 = no limit.
MAX_STORED_MATCHES=50
```

## 6. Run the migration

```bash
uv run alembic upgrade head
```

## 7. Verify the setup

Test that tusd is running:

```bash
curl -s http://127.0.0.1:1080/files/ -X OPTIONS -I
# Should return tus headers (Tus-Resumable, Tus-Version, etc.)
```

Test through nginx:

```bash
curl -s https://overwatch-mcp.example.com/files/ -X OPTIONS -I
```

## Request flow

```
Client (tuspy)
  │
  │  POST /files/ (with Authorization: Bearer <key>)
  ▼
nginx (:443)
  │
  │  proxy_pass
  ▼
tusd (:1080)
  │
  │  pre-create hook → MCP server (:8000/tusd-hook) validates auth + match_id
  │  PATCH /files/<id> (chunked upload)
  │  post-finish hook → MCP server (:8000/tusd-hook) creates MatchFile row
  ▼
/srv/tusd-data/<tus_id>  (file on disk)
```

# Docker Compose Deployment Guide

Complete guide for deploying SpoolSync with docker-compose.

## Quick Start

### 1. Choose Your Configuration

We provide three docker-compose examples:

| File | Use Case | Phase |
|------|----------|-------|
| `docker-compose.example.yml` | **Full setup** with Spoolman + SpoolSync | 1 |
| `docker-compose.simple.yml` | **Add to existing** Spoolman deployment | 1 |
| `docker-compose.phase2.yml` | **Two-way sync** (requires SpoolEase endpoint) | 2 |

### 2. Copy and Configure

```bash
# Choose one and copy to docker-compose.yml
cp docker-compose.example.yml docker-compose.yml

# Edit with your settings
nano docker-compose.yml
```

### 3. Update Configuration

**Required changes:**
- Replace `yourusername/spoolsync:latest` with your Docker Hub username
- Set `SPOOLEASE_URL` to your SpoolEase device IP
- Set `SPOOLEASE_SECURITY_KEY` to your device's security key

### 4. Deploy

```bash
# Start services
docker-compose up -d

# Watch logs
docker-compose logs -f spoolsync

# Check status
docker-compose ps
```

## Configuration Examples

### Example 1: Fresh Install (Spoolman + SpoolSync)

**Use:** `docker-compose.example.yml`

```yaml
services:
  spoolman:
    image: ghcr.io/donkie/spoolman:latest
    ports:
      - "7912:8000"
    volumes:
      - spoolman-data:/home/app/.local/share/spoolman

  spoolsync:
    image: yourusername/spoolsync:latest
    environment:
      SPOOLMAN_URL: http://spoolman:8000/api/v1
      SPOOLEASE_URL: http://192.168.0.120
      SPOOLEASE_MODE: encrypted
      SPOOLEASE_SECURITY_KEY: "123456"
    volumes:
      - spoolsync-state:/state
```

**Deploy:**
```bash
docker-compose -f docker-compose.example.yml up -d
```

**Access:**
- Spoolman UI: http://localhost:7912
- Logs: `docker-compose logs -f spoolsync`

### Example 2: Add to Existing Spoolman

**Use:** `docker-compose.simple.yml`

If you already have Spoolman running:

```yaml
services:
  spoolsync:
    image: yourusername/spoolsync:latest
    network_mode: "container:spoolman"  # Share Spoolman's network
    environment:
      SPOOLMAN_URL: http://localhost:8000/api/v1
      SPOOLEASE_URL: http://192.168.0.120
      SPOOLEASE_MODE: encrypted
      SPOOLEASE_SECURITY_KEY: "123456"
```

**Deploy:**
```bash
docker-compose -f docker-compose.simple.yml up -d
```

### Example 3: Phase 2 Two-Way Sync

**Use:** `docker-compose.phase2.yml`

**Prerequisites:**
- SpoolEase POST endpoint implemented
- JSON mode configured

```yaml
services:
  spoolsync:
    image: yourusername/spoolsync:latest
    environment:
      SPOOLMAN_URL: http://spoolman:8000/api/v1
      SPOOLEASE_URL: http://192.168.0.120
      SPOOLEASE_MODE: json                # JSON mode required
      SPOOLEASE_TOKEN: "your-token"       # Token for endpoint
      TWO_WAY_SYNC: "true"                # Enable Phase 2
```

**Deploy:**
```bash
docker-compose -f docker-compose.phase2.yml up -d
```

## Environment Variables Reference

### Required

| Variable | Example | Description |
|----------|---------|-------------|
| `SPOOLMAN_URL` | `http://spoolman:8000/api/v1` | Spoolman API endpoint |
| `SPOOLEASE_URL` | `http://192.168.0.120` | SpoolEase device IP |
| `SPOOLEASE_MODE` | `encrypted` or `json` | Connection mode |
| `SPOOLEASE_SECURITY_KEY` | `123456` | For encrypted mode |
| `SPOOLEASE_TOKEN` | `your-token` | For JSON mode |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_DIAMETER` | `1.75` | Filament diameter (mm) |
| `DEFAULT_DENSITY` | `1.24` | Filament density (g/cm³) |
| `SYNC_INTERVAL_SECONDS` | `30` | Poll interval |
| `DRY_RUN` | `false` | Test mode (no writes) |
| `ARCHIVE_MISSING` | `true` | Archive removed spools |
| `TWO_WAY_SYNC` | `false` | Enable Phase 2 |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

## Network Configurations

### Option 1: Shared Network (Recommended)

Both services in same compose file:

```yaml
services:
  spoolman:
    networks:
      - spoolsync-network
  
  spoolsync:
    networks:
      - spoolsync-network

networks:
  spoolsync-network:
```

### Option 2: Container Network Mode

SpoolSync uses Spoolman's network:

```yaml
services:
  spoolsync:
    network_mode: "container:spoolman"
    environment:
      SPOOLMAN_URL: http://localhost:8000/api/v1
```

### Option 3: External Network

Connect to existing Spoolman network:

```yaml
services:
  spoolsync:
    networks:
      - spoolman_default

networks:
  spoolman_default:
    external: true
```

## Volume Management

### Persist Data

```yaml
volumes:
  spoolman-data:
    name: spoolman_data
    driver: local
  
  spoolsync-state:
    name: spoolsync_state
    driver: local
```

### Backup State

```bash
# Backup
docker run --rm \
  -v spoolsync_state:/state \
  -v $(pwd):/backup \
  alpine tar czf /backup/spoolsync-backup.tar.gz -C /state .

# Restore
docker run --rm \
  -v spoolsync_state:/state \
  -v $(pwd):/backup \
  alpine tar xzf /backup/spoolsync-backup.tar.gz -C /state
```

## Updating

### Update to Latest Version

```bash
# Pull new image
docker-compose pull spoolsync

# Recreate container
docker-compose up -d spoolsync

# Check logs
docker-compose logs -f spoolsync
```

### Update to Specific Version

```yaml
services:
  spoolsync:
    image: yourusername/spoolsync:v2.0.0  # Specific version
```

## Monitoring

### View Logs

```bash
# Live logs
docker-compose logs -f spoolsync

# Last 100 lines
docker-compose logs --tail=100 spoolsync

# Since timestamp
docker-compose logs --since=30m spoolsync

# Search logs
docker-compose logs spoolsync | grep "reconcile"
```

### Check Status

```bash
# Service status
docker-compose ps

# Resource usage
docker stats spoolsync

# Health check
docker inspect spoolsync | jq '.[0].State.Health'
```

### Key Log Messages

**Successful sync:**
```
INFO    spoolsync              spoolsync starting
INFO    spoolsync              loaded 34 material densities
INFO    spoolsync.reconcile    SE→SM reconcile: {'created': 0, 'updated': 5, ...}
```

**Phase 2 enabled:**
```
INFO    spoolsync              spoolsync TWO-WAY starting
INFO    spoolsync.spoolman     websocket connected, listening for Spoolman changes...
```

## Troubleshooting

### SpoolSync Won't Start

**Check logs:**
```bash
docker-compose logs spoolsync
```

**Common issues:**
- `Missing required environment variables`: Add `SPOOLMAN_URL` and `SPOOLEASE_URL`
- `Connection refused`: Check Spoolman is running
- `unauthorized`: Verify security key or token

### Can't Connect to SpoolEase

**Test connectivity:**
```bash
docker-compose exec spoolsync curl http://192.168.0.120/api/spools
```

**Fixes:**
- Verify SpoolEase IP address
- Check device is on same network
- Test from host: `curl http://192.168.0.120/api/spools`

### Can't Connect to Spoolman

**Test connectivity:**
```bash
docker-compose exec spoolsync curl http://spoolman:8000/api/v1/health
```

**Fixes:**
- Check `depends_on` is set
- Verify network configuration
- Use `docker-compose ps` to check Spoolman is running

### Database Locked

**Symptom:** `database is locked`

**Fix:**
```bash
# Stop services
docker-compose stop

# Remove state volume
docker volume rm spoolsync_state

# Restart
docker-compose up -d
```

### Phase 2 Not Working

**Check:**
1. `TWO_WAY_SYNC=true` is set
2. `SPOOLEASE_MODE=json` (not encrypted)
3. SpoolEase endpoint is implemented
4. Logs show "websocket connected"

## Production Deployment

### Example Production Setup

```yaml
services:
  spoolman:
    image: ghcr.io/donkie/spoolman:latest
    restart: always
    environment:
      SPOOLMAN_DB_TYPE: postgres
      SPOOLMAN_DB_HOST: postgres
    volumes:
      - spoolman-data:/home/app/.local/share/spoolman
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M

  spoolsync:
    image: yourusername/spoolsync:v2.0.0  # Use specific version
    restart: always
    environment:
      SPOOLMAN_URL: http://spoolman:8000/api/v1
      SPOOLEASE_URL: http://192.168.0.120
      SPOOLEASE_MODE: encrypted
      SPOOLEASE_SECURITY_KEY: "${SPOOLEASE_KEY}"  # From .env file
      LOG_LEVEL: WARNING  # Less verbose in production
    volumes:
      - spoolsync-state:/state
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256M
```

### Use Environment File

```bash
# Create .env file
cat > .env << EOF
SPOOLEASE_URL=http://192.168.0.120
SPOOLEASE_KEY=123456
SPOOLMAN_URL=http://spoolman:8000/api/v1
EOF

# Reference in docker-compose.yml
services:
  spoolsync:
    env_file: .env
```

### Logging to File

```yaml
services:
  spoolsync:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

## Examples by Use Case

### Case 1: Home Lab (Single Machine)

```yaml
# Everything on one machine
services:
  spoolman:
    image: ghcr.io/donkie/spoolman:latest
    ports: ["7912:8000"]
    volumes: [spoolman-data:/home/app/.local/share/spoolman]

  spoolsync:
    image: yourusername/spoolsync:latest
    environment:
      SPOOLMAN_URL: http://spoolman:8000/api/v1
      SPOOLEASE_URL: http://192.168.0.120
      SPOOLEASE_MODE: encrypted
      SPOOLEASE_SECURITY_KEY: "123456"
```

### Case 2: Multiple SpoolEase Devices

```yaml
# Sync multiple SpoolEase devices to one Spoolman
services:
  spoolsync-printer1:
    image: yourusername/spoolsync:latest
    environment:
      SPOOLEASE_URL: http://192.168.0.120
      SPOOLEASE_SECURITY_KEY: "key1"
      SPOOLMAN_URL: http://spoolman:8000/api/v1
    volumes: [sync-printer1:/state]

  spoolsync-printer2:
    image: yourusername/spoolsync:latest
    environment:
      SPOOLEASE_URL: http://192.168.0.121
      SPOOLEASE_SECURITY_KEY: "key2"
      SPOOLMAN_URL: http://spoolman:8000/api/v1
    volumes: [sync-printer2:/state]
```

### Case 3: Remote Spoolman

```yaml
# SpoolSync connects to remote Spoolman
services:
  spoolsync:
    image: yourusername/spoolsync:latest
    environment:
      SPOOLMAN_URL: https://spoolman.example.com/api/v1
      SPOOLEASE_URL: http://192.168.0.120
      SPOOLEASE_MODE: encrypted
      SPOOLEASE_SECURITY_KEY: "123456"
```

## Support

- **Documentation**: See [README.md](README.md)
- **Phase 2 Guide**: See [PHASE2-README.md](PHASE2-README.md)
- **Issues**: Check container logs first
- **Updates**: Pull latest image and restart

## Checklist

Before deploying:
- [ ] Updated Docker Hub username in image name
- [ ] Set correct SpoolEase IP address
- [ ] Configured security key or token
- [ ] Tested SpoolEase connectivity
- [ ] Reviewed log level settings
- [ ] Configured volumes for persistence
- [ ] Set restart policy
- [ ] Documented configuration

After deploying:
- [ ] Verify services are running (`docker-compose ps`)
- [ ] Check logs for errors (`docker-compose logs`)
- [ ] Confirm sync is working (check Spoolman UI)
- [ ] Monitor resource usage (`docker stats`)
- [ ] Set up backup for volumes

# Quick Start with Docker Compose 🚀

Get SpoolSync running in 5 minutes!

## Option 1: Full Setup (Spoolman + SpoolSync)

**Best for:** New installations or testing

```bash
# 1. Copy the example
cp docker-compose.example.yml docker-compose.yml

# 2. Edit configuration
nano docker-compose.yml
# Change these lines:
#   - image: yourusername/spoolsync:latest
#   - SPOOLEASE_URL: http://192.168.0.120  (your SpoolEase IP)
#   - SPOOLEASE_SECURITY_KEY: "123456"     (your device key)

# 3. Start services
docker-compose up -d

# 4. Watch logs
docker-compose logs -f spoolsync

# 5. Access Spoolman
open http://localhost:7912
```

**You should see:**
```
INFO    spoolsync starting: SpoolEase=http://192.168.0.120 -> Spoolman=...
INFO    loaded 34 material densities from Spoolman external DB
INFO    reconcile: {'created': 137, 'updated': 0, ...}
```

## Option 2: Add to Existing Spoolman

**Best for:** Already running Spoolman

```bash
# 1. Copy the simple config
cp docker-compose.simple.yml docker-compose.yml

# 2. Edit configuration
nano docker-compose.yml
# Change:
#   - image: yourusername/spoolsync:latest
#   - SPOOLEASE_URL: http://192.168.0.120
#   - SPOOLEASE_SECURITY_KEY: "123456"

# 3. If Spoolman uses custom network:
#    Uncomment the networks section and set correct name

# 4. Start SpoolSync
docker-compose up -d

# 5. Verify
docker-compose logs -f spoolsync
```

## Option 3: Phase 2 Two-Way Sync

**Best for:** Real-time sync (requires SpoolEase endpoint)

```bash
# 1. Implement SpoolEase POST endpoint first!
#    See: PHASE2-SPOOLEASE-ENDPOINT.md

# 2. Copy Phase 2 config
cp docker-compose.phase2.yml docker-compose.yml

# 3. Edit configuration
nano docker-compose.yml
# Change:
#   - image: yourusername/spoolsync:latest
#   - SPOOLEASE_URL: http://192.168.0.120
#   - SPOOLEASE_TOKEN: "your-secure-token"
#   - TWO_WAY_SYNC: "true"

# 4. Start services
docker-compose up -d

# 5. Watch for websocket connection
docker-compose logs -f spoolsync | grep websocket
```

**You should see:**
```
INFO    spoolsync TWO-WAY starting
INFO    websocket connected, listening for Spoolman changes...
```

## Verification Checklist

After starting, verify everything works:

### ✅ Phase 1 (One-Way Sync)

1. **Check SpoolSync is running**
   ```bash
   docker-compose ps
   # Should show spoolsync as "Up"
   ```

2. **Check logs for errors**
   ```bash
   docker-compose logs spoolsync | grep ERROR
   # Should be empty or only show minor warnings
   ```

3. **Verify Spoolman has data**
   - Open http://localhost:7912
   - Check "Spools" page
   - You should see your SpoolEase spools

4. **Test weight sync**
   - Update weight on SpoolEase device
   - Wait 30 seconds
   - Refresh Spoolman UI
   - Weight should update

### ✅ Phase 2 (Two-Way Sync)

5. **Check websocket connected**
   ```bash
   docker-compose logs spoolsync | grep "websocket connected"
   ```

6. **Test write-back**
   - Edit location in Spoolman UI: "Shelf A"
   - Check logs: `docker-compose logs spoolsync | grep "writing back"`
   - Verify in SpoolEase UI: note field shows "Location: Shelf A"

## Common Issues & Fixes

### Issue: "Missing required environment variables"

**Fix:**
```bash
# Add SPOOLMAN_URL and SPOOLEASE_URL to docker-compose.yml
environment:
  SPOOLMAN_URL: http://spoolman:8000/api/v1
  SPOOLEASE_URL: http://192.168.0.120
```

### Issue: "Connection refused" to SpoolEase

**Fix:**
```bash
# Test from host machine first
curl http://192.168.0.120/api/spools

# If that works, it's a Docker networking issue
# Try using host networking:
network_mode: host
environment:
  SPOOLMAN_URL: http://localhost:7912/api/v1
```

### Issue: "database is locked"

**Fix:**
```bash
# Stop and remove state volume
docker-compose down
docker volume rm spoolsync_state
docker-compose up -d
```

### Issue: No spools appearing in Spoolman

**Check:**
```bash
# 1. Verify SpoolEase is accessible
docker-compose exec spoolsync curl http://192.168.0.120/api/spools

# 2. Check security key is correct
# Look for "InvalidTag" error in logs

# 3. Enable debug logging
# In docker-compose.yml: LOG_LEVEL: DEBUG
docker-compose up -d
docker-compose logs -f spoolsync
```

## Configuration Quick Reference

### Required Variables

```yaml
SPOOLMAN_URL: http://spoolman:8000/api/v1  # Spoolman API
SPOOLEASE_URL: http://192.168.0.120        # SpoolEase IP
SPOOLEASE_MODE: encrypted                  # or json
SPOOLEASE_SECURITY_KEY: "123456"           # Your key
```

### Common Customizations

```yaml
# Change poll interval (default 30 seconds)
SYNC_INTERVAL_SECONDS: "60"

# Test without writing (dry-run)
DRY_RUN: "true"

# Change filament diameter
DEFAULT_DIAMETER: "2.85"  # For 2.85mm filament

# Change default density
DEFAULT_DENSITY: "1.27"   # For PETG (PLA: 1.24, ABS: 1.04)

# Enable debug logging
LOG_LEVEL: DEBUG
```

## Commands Cheat Sheet

```bash
# Start services
docker-compose up -d

# Stop services
docker-compose down

# Restart single service
docker-compose restart spoolsync

# View logs (live)
docker-compose logs -f spoolsync

# View logs (last 100 lines)
docker-compose logs --tail=100 spoolsync

# Check status
docker-compose ps

# Update to latest version
docker-compose pull spoolsync
docker-compose up -d spoolsync

# Shell into container
docker-compose exec spoolsync sh

# Check resource usage
docker stats spoolsync

# Remove everything (including volumes!)
docker-compose down -v
```

## Next Steps

1. ✅ **Verify sync is working** - Check Spoolman UI
2. ✅ **Set up monitoring** - Watch logs occasionally
3. ✅ **Configure backups** - See DOCKER-COMPOSE-GUIDE.md
4. ⏳ **Implement Phase 2** - See PHASE2-SPOOLEASE-ENDPOINT.md
5. ✅ **Push to GitHub** - `git push origin main`
6. ✅ **Update Docker Hub** - Happens automatically via Actions

## Getting Help

- **Full guide**: [DOCKER-COMPOSE-GUIDE.md](DOCKER-COMPOSE-GUIDE.md)
- **Phase 2 details**: [PHASE2-README.md](PHASE2-README.md)
- **Troubleshooting**: Check logs first: `docker-compose logs spoolsync`

## Success!

If you see this in the logs:
```
INFO    reconcile: {'created': 137, 'updated': 0, 'unchanged': 0, 'archived': 0, 'errors': 0}
```

**Congratulations! Your SpoolSync is working! 🎉**

Your SpoolEase spools are now syncing to Spoolman every 30 seconds.

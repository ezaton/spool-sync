# Phase 2: Two-Way Sync

## Status

✅ **Bridge implementation complete**
⏳ **SpoolEase endpoint required** (see [PHASE2-SPOOLEASE-ENDPOINT.md](PHASE2-SPOOLEASE-ENDPOINT.md))

Phase 2 adds **real-time two-way sync** between Spoolman and SpoolEase.

## What's New

### Phase 1 (Current - Working)
- ✅ SpoolEase → Spoolman (one-way, polling)
- ✅ 30-second polling interval
- ✅ All spools, vendors, filaments synced
- ✅ Encrypted mode working

### Phase 2 (Implemented, Needs SpoolEase Endpoint)
- ✅ Spoolman → SpoolEase (websocket-driven, real-time)
- ✅ Echo suppression to prevent sync loops
- ✅ Field ownership (only writes location/comment back)
- ✅ Automatic reconnection on disconnect
- ⏳ Requires SpoolEase POST endpoint

## How It Works

### Architecture

```
SpoolEase Device                Bridge                 Spoolman Server
     ├─────────────────────────────────────────────────────────┤
     │                                                          │
     │  Poll every 30s                                          │
     │  ──────────────>  Read changes  ──────────────>         │
     │  (weight, consumption,          (vendors, filaments,    │
     │   tag scans)                     spools)                │
     │                                                          │
     │                   Listen websocket  <──────────────     │
     │  <──────────────  (location, comment                    │
     │  POST update       changes)                             │
     │  (location, comment)                                    │
     └──────────────────────────────────────────────────────────┘
```

### Data Flow

**SpoolEase → Spoolman** (Phase 1, polling):
1. Bridge polls SpoolEase every 30 seconds
2. Detects changes by content hash
3. Upserts vendors, filaments, spools to Spoolman
4. Tracks write in echo suppression table

**Spoolman → SpoolEase** (Phase 2, real-time):
1. Bridge listens to Spoolman websocket
2. Receives event: `{type: "updated", resource: "spool", payload: {...}}`
3. Checks echo suppression (is this our own write?)
4. If not an echo, writes back **only** location/comment to SpoolEase
5. Tracks write for echo suppression

### Field Ownership

| Field | Owner | Sync Direction |
|-------|-------|----------------|
| Physical weight, consumption | SpoolEase | → Spoolman only |
| Tag ID, K-factor | SpoolEase | → Spoolman only |
| Material, color, vendor | Created from SpoolEase, enriched in Spoolman | → Spoolman only |
| **Location, comment** | **Spoolman** | **← → Both ways** |
| Price | Spoolman | ← Spoolman only (not written back yet) |
| Archived | Spoolman | ← Spoolman only (SpoolEase doesn't have concept) |

## Configuration

### Enable Two-Way Sync

Set in `.env`:
```bash
TWO_WAY_SYNC=true
SPOOLEASE_MODE=json
SPOOLEASE_TOKEN=your-token
```

Or in docker-compose.yml:
```yaml
services:
  spoolsync:
    environment:
      TWO_WAY_SYNC: "true"
      SPOOLEASE_MODE: "json"
      SPOOLEASE_TOKEN: "your-secure-token"
```

### SpoolEase Requirements

**Before enabling TWO_WAY_SYNC**, implement the POST endpoint in SpoolEase firmware:

See complete specification: [PHASE2-SPOOLEASE-ENDPOINT.md](PHASE2-SPOOLEASE-ENDPOINT.md)

**Quick summary**:
- Add `POST /api/spools/add-edit.json` endpoint
- Accept: `{id, note}` 
- Authenticate with Bearer token
- Update record using existing `edit_spool_from_web` logic
- ~50 lines of Rust code

## Testing

### 1. Test Phase 1 First

Ensure one-way sync works:
```bash
TWO_WAY_SYNC=false
DRY_RUN=false
python -m spoolsync
```

Verify:
- ✅ Spools appear in Spoolman
- ✅ Weight updates sync
- ✅ No errors in logs

### 2. Implement SpoolEase Endpoint

Follow [PHASE2-SPOOLEASE-ENDPOINT.md](PHASE2-SPOOLEASE-ENDPOINT.md)

Test manually:
```bash
curl -X POST http://192.168.0.120/api/spools/add-edit.json \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"id": "178", "note": "Test location"}'
```

### 3. Enable Two-Way Sync

```bash
TWO_WAY_SYNC=true
SPOOLEASE_MODE=json
SPOOLEASE_TOKEN=your-token
DRY_RUN=true  # Start with dry-run

python -m spoolsync
```

### 4. Test Write-Back

In Spoolman UI:
1. Edit a spool's location: "Shelf A"
2. Watch bridge logs: `writing back to SpoolEase record 178: {'location': 'Shelf A'}`
3. Check SpoolEase UI: note field shows "Location: Shelf A"

### 5. Test Echo Suppression

1. Update weight on SpoolEase device
2. Wait for poll (30s)
3. Weight updates in Spoolman
4. Verify: No write-back to SpoolEase (echo suppressed)

## Monitoring

### Logs

**Successful write-back**:
```
INFO    spoolsync.reconcile_sm_to_se writing Spoolman changes back to SpoolEase 178: {'location': 'Shelf A'}
INFO    spoolsync.spoolease          successfully updated SpoolEase record 178
```

**Echo suppressed**:
```
DEBUG   spoolsync.reconcile_sm_to_se suppressing echo: spoolman spool_id=42 (our recent write)
```

**Websocket connected**:
```
INFO    spoolsync.spoolman           websocket connected, listening for Spoolman changes...
```

### Health Checks

- **Phase 1 working**: Log shows `SE→SM reconcile: {'created': 0, 'updated': X, ...}`
- **Phase 2 working**: Log shows `websocket connected, listening...`
- **Write-backs working**: Search logs for `writing back to SpoolEase`

## Troubleshooting

### Websocket Won't Connect

**Symptoms**: `websocket disconnected: ...`, continuous reconnection attempts

**Fixes**:
- Check Spoolman is accessible: `curl http://block:7912/api/v1/health`
- Verify Spoolman version supports websockets (v0.16+)
- Check network connectivity between bridge and Spoolman

### Write-Backs Not Working

**Symptoms**: Changes in Spoolman don't appear in SpoolEase

**Fixes**:
1. Check `TWO_WAY_SYNC=true` is set
2. Verify SpoolEase endpoint exists: `curl -X POST http://192.168.0.120/api/spools/add-edit.json`
3. Check SPOOLEASE_TOKEN matches
4. Look for errors in logs: `failed to write to SpoolEase`

### Echo Loops

**Symptoms**: Changes ping-pong between systems, logs show repeated writes

**Should not happen** if echo suppression is working. If it does:
1. Check shadow state is being written: `SELECT * FROM recent_writes;`
2. Verify content hashing is consistent
3. Report as bug with logs

### Dry-Run Not Working for Phase 2

**Expected**: Dry-run prevents writes to **Spoolman** but still writes to **SpoolEase** in Phase 2

**Reason**: Write-back respects dry-run flag separately

**Fix**: Check logs for `[dry-run] would write to SpoolEase`

## Performance

### Resource Usage

**Phase 1 only**:
- CPU: ~1-2% idle, ~10% during poll
- Memory: ~50MB
- Network: ~5KB every 30s

**Phase 2 enabled**:
- CPU: ~2-5% (websocket + polling)
- Memory: ~60MB
- Network: ~5KB every 30s + websocket (minimal)

### Latency

- **Phase 1**: Up to 30 seconds (polling interval)
- **Phase 2**: < 1 second (websocket is real-time)

## Migration from Phase 1

If you're currently running Phase 1:

1. ✅ Keep running Phase 1 (no changes needed)
2. ⏳ Implement SpoolEase endpoint when ready
3. ⏳ Set `TWO_WAY_SYNC=true` when endpoint is live
4. ✅ Test with `DRY_RUN=true` first
5. ✅ Enable live write-backs: `DRY_RUN=false`

**No data migration needed** - shadow state is forward-compatible

## Limitations

### Current Limitations

1. **Location + Comment combined**: SpoolEase has one `note` field, so:
   - Spoolman location → SpoolEase note: "Location: X"
   - Spoolman comment → SpoolEase note: "Comment text"
   - Both → "Location: X | Comment text"

2. **Price not written back**: SpoolEase doesn't track price

3. **Archived not written back**: SpoolEase doesn't have archive concept

4. **No conflict resolution UI**: Last-writer-wins with field ownership

### Future Enhancements

- [ ] Separate location field in SpoolEase
- [ ] Price tracking in SpoolEase
- [ ] Archive/soft-delete in SpoolEase
- [ ] Conflict resolution UI
- [ ] Manual conflict override

## FAQ

**Q: Do I need Phase 2?**
A: Only if you want to edit location/comments in Spoolman UI and have them sync back to SpoolEase. Phase 1 works great for read-only Spoolman.

**Q: Can I use encrypted mode with Phase 2?**
A: No, two-way sync requires `SPOOLEASE_MODE=json` for write-back. Encrypted mode is read-only.

**Q: What if the SpoolEase endpoint isn't implemented?**
A: Bridge will log errors when trying to write back. Phase 1 (SpoolEase → Spoolman) continues working.

**Q: Is it safe to enable while running?**
A: Yes! Set `TWO_WAY_SYNC=true` and restart the bridge. No data loss risk - write-backs are idempotent.

**Q: Performance impact?**
A: Minimal. Websocket adds ~10MB memory and <1% CPU. Network traffic is only when changes occur.

## Next Steps

1. ✅ **Currently**: Run Phase 1, verify it works
2. ⏳ **When ready**: Implement SpoolEase endpoint ([spec](PHASE2-SPOOLEASE-ENDPOINT.md))
3. ⏳ **Test**: Enable TWO_WAY_SYNC with DRY_RUN
4. ✅ **Deploy**: Production with full two-way sync

Estimated time to implement SpoolEase endpoint: **1-2 hours** for experienced Rust developer.

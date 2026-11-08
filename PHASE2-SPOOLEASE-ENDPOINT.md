# Phase 2: SpoolEase Write-Back Endpoint Specification

## Overview

For Phase 2 two-way sync, SpoolEase needs a **JSON write endpoint** to receive updates from Spoolman. This allows the bridge to write back Spoolman-owned fields (location, comments) while SpoolEase continues to own physical measurements.

## Required Endpoint

### POST `/api/spools/add-edit.json`

**Purpose**: Update existing spool records with Spoolman-managed fields.

**Authentication**: Bearer token (same as read endpoint)

**Request Format**:
```json
{
  "id": "178",           // Required: SpoolEase record ID
  "note": "Location: Shelf A | User comment here"  // Optional: combined location + comment
}
```

**Response Format**:
```json
{
  "success": true,
  "id": "178"
}
```

**Error Response**:
```json
{
  "success": false,
  "error": "Record not found"
}
```

## Implementation Notes

### 1. Reuse Existing Logic

The existing `edit_spool_from_web` function in `store.rs` (line 275) can be reused:

```rust
// In web_app.rs, add new route handler
async fn handle_spool_update_json(
    State(store): State<Arc<Mutex<Store>>>,
    Json(payload): Json<SpoolRecordJson>
) -> Result<Json<UpdateResponse>, StatusCode> {
    let mut store = store.lock().await;
    
    // Validate token (same as read endpoint)
    // ... authentication logic ...
    
    // Update only allowed fields
    if let Some(record) = store.get_spool_by_id(&payload.id) {
        let mut updated = record.clone();
        
        // Only update note field (where location/comment are stored)
        if let Some(note) = payload.note {
            updated.note = note;
        }
        
        // Use existing update logic
        store.update_spool(updated)?;
        
        Ok(Json(UpdateResponse { success: true, id: payload.id }))
    } else {
        Err(StatusCode::NOT_FOUND)
    }
}
```

### 2. Field Restrictions

**IMPORTANT**: Only allow updating these fields from external writes:
- `note` (combined location + comment)

**NEVER allow external updates to**:
- `weight_current` (owned by scale)
- `weight_new`, `weight_core`, `weight_advertised` (physical measurements)
- `consumed_since_add`, `consumed_since_weight` (tracked by firmware)
- `tag_id` (managed by NFC reader)
- `ext_has_k` (K-factor calibration)

### 3. DTO for JSON Endpoint

Define a clean DTO (similar to the read endpoint):

```rust
#[derive(Deserialize)]
pub struct SpoolUpdateJson {
    pub id: String,           // Required
    pub note: Option<String>, // Optional
}

#[derive(Serialize)]
pub struct UpdateResponse {
    pub success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}
```

### 4. Route Registration

Add to `web_app.rs` routes:

```rust
// In the router setup
.route("/api/spools/add-edit.json", post(handle_spool_update_json))
```

### 5. Token Authentication

Use the same token authentication as the read endpoint:

```rust
async fn extract_token(
    headers: &HeaderMap
) -> Result<String, StatusCode> {
    headers
        .get("Authorization")
        .and_then(|h| h.to_str().ok())
        .and_then(|s| s.strip_prefix("Bearer "))
        .ok_or(StatusCode::UNAUTHORIZED)
        .map(|s| s.to_string())
}
```

## Testing the Endpoint

### 1. Manual Test with curl

```bash
# Update note field
curl -X POST http://<spoolease_address>/api/spools/add-edit.json \
  -H "Authorization: Bearer your-token" \
  -H "Content-Type: application/json" \
  -d '{"id": "178", "note": "Location: Shelf A | Updated from Spoolman"}'
```

### 2. Verify in SpoolEase UI

After posting an update:
1. Check the SpoolEase web UI
2. Verify the note field shows the updated value
3. Confirm weight fields are unchanged

### 3. Test with Bridge

```bash
# Enable two-way sync
export TWO_WAY_SYNC=true
export SPOOLEASE_MODE=json
export SPOOLEASE_TOKEN=your-token

# Run bridge
python -m spoolsync
```

Then in Spoolman UI:
1. Edit a spool's location or comment
2. Watch bridge logs for "writing back to SpoolEase"
3. Verify change appears in SpoolEase

## Configuration

Add to `spoolease.cfg` (optional):

```toml
[integration]
# Token for external sync (bridge authentication)
token = "your-secure-token"

# Enable JSON API endpoints
json_api_enabled = true
```

## Security Considerations

1. **Token must be strong**: Generate a secure random token, not "123456"
2. **Rate limiting**: Consider adding rate limiting to prevent abuse
3. **Field validation**: Strictly validate which fields can be updated externally
4. **Logging**: Log all external updates for audit trail

## Limitations & Trade-offs

### Current Mapping Limitations

**Single `note` field**: SpoolEase only has one `note` field, so location and comment are combined:
```
"Location: Shelf A | User comment here"
```

**Not mapped back**:
- `price`: SpoolEase doesn't track price
- `archived`: SpoolEase doesn't have archive concept (records are just deleted)

### Future Enhancements

Could add separate fields:
```rust
pub struct SpoolRecord {
    // ... existing fields ...
    pub location: String,  // NEW: separate from note
    pub price: Option<f32>, // NEW: price tracking
}
```

But this requires:
1. CSV schema migration
2. UI updates
3. Backward compatibility handling

## Alternative: Read-Only Mode

If write-back is not desired, set `TWO_WAY_SYNC=false` (default) and run Phase 1 only:
- SpoolEase → Spoolman (one-way)
- All changes to location/comment must be made in SpoolEase
- Spoolman becomes a read-only inventory view

This is simpler but less convenient for users who primarily interact with Spoolman.

## Implementation Checklist

- [ ] Add `SpoolUpdateJson` and `UpdateResponse` types
- [ ] Implement `handle_spool_update_json` handler
- [ ] Register `/api/spools/add-edit.json` route
- [ ] Add token authentication check
- [ ] Test manual updates with curl
- [ ] Test with bridge in TWO_WAY_SYNC mode
- [ ] Document in SpoolEase README
- [ ] Add configuration example

## Summary

**Minimal change**: ~50 lines of Rust code to add a single POST endpoint
**Maximum impact**: Enables full two-way sync between SpoolEase and Spoolman
**Safety**: Only allows updating non-physical fields (location, comments)

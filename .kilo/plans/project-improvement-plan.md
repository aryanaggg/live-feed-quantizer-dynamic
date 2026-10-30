# Project Improvement Plan: Live Feed Quantizer

## Coding Agent Instructions

### Role
You are an implementation-capable agent tasked with executing the improvement plan for the Live Feed Quantizer project. Your job is to make the specified code changes, not just advise on them.

### Scope
- Update dependencies and package configurations
- Fix thread safety and concurrency issues
- Improve CORS and security configurations
- Add proper error handling and validation
- Modernize the codebase while maintaining backward compatibility
- Do NOT remove existing functionality or break working features

### Guidelines
1. **Always read the full context** of a file before making changes - use the `read` tool first
2. **Preserve existing behavior** - changes should fix issues, not redesign everything
3. **Run tests after each change** - use `python -m pytest tests -q` to verify
4. **Make one change at a time** - don't batch multiple unrelated fixes
5. **Check for edge cases** - especially around thread safety, error paths, and backward compatibility
6. **Follow the project's coding patterns** - match the existing style, don't impose new conventions
7. **Document changes** - add or update comments where needed to explain non-obvious decisions

### Anti-Patterns to Avoid
- ❌ Don't rewrite the entire frontend in React - keep the HTML/JS as-is unless specifically needed
- ❌ Don't change the core quantization algorithm - it works, just fix surrounding issues
- ❌ Don't remove the RTSP/ONVIF camera support - it's a key feature
- ❌ Don't change the WebSocket protocol message format - only add validation

### Success Criteria
- All 24 existing tests still pass
- Dependencies are updated to modern versions
- No new runtime errors or warnings
- CORS is properly configured (no wildcard origins in production)
- Thread safety is improved without deadlocks
- Camera proxy has basic SSRF protection

---

# Improvement Plan Phases

## Phase 1: Dependency & Configuration Updates
**Goal**: Bring dependencies up to date and fix packaging

### Tasks:
1. **Update requirements.txt** - Replace stale versions with current stable releases
   - fastapi: 0.104.1 → 0.123.4 (or latest 0.x)
   - uvicorn: 0.24.0 → 0.38.0 (or latest 0.x)
   - numpy: 1.24.3 → 2.x (compatible)
   - opencv-python: 4.8.1.78 → 4.12.0 (or latest)
   - pillow: 10.1.0 → 12.0.0 (or latest)
   - librosa: 0.10.0 → 0.11.0 (or latest)
   - scipy: 1.11.4 → 1.16.3 (or latest)
   - Add tinytuya if needed for Tuya integration

2. **Add pyproject.toml** - Create modern Python packaging config
   - Specify dependencies with proper version ranges
   - Add development dependencies section
   - Enable `pip install .` workflow
   - Include python_requires for Python 3.11+

3. **Add tinytuya to requirements** (if keeping Tuya integration)
   - Or make truly optional with proper error handling

### Exit Criteria
- `pip install -r requirements.txt` succeeds
- `pip install .` works (via pyproject.toml)
- All existing tests pass

---

## Phase 2: Security & CORS Hardening
**Goal**: Fix CORS configuration and add SSRF protection

### Tasks:
1. **Fix CORS in server.py** - Replace `allow_origins=["*"]` with proper origin validation
   - Add origin whitelist for production
   - Keep wildcard only for development/localhost
   - Add documentation comment explaining the choice

2. **Add SSRF protection to `/camera_proxy` endpoint**
   - Validate URL scheme (only http/https allowed)
   - Block loopback/private IP addresses (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
   - Add rate limiting (e.g., max 10 requests/minute per client)
   - Return 400 for invalid URLs instead of crashing

### Exit Criteria
- CORS no longer uses wildcard with credentials enabled
- Camera proxy rejects internal/loopback URLs
- Camera proxy returns proper error for malformed URLs

---

## Phase 3: Thread Safety & Concurrency Fixes
**Goal**: Fix shared state issues and improve thread safety

### Tasks:
1. **Fix LUT cache in image_quantizer/quantizer.py**
   - Make cache per-`AudioPaletteQuantizer` instance OR add threading.Lock
   - The module-level `_cache = {"key": None, "lut": None, "pal": None}` is shared across all instances
   - Option A: Move cache inside AudioPaletteQuantizer class
   - Option B: Add lock around cache access in `_get_lut()`
   - Choose Option A as it's cleaner since each connection already has its own quantizer

2. **Ensure latest_audio is per-connection** (verify it already is)
   - In server.py websocket_endpoint, `latest_audio = None` is initialized per connection
   - Verify camera loops (`_ip_camera_loop`, `_rtsp_stream_loop`) use the correct per-connection instance
   - No global `latest_audio` outside per-connection scope

3. **Fix thread safety around quantizer.state**
   - Review all places where `quantizer._lock` is acquired
   - Ensure asyncio.to_thread() + threading.Lock combinations are safe
   - Test with multiple concurrent WebSocket connections

### Exit Criteria
- LUT cache is either per-instance or thread-safe
- Multiple concurrent WebSocket connections don't corrupt each other's state
- No deadlocks in async/sync boundary

---

## Phase 4: Error Handling & Validation
**Goal**: Improve error responses and input validation

### Tasks:
1. **Add proper WebSocket error responses**
   - Unknown control message types should return `{"type": "error", "detail": "unknown_message_type"}` instead of silent `logger.debug` + `continue`
   - Invalid JSON should return error status, not just log warning

2. **Add input validation to control messages**
   - Validate `jpeg_quality` is within range (10-100) before clamping
   - Validate `audio_gain` is within range (0.1-5.0) before clamping
   - Add maximum payload size checks for binary messages

3. **Add error handling to camera_source switching**
   - Handle case where camera_source was never set (ip_camera_stop/rtsp_stop may be None)
   - Add cleanup for orphaned tasks when connection disconnects unexpectedly

### Exit Criteria
- All WebSocket message types return proper responses or errors
- Invalid inputs are rejected with clear error messages
- No silent failures or uncaught exceptions

---

## Phase 5: Frontend Improvements
**Goal**: Improve frontend robustness without major redesign

### Tasks:
1. **Add WebGL availability detection and fallback**
   - Check if WebGL is available on startup
   - Show meaningful error message if unavailable (instead of silent failure)
   - Add `glOk` check similar to existing pattern and disable quantizer if WebGL unavailable

2. **Add localStorage error handling**
   - Wrap all `localStorage.getItem`/`setItem` in try/catch
   - Graceful degradation when in private browsing mode or quota exceeded
   - Don't crash UI when storage is unavailable

3. **Add camera_proxy URL validation notes**
   - Document that users should only paste valid http/https camera URLs
   - Consider adding protocol prefix helper (auto-add http:// if missing)

### Exit Criteria
- WebGL unavailable shows clear error, not blank screen
- No JavaScript errors from localStorage in private browsing
- Frontend remains functional with storage disabled

---

## Phase 6: Testing & Validation
**Goal**: Ensure all changes don't break existing functionality

### Tasks:
1. **Run full test suite** after each phase
   - `python -m pytest tests -q`
   - Verify all 24 tests pass

2. **Add integration tests** if needed for new functionality
   - Test camera proxy with valid/invalid URLs
   - Test multiple concurrent WebSocket connections
   - Test CORS with different origins

3. **Manual verification steps**
   - Start the server: `python -m uvicorn server:app --port 8000`
   - Open browser to http://localhost:8000
   - Test: connect camera, change quality, toggle audio features
   - Test: RTSP/ONVIF camera URLs (if available in environment)

### Exit Criteria
- All 24 existing tests pass
- No new test failures introduced
- Manual browser testing confirms core functionality works

---

## Phase 7: Documentation & Review
**Goal**: Update documentation and verify changes

### Tasks:
1. **Update README.md** with new setup instructions
   - Dependency installation
   - How to run in development mode
   - Known issues and workarounds

2. **Add comments to explain non-obvious decisions**
   - CORS configuration rationale
   - Thread safety choices
   - Any deliberate trade-offs

3. **Review plan completion**
   - Verify all Phase exit criteria met
   - Document any out-of-scope items
   - Create hand-off documentation if needed

### Exit Criteria
- README reflects current setup process
- Key decisions are documented in code comments
- Plan is marked complete

---

## Phase 8: Actor System & State Management
**Goal**: Improve per-connection isolation and reduce global state coupling

### Tasks:
1. **Verify per-connection quantizer isolation** (already complete)
   - Confirm each WebSocket connection gets its own `AudioPaletteQuantizer` instance
   - Verify `latest_audio` is truly per-connection via closure (already verified)
   - Document the isolation pattern for future reference

2. **Add message-passing boundary comments**
   - Add clear comments documenting which state is per-connection vs global
   - Mark areas where camera loops close over shared state
   - Add TODO markers for future refactoring toward message-passing pattern

3. **Clean up camera source state on disconnect**
   - Ensure `ip_camera_stop`, `rtsp_stop` are properly cleaned up in all exit paths
   - Add cleanup for orphaned tasks when WebSocket disconnects unexpectedly
   - Verify the `finally` block (line 729-737) handles all cleanup cases

### Exit Criteria
- Per-connection isolation is documented and verified
- No memory leaks from orphaned async tasks
- All camera source cleanup paths tested

---

## Phase 9: Error Handling Completeness
**Goal**: Ensure all code paths return proper error responses

### Tasks:
1. **Fix silent binary payload drops** (`server.py` line 585-586)
   - Currently: `logger.debug("[WS] Ignoring %d-byte binary payload (not JPEG/PCM)", len(data)) + continue`
   - Fix: Add client feedback for truly unknown binary payloads, or document as expected protocol behavior
   - Decision point: Is silent drop acceptable since binary type doesn't carry format info, or should we send error?

2. **Add error responses for all WS control message fallthroughs**
   - Review all `continue` statements after `isinstance` checks
   - Ensure every control message type either processes or sends error + continues
   - Specifically: `if not isinstance(obj, dict): continue` at line 601

3. **Complete camera_source switching error handling**
   - Handle case where camera_source was never set (ip_camera_stop/rtsp_stop may be None)
   - Add cleanup for orphaned tasks when connection disconnects unexpectedly
   - Verify the `finally` block (line 773-781) handles all cleanup cases

### Exit Criteria
- All WebSocket message types return proper responses or errors
- Invalid inputs are rejected with clear error messages
- No silent failures or uncaught exceptions

---

## Phase 10: Frontend Resilience
**Goal**: Improve frontend robustness without major redesign

### Tasks:
1. **Add WebGL availability detection and fallback** (`frontend/index.html`)
   - Check if WebGL is available on startup (already has `glOk` pattern)
   - Show meaningful error message if unavailable (currently blank screen risk)
   - Disable quantizer features if WebGL unavailable, show "WebGL required" notice

2. **Add localStorage error handling** (frontend JS throughout)
   - Wrap all `localStorage.getItem`/`setItem` in try/catch
   - Graceful degradation when in private browsing mode or quota exceeded
   - Don't crash UI when storage is unavailable
   - Fallback to default settings when storage fails

3. **Add type-ahead helper for camera URLs**
   - Auto-add `http://` prefix if user pastes host only
   - Validate URL format before sending to server
   - Show inline validation feedback

### Exit Criteria
- WebGL unavailable shows clear error, not blank screen
- No JavaScript errors from localStorage in private browsing
- Frontend remains functional with storage disabled

---

## Phase 11: Type Hints & Code Quality
**Goal**: Improve type consistency and code maintainability

### Tasks:
1. **Run mypy/pyright** on the codebase
   - Identify files/type annotation gaps
   - Add missing type hints to functions that lack them
   - Specifically: `server.py` control message handlers, `quantizer.py` functions

2. **Rename `skip_audio` → `preserve_palette`** (`sound_device_processor.py`)
   - The parameter name is misleading - it doesn't "skip" audio, it "preserves" the current palette
   - Update the `process_frame()` function signature
   - Update all call sites
   - Update test `test_skip_audio_legacy_flag_uses_cached_palette` → `test_preserve_palette_uses_cached`

3. **Add pytest fixtures** for test reuse
   - Create conftest.py with shared fixtures
   - Quantizer instances, sample audio tones, test frames
   - Reduce duplication across the 24 existing tests

### Exit Criteria
- mypy/pyright runs with 0 errors (or minimal suppressions)
- `skip_audio` renamed to `preserve_palette` everywhere
- Tests use fixtures, reducing duplication

---

## Phase 12: Rate Limiting & Timestamp Sync
**Goal**: Prevent abuse and improve synchronization

### Tasks:
1. **Add rate limiting to camera loops** (`server.py`)
   - Add frame counter per camera loop
   - Enforce maximum fetch rate (e.g., max 30fps hard cap beyond IP_CAMERA_INTERVAL)
   - Log and drop frames exceeding rate limit

2. **Add audio/video timestamp synchronization**
   - Include frame timestamps in WebSocket binary messages
   - Client can synchronize palette changes with audio events
   - Store last audio timestamp alongside frame data

### Exit Criteria
- Camera loops hard-cap at 30fps maximum
- Timestamp synchronization implemented in data pipeline

---

## Phase 13: Testing & Validation (Extended)
**Goal**: Comprehensive verification of all changes

### Tasks:
1. **Run full test suite** after each new phase
   - `python -m pytest tests -q`
   - Verify all 24 tests pass

2. **Manual verification steps** (expanded)
   - Start the server: `python -m uvicorn server:app --port 8000`
   - Open browser to http://localhost:8000
   - Test: connect camera, change quality, toggle audio features
   - Test: RTSP/ONVIF camera URLs (if available in environment)
   - Test: WebGL availability scenarios
   - Test: private browsing mode
   - Test: concurrent WebSocket connections (2+ tabs)

3. **Add integration tests** if needed for new functionality
   - Test camera proxy with valid/invalid URLs
   - Test multiple concurrent WebSocket connections
   - Test CORS with different origins

### Exit Criteria
- All 24 existing tests pass
- No new test failures introduced
- Manual browser testing confirms core functionality
- Edge cases (private browsing, concurrent connections) handled gracefully

---

## Success Metrics (Updated Table)

| Metric | Current | Target |
|--------|---------|--------|
| Dependencies up-to-date | 0/9 | 9/9 |
| CORS wildcard usage | 1/1 | 0/1 *(Phase 2 partially)* |
| LUT cache thread-safety | Unsafe | Safe *(Phase 3)* |
| Test pass rate | 24/24 | 24/24 |
| Camera proxy SSRF protection | None | URL validation *(Phase 2)* |
| WebGL fallback | None | Detected + error shown *(Phase 10)* |
| pyproject.toml exists | No | Yes *(Phase 1)* |
| Actor system documented | No | Documented *(Phase 8)* |
| Error responses complete | Partial | Complete *(Phase 9)* |
| Type hints consistent | Partial | Consistent *(Phase 11)* |
| Rate limiting active | None | Active *(Phase 12)* |

---
# Integrations Hub Feature Plan

## Overview
Add a unifying integrations hub to the Live Feed Quantizer ecosystem that enables connecting external services: local network Tuya/IoT devices, WiFi equipment, Google Home, and other smart home providers. This hub will serve as the bridge between the live music visualization system and external IoT/smart home devices.

## Branch
- `feature/integrations-hub` (to be created by user/git)

## Design Decisions

### 1. Integration Types
- **Local Network (Tuya/IoT)**: Discover and control Tuya WiFi smart devices on the local network using `tinytuya` library
- **WiFi Equipment**: Generic WiFi device discovery and connection
- **Google Home**: Cast audio/visual control via Google Home/Nest devices
- **Other Providers**: Open framework for additional integrations

### 2. UI Design
- Add "Integrations" button to the control dock (next to existing Tune button)
- Modal/panel that shows connected services and allows adding new ones
- Tabbed interface: Local Network, WiFi, Google Home, Other

### 3. Backend Architecture
- Extend FastAPI WebSocket endpoint to support integration commands
- Add device discovery handlers
- Create integration manager that coordinates between services
- Maintain per-connection integration state (isolation principle)

### 4. Data Flow
```
User clicks "Add Device" → UI sends integration request → 
Backend discovers/devices → Returns device info → UI displays device ->
User selects device → Integration-specific control → 
Quantizer receives device state → Audio/palette updates reflect device status
```

### 5. Minituya/tinytuya Library
- Use `tinytuya` (PyPI) for Tuya IoT device integration
- Library supports: local network scanning, device control, cloud API
- Installation: `pip install tinytuya`

### 6. Google Home Integration
- Use `google-home-api` or native Cast protocol
- Support: audio playback control, device status queries
- Discovery via mDNS/DNS-SD

## Affected Files

### Frontend (`frontend/index.html`)
- Add "Integrations" button to `#dock`
- Add integrations modal/panel UI
- WebSocket message handlers for integration commands

### Backend (`server.py`)
- Add integration command types to WebSocket protocol
- Add device discovery endpoints
- Add tinytuya device management
- Add Google Home Cast integration

### New Files (if needed)
- `integrations.py` - Integration manager module
- `tuya_scanner.py` - Tuya local network scanner

## Implementation Phases

### Phase 1: UI Foundation
- [ ] Add Integrations button to dock
- [ ] Create integrations panel with tabs
- [ ] Add basic styling matching existing UI

### Phase 2: Local Network/Tuya Integration
- [ ] Install tinytuya dependency
- [ ] Implement device scanner/discovery
- [ ] Add device selection and control UI
- [ ] WebSocket handlers for Tuya devices

### Phase 3: Google Home Integration
- [ ] Research and select Google Home API library
- [ ] Implement Cast/Audio control
- [ ] Add device discovery via mDNS
- [ ] WebSocket handlers for Google Home

### Phase 4: Hub Orchestration
- [ ] Create integration manager class
- [ ] Coordinate multiple simultaneous integrations
- [ ] State management across services
- [ ] Error handling and fallback

## Risks & Considerations
1. **Tinytuya dependency**: Requires `cryptography` library for AES encryption support
2. **Google Home**: May require OAuth or cast protocol knowledge
3. **Network dependencies**: Integrations require network access, gracefully handle offline state
4. **Isolation**: Per-connection integration state must not interfere with existing quantizer behavior
5. **Security**: API keys and credentials must be handled securely (local storage with encryption optional)

## Validation
- Tuya device discovery on local network
- Google Home device detection and control
- Multiple integrations can run simultaneously
- UI integrates smoothly with existing dock/tune panel
- No breakage of existing camera/audio quantization pipeline

## Open Questions
- Should integrations be per-connection or global (shared across all clients)?
- What Google Home API/library to use?
- How to handle device credentials securely?
- Should there be a "default" integration setup on first run?
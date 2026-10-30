# Graph Report - live-feed-quantizer  (2026-10-28)

## Corpus Check
- 21 files · ~37,259 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 63 file(s) not represented in the graph (top: .ttf 54, (none) 4, .bak 1)

## Summary
- 270 nodes · 456 edges · 14 communities (13 shown, 1 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 24 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `eb35b32b`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- server.py
- numpy
- main.py
- AudioPaletteQuantizer
- capture_mic_and_playback.py
- test_server_ws.py
- Integrations Hub Feature Plan
- railway.json
- _RtspCap
- _RtspCap
- 🚀 Deploy to Railway
- SKILL.md
- live-feed-quantizer Knowledge Graph Analysis
- Live Feed Quantizer

## God Nodes (most connected - your core abstractions)
1. `AudioPaletteQuantizer` - 25 edges
2. `websocket_endpoint()` - 17 edges
3. `extract_colors_from_audio()` - 14 edges
4. `_rtsp_stream_loop()` - 12 edges
5. `quantize_array()` - 10 edges
6. `_ip_camera_loop()` - 10 edges
7. `MonitorApp` - 9 edges
8. `_handle_frame()` - 9 edges
9. `Integrations Hub Feature Plan` - 9 edges
10. `quantize_image()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `server.py Restructuring` --references--> `_RtspCap`  [INFERRED]
  .kilo/plans/graphify-code-fixes-plan.md → server.py
- `Architecture` --references--> `extract_colors_from_audio()`  [INFERRED]
  README.md → audio_to_palette/music_visualizer.py
- `Architecture` --references--> `quantize_array()`  [INFERRED]
  README.md → image_quantizer/quantizer.py
- `Architecture` --references--> `quantize_image()`  [INFERRED]
  README.md → image_quantizer/quantizer.py
- `server.py Restructuring` --references--> `websocket_endpoint()`  [INFERRED]
  .kilo/plans/graphify-code-fixes-plan.md → server.py

## Import Cycles
- None detected.

## Communities (14 total, 1 thin omitted)

### Community 0 - "server.py"
Cohesion: 0.06
Nodes (19): Graphify-Detected Improvements, Key Changes Made, server.py Restructuring, camera_proxy(), _clamp(), _decode_jpeg_bytes(), decode_jpeg_payload(), _decode_jpeg_payload() (+11 more)

### Community 1 - "numpy"
Cohesion: 0.19
Nodes (12): AudioPaletteQuantizer, process_frame(), _frame(), test_instances_have_isolated_state(), test_invalid_audio_chunk_is_ignored_not_fatal(), test_quantizes_with_audio_and_output_uses_palette(), test_returns_none_for_silent_audio_before_any_palette(), test_returns_none_without_audio() (+4 more)

### Community 2 - "main.py"
Cohesion: 0.12
Nodes (22): extract_colors_from_audio(), _build_lut(), _get_lut(), _nearest_indices(), _prepare_palette(), quantize_array(), quantize_bgr(), quantize_image() (+14 more)

### Community 3 - "AudioPaletteQuantizer"
Cohesion: 0.13
Nodes (6): adjust_channels(), format_device_entry(), list_audio_devices(), main(), MonitorApp, callback()

### Community 4 - "capture_mic_and_playback.py"
Cohesion: 0.12
Nodes (8): _audio_loop(), AudioProcessor, latest_audio_chunk(), start_audio(), stop_audio(), _get_screen_size(), start_camera(), process_frame()

### Community 5 - "test_server_ws.py"
Cohesion: 0.31
Nodes (7): _connect(), _jpeg_data_url(), test_full_frame_flow_returns_quantized_jpeg(), test_invalid_json_returns_no_crash(), test_missing_audio_returns_json_waiting_status(), test_ping_pong(), _tone()

### Community 6 - "Integrations Hub Feature Plan"
Cohesion: 0.09
Nodes (22): 1. Integration Types, 2. UI Design, 3. Backend Architecture, 4. Data Flow, 5. Minituya/tinytuya Library, 6. Google Home Integration, Affected Files, Backend (`server.py`) (+14 more)

### Community 7 - "railway.json"
Cohesion: 0.15
Nodes (13): description, devDependencies, concurrently@^10.0.6, name, private, scripts, backend, dev (+5 more)

### Community 8 - "_RtspCap"
Cohesion: 0.29
Nodes (6): build, builder, numReplicas, restartPolicyMaxRetries, deploy, $schema

### Community 10 - "🚀 Deploy to Railway"
Cohesion: 0.13
Nodes (14): 1. Push your changes to GitHub, 2. Go to Railway, 3. Configure on Railway, 4. Deploy, 5. Test Your App, Build fails?, Camera access denied?, Cost (+6 more)

### Community 11 - "SKILL.md"
Cohesion: 0.20
Nodes (9): CANVAS CREATION, DEDUCING THE SUBTLE REFERENCE, DESIGN PHILOSOPHY CREATION, ESSENTIAL PRINCIPLES, FINAL STEP, HOW TO GENERATE A VISUAL PHILOSOPHY, MULTI-PAGE OPTION, PHILOSOPHY EXAMPLES (+1 more)

### Community 12 - "live-feed-quantizer Knowledge Graph Analysis"
Cohesion: 0.29
Nodes (6): Graphify-Driven Code Fixes - Actual Results, live-feed-quantizer Knowledge Graph Analysis, Post-fix Cohesion Scores (after server.py restructuring), Pre-fix Cohesion Scores (from initial graphify run), Verification, What Was NOT Changed (within permission scope)

### Community 13 - "Live Feed Quantizer"
Cohesion: 0.29
Nodes (6): Deploy (Railway), Live Feed Quantizer, Run locally (local window mode), Run locally (web), Tests, WebSocket protocol (`/ws`)

## Knowledge Gaps
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AudioPaletteQuantizer` connect `numpy` to `server.py`, `main.py`, `capture_mic_and_playback.py`?**
  _High betweenness centrality (0.144) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `AudioPaletteQuantizer` (e.g. with `Architecture` and `_ip_camera_loop()`) actually correct?**
  _`AudioPaletteQuantizer` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Should `server.py` be split into smaller, more focused modules?**
  _Cohesion score 0.0602322206095791 - nodes in this community are weakly interconnected._
- **Why does `server.py Restructuring` connect `server.py` to `_RtspCap`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `websocket_endpoint()` (e.g. with `server.py Restructuring` and `.set_sample_rate()`) actually correct?**
  _`websocket_endpoint()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Should `main.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11923076923076924 - nodes in this community are weakly interconnected._
- **Why does `_RtspCap` connect `_RtspCap` to `server.py`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
# Graph Report - live-feed-quantizer  (2026-10-28)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 196 nodes · 376 edges · 10 communities (9 shown, 1 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 11 edges (avg confidence: 0.85)
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
- package.json
- railway.json
- _RtspCap
- _RtspCap

## God Nodes (most connected - your core abstractions)
1. `AudioPaletteQuantizer` - 24 edges
2. `websocket_endpoint()` - 16 edges
3. `extract_colors_from_audio()` - 13 edges
4. `_rtsp_stream_loop()` - 11 edges
5. `MonitorApp` - 9 edges
6. `_handle_frame()` - 9 edges
7. `quantize_array()` - 9 edges
8. `_ip_camera_loop()` - 8 edges
9. `_frame()` - 8 edges
10. `test_full_frame_flow_returns_quantized_jpeg()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `test_instances_have_isolated_state()` --calls--> `AudioPaletteQuantizer`  [EXTRACTED]
  tests/test_sound_device_processor.py → sound_device_processor.py
- `test_invalid_audio_chunk_is_ignored_not_fatal()` --calls--> `AudioPaletteQuantizer`  [EXTRACTED]
  tests/test_sound_device_processor.py → sound_device_processor.py
- `test_quantizes_with_audio_and_output_uses_palette()` --calls--> `AudioPaletteQuantizer`  [EXTRACTED]
  tests/test_sound_device_processor.py → sound_device_processor.py
- `test_returns_none_for_silent_audio_before_any_palette()` --calls--> `AudioPaletteQuantizer`  [EXTRACTED]
  tests/test_sound_device_processor.py → sound_device_processor.py
- `test_returns_none_without_audio()` --calls--> `AudioPaletteQuantizer`  [EXTRACTED]
  tests/test_sound_device_processor.py → sound_device_processor.py

## Import Cycles
- None detected.

## Communities (10 total, 1 thin omitted)

### Community 0 - "server.py"
Cohesion: 0.07
Nodes (16): camera_proxy(), _clamp(), _decode_jpeg_bytes(), decode_jpeg_payload(), _decode_jpeg_payload(), _handle_frame(), health(), _ip_camera_loop() (+8 more)

### Community 1 - "numpy"
Cohesion: 0.18
Nodes (13): AudioPaletteQuantizer, _clamp(), process_frame(), _frame(), test_instances_have_isolated_state(), test_invalid_audio_chunk_is_ignored_not_fatal(), test_quantizes_with_audio_and_output_uses_palette(), test_returns_none_for_silent_audio_before_any_palette() (+5 more)

### Community 2 - "main.py"
Cohesion: 0.20
Nodes (12): _build_lut(), _get_lut(), _nearest_indices(), _prepare_palette(), quantize_array(), quantize_bgr(), quantize_image(), _quantize_with_lut() (+4 more)

### Community 3 - "AudioPaletteQuantizer"
Cohesion: 0.13
Nodes (6): adjust_channels(), format_device_entry(), list_audio_devices(), main(), MonitorApp, callback()

### Community 4 - "capture_mic_and_playback.py"
Cohesion: 0.15
Nodes (6): _audio_loop(), AudioProcessor, latest_audio_chunk(), start_audio(), stop_audio(), process_frame()

### Community 5 - "test_server_ws.py"
Cohesion: 0.21
Nodes (9): _get_screen_size(), start_camera(), _connect(), _jpeg_data_url(), test_full_frame_flow_returns_quantized_jpeg(), test_invalid_json_returns_no_crash(), test_missing_audio_returns_json_waiting_status(), test_ping_pong() (+1 more)

### Community 6 - "package.json"
Cohesion: 0.27
Nodes (8): extract_colors_from_audio(), test_all_rgb_colors_are_ints(), test_empty_audio_returns_no_chunks(), test_energy_consistency_tone_vs_silence_headers(), test_requires_sample_rate_with_array(), test_returns_n_colors(), test_silent_audio_still_returns_equal_energy_palette(), _tone()

### Community 7 - "railway.json"
Cohesion: 0.15
Nodes (13): description, devDependencies, concurrently@^10.0.6, name, private, scripts, backend, dev (+5 more)

### Community 8 - "_RtspCap"
Cohesion: 0.29
Nodes (6): build, builder, deploy, numReplicas, restartPolicyMaxRetries, $schema

## Knowledge Gaps
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AudioPaletteQuantizer` connect `numpy` to `server.py`, `main.py`, `capture_mic_and_playback.py`?**
  _High betweenness centrality (0.165) - this node is a cross-community bridge._
- **Should `server.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07342995169082125 - nodes in this community are weakly interconnected._
- **Why does `_RtspCap` connect `_RtspCap` to `server.py`?**
  _High betweenness centrality (0.054) - this node is a cross-community bridge._
- **Should `AudioPaletteQuantizer` be split into smaller, more focused modules?**
  _Cohesion score 0.13043478260869565 - nodes in this community are weakly interconnected._
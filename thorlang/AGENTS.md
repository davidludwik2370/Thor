# Frozen Python compiler reference

The user retired this compiler from ongoing maintenance. The snapshot is tagged
`python-reference-2026-11-21`; source hashes are in
`docs/python-reference-frozen.json`.

Implement compiler fixes and features in Rust. Do not update these Python
compiler modules to match new Rust behavior. Preserve them for historical
inspection and explicitly requested optional differential tests. Python test
runners and other development tools outside this directory remain maintained.
The tagged full repository preserves the matching shared frontend too.

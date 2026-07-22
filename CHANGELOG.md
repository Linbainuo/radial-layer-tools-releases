# Changelog

All notable changes to Radial Layer Tools are documented here.

## Unreleased

## 1.0.1 - 2026-07-22

- Fixed GitHub release downloads by safely following asset redirects.
- Added checksum mismatch diagnostics for update troubleshooting.
- Added a green visual state when a new version is available.
- Added a restart confirmation after a successful in-plugin update.
- Added Restart later and Restart now actions in English and Chinese.
- Restart now uses Painter's normal close flow so unsaved projects receive Painter's native save prompt.
- Replaced the Windows command-string relaunch with a bundled restart helper.
- The helper waits for the old Painter process to exit before relaunching.

## 1.0.0 - 2026-07-22

- Added asynchronous update checks through public GitHub Releases.
- Added verified in-plugin download and installation with local backup and rollback.
- Preserved `radial_layer_tools_config.json` during release packaging and updates.
- Added a tag-only GitHub Actions release workflow.
- Added configurable radial menus and menu presets.
- Added fill layer, paint layer, mask, adjustment, and Painter filter commands.
- Added bilingual command search and Painter-language following.
- Added hold-key radial interaction, layer navigation shortcuts, and visibility toggle.
- Added settings editor with command reordering, menu management, and visual preview.
- Added a visible product version label to the properties page.


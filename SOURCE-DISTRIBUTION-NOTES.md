# Source distribution notes

The recovered ClassPoints 2.13.16 source package is being prepared for publication.

The original recovered package contained two third-party Windows executables:
- `assets/ffmpeg.exe` (FFmpeg build `N-92722-gf22fcd4483`, configured with `--enable-gpl --enable-version3`)
- `cloudflared.exe` (Cloudflare cloudflared; Apache License 2.0 notice found in the binary)

These binaries are intentionally not included in the public source archive until their redistribution requirements and provenance are fully addressed. The PyInstaller spec currently expects them at the paths above, so a complete Windows application build requires appropriately licensed copies.

The project source is licensed under MIT by `LICENSE`. Third-party components retain their own licenses; see `THIRD-PARTY-NOTICES.md`.

The original source code was lost; this source tree was reconstructed by the project author from the author's remaining `setup.exe` and `app.exe` artifacts. Reconstructed build documentation identifies the reconstruction process and known limitations.

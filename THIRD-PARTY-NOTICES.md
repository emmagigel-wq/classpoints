# Third-party software notices

The MIT license in the repository's `LICENSE` file applies only to original
ClassPoints project code. It does not relicense bundled third-party software.

## FFmpeg

The recovered Windows build includes an `ffmpeg.exe` binary. Its version
banner identifies it as `N-92722-gf22fcd4483`, and its recorded configure
options include `--enable-gpl --enable-version3`. This indicates a GPLv3
licensing basis for that build.

FFmpeg is a separate project:
- Project: https://ffmpeg.org/
- License information: https://ffmpeg.org/legal.html
- Source code: https://ffmpeg.org/download.html

**Redistribution note:** Do not redistribute this exact FFmpeg binary as part
of a public release until the corresponding source code and build/configuration
materials have been identified and the applicable GPL obligations have been
reviewed and satisfied. A generic link to a different FFmpeg source snapshot
may not be sufficient for the exact binary.

## cloudflared

The recovered Windows build includes a Cloudflare `cloudflared.exe` binary.
Its Go build metadata identifies the Cloudflare cloudflared project and the
module version `v0.0.0-20261021105109-de6376c5af4a+dirty`. The binary contains
an Apache License 2.0 notice.

- Project: https://github.com/cloudflare/cloudflared
- License: https://github.com/cloudflare/cloudflared/blob/master/LICENSE

Cloudflared remains subject to its own Apache License 2.0 terms, including
preservation of applicable notices and any required NOTICE material.

## Other dependencies

Python packages and other third-party components used by ClassPoints retain
their respective licenses. Consult each dependency's own distribution
metadata and license files before redistributing a packaged application.

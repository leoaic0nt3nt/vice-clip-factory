# Third-party software and assets

This is an aggregation of independent pinned projects plus original orchestration.
The root MIT license does not replace any upstream license.

| Source | Role | License / important boundary |
|---|---|---|
| yt-dlp/yt-dlp | Media ingest | Unlicense; platform access and media rights remain separate |
| m-bain/whisperX | ASR, word alignment | BSD-2-Clause; model licenses and gated weights are separate |
| Breakthrough/PySceneDetect | Scene boundaries | BSD-3-Clause |
| WyattBlue/auto-editor | Audio/motion editing | Unlicense for source; binary dependencies have their own terms; current source is Nim |
| remotion-dev/remotion | Optional React renderer | Custom Remotion License, not MIT; check eligibility before commercial use |
| FFmpeg/FFmpeg | Media processing | LGPL/GPL depending on configuration and components; inspect your binary build |
| Teekeks/pyTwitchAPI | Twitch SDK/EventSub | MIT |
| Edualnog/medusa-clip | Optional gameplay engine | AGPL-3.0; redistribution and network-service obligations may apply to your combined/modified deployment |
| fralapo/clippyme | Optional watcher/editor app | MIT; publishing integration depends on external Zernio, with separate terms/pricing |

All original license files remain inside their submodule. Exact source commits:
[upstream.lock.json](upstream.lock.json). Clone using `--recurse-submodules`.
Remotion checkout on the author's disk may be sparse; Git records the full commit.

Bundled Inter Tight static fonts are derived from the Inter Tight variable font;
its SIL Open Font License is in [assets/fonts/OFL.txt](assets/fonts/OFL.txt).
Original diagrams, film-strip art and synthetic fixture are authored for this project.
The original Instagram references and design-reference PDF are not redistributed.

This repository is unaffiliated with Rockstar, Take-Two, Twitch, Google, Meta,
Remotion and the listed maintainers. No gameplay or music license is granted.

## GTA VI game mark

The game-identification mark in assets/gta-vi-logo.png is an original downloaded asset from [Rockstar's GTA VI website](https://www.rockstargames.com/VI). Source URL and checksum: assets/gta-vi-logo.source.json. The trimmed placement copy preserves visible pixels. This mark is not included in the root MIT grant. Vice Clip Factory is independent and claims no Rockstar/Take-Two affiliation or endorsement.

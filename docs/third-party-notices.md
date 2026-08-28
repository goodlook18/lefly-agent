# Third-Party Notices

LeFly Agent source is licensed under Apache-2.0. This document records direct
third-party software whose code is bundled into the packaged Web Console, plus
direct dependencies installed separately by users or contributors.

## Bundled Console runtime

| Package | Version | License | Upstream |
| --- | --- | --- | --- |
| `lucide-react` | 0.539.0 | ISC | [Lucide](https://github.com/lucide-icons/lucide) |
| `react` | 19.1.1 | MIT | [React](https://github.com/facebook/react) |
| `react-dom` | 19.1.1 | MIT | [React](https://github.com/facebook/react) |
| `three` | 0.179.1 | MIT | [Three.js](https://github.com/mrdoob/three.js) |

### React and React DOM MIT notice

```text
MIT License

Copyright (c) Meta Platforms, Inc. and affiliates.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Three.js MIT notice

```text
The MIT License

Copyright © 2010-2025 three.js authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

### Lucide ISC notice

```text
ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part
of Feather (MIT). All other copyright (c) for Lucide are held by Lucide
Contributors 2022.

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
```

## Separately installed Python dependencies

The source archive does not vendor Python dependencies. Package installers
resolve them from each package's `pyproject.toml`. Direct `v0.1.1` runtime and
optional text-Agent dependencies include:

| Package | Validated version | License |
| --- | --- | --- |
| `aiohttp` | 3.14.3 | Apache-2.0 AND MIT |
| `websockets` | 15.0.1 | BSD-3-Clause |
| `livekit-agents` | 1.5.4 | Apache-2.0 |
| `livekit-plugins-openai` | 1.5.4 | Apache-2.0 |
| `openai` | 2.54.0 | Apache-2.0 |
| `httpx` | 0.28.1 | BSD-3-Clause |

`jsonschema` 4.26.0 (MIT) is used by Protocol tests. Review
resolved transitive dependency metadata when creating a binary distribution;
`v0.1.1` publishes source only.

## Contributor-only frontend dependencies

Playwright, Testing Library, TypeScript, Vite, Vitest, jsdom, React/Three type
packages, and the Vite React plugin are direct development dependencies. They
are used to build and test the Console but are not runtime imports in the
packaged application.

The Console uses operating-system font fallbacks. LeFly Agent does not bundle
PingFang, Microsoft YaHei, Noto Sans CJK, or another web font.

## Media

The Console screenshot is generated from this repository. The prototype
photographs and survey QR image are LeFly-owned release media approved for
public distribution. Visible third-party product names and trademarks in the
parts photograph belong to their respective owners and do not imply endorsement.

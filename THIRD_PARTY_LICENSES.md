# Third-party software

crmbuilder uses the open-source packages below. Each keeps its own license; the
full license texts ship inside the packages (`site-packages/*/LICENSE`,
`node_modules/*/LICENSE`). Exact versions are pinned in `uv.lock` and
`frontend/pnpm-lock.yaml`.

## Python

| Package | License |
|---|---|
| anthropic | MIT |
| certifi | MPL-2.0 |
| httpx | BSD-3-Clause |
| pydantic | MIT |
| PyYAML | MIT |
| fastapi | MIT |
| SQLAlchemy | MIT |
| alembic | MIT |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| psycopg | LGPL-3.0 (used unmodified as a library) |
| uvicorn | BSD-3-Clause |

## Web interface

| Package | License |
|---|---|
| @bitrix24/b24ui-nuxt | MIT, © 2024 Bitrix24 |
| @bitrix24/b24icons-vue | MIT, © 2024 Bitrix24 |
| nuxt, vue | MIT |
| tailwindcss | MIT |

"Bitrix24" is a trademark of its owner. crmbuilder is an independent product that
works with Bitrix24; it is not made or endorsed by Bitrix24.

## Bitrix24 UI and icons (MIT)

The license of `@bitrix24/b24ui-nuxt` and `@bitrix24/b24icons-vue`:

```
MIT License

Copyright (c) 2024 Bitrix24

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

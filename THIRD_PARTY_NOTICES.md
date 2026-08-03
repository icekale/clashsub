# Third-party notices

The administration shell adapts layout ideas from `cms-tg-ingest`, Copyright
(c) 2026 cms-tg-ingest contributors, licensed under the MIT License. No
CMS-specific code or brand assets are included.

The frontend uses Vue, Vue Router, Vite, Naive UI, Vitest, and Vue Test Utils.
These projects and their bundled dependencies are distributed under their
respective open-source licenses; the named projects are available under the
MIT License. License texts remain available in the installed packages and
their upstream repositories.

The Python runtime uses FastAPI, Uvicorn, Pydantic, HTTPX, PyYAML, and
argon2-cffi under their respective open-source licenses. Their license texts
remain available in the installed distributions and upstream repositories.

The runtime image bundles the SubConverter-Extended conversion service
(`aethersailor/subconverter-extended`), which is licensed under GPL-3.0.
Its source is available at
https://github.com/Aethersailor/SubConverter-Extended. It is launched as a
separate loopback process inside the same container; no GPL code is linked
into the Python application.

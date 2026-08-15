# Versioned API contract

The committed OpenAPI document is the backend contract for API consumers. Its
semantic version is exposed as `info.version` and is currently `0.2.0`.

Regenerate the contract whenever routes or public schemas change:

```powershell
.venv\Scripts\python.exe scripts\generate-openapi.py
```

Verify that the committed document matches the application before a release:

```powershell
.venv\Scripts\python.exe scripts\generate-openapi.py --check
```

Consumers should generate their SDK from `apps/api/openapi/v1.json`, pin the
contract version they support, and treat a breaking major-version change as a
deliberate migration. This keeps client generation separate from the frontend
implementation and works for TypeScript, Python, or government integration
clients.

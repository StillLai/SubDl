# SubDl Code Review Criteria

## Code Quality

### Type Annotations
- [ ] All functions have complete type annotations (parameters + return value)
- [ ] Uses Python 3.12+ syntax: `list[str]` not `List[str]`, `str | None` not `Optional[str]`

### Logging
- [ ] Uses `log_info` / `log_warn` / `log_error` from utils (not `print` or `logging` module directly)
- [ ] All log output goes to stderr (never stdout)
- [ ] stdout is only used for Node.js subprocess JSON communication

### Exception Handling
- [ ] Uses `SubDlError` hierarchy (`ConfigError` / `DownloadError` / `ConversionError` / `TemplateError` / `ValidationError` / `UploadError`)
- [ ] No bare `Exception` — custom exceptions must inherit `SubDlError`
- [ ] Exceptions carry a `context` dict with relevant info

### Network Requests
- [ ] Uses `utils.http_get_with_retry` / `http_request`
- [ ] No direct `requests` or `urllib` usage outside utils.py

### Shared Mutable State
- [ ] Functions that modify template data call `copy.deepcopy()` before mutation
- [ ] No in-place modification of shared template objects (providers/outbounds etc.)

### Code Design
- [ ] No duplicated judgment logic — same check executed once, result passed via parameters
- [ ] Modifications only touch target objects — no side effects on unrelated data
- [ ] Solution is optimal, not just functional

## Naming Conventions
- [ ] Files: snake_case (Python), camelCase (Node.js)
- [ ] Functions: snake_case
- [ ] Constants: UPPER_SNAKE_CASE
- [ ] Exception classes: PascalCase
- [ ] Dataclasses: PascalCase with `@dataclass` decorator
- [ ] Private members: `_single_underscore` prefix

## Architecture Constraints

### stdout / stderr Separation
- [ ] stdout only outputs JSON data (Node.js subprocess communication)
- [ ] All logging via `log_info` etc. → stderr

### Provider Prefix Mechanism
- [ ] Node tags use `"{sub_name}/"` prefix (merge_config step 1)
- [ ] Provider expansion uses `"{provider_tag}/{node_tag}"` format

### Gist File Naming
- [ ] `_MANAGED_FILE_PATTERNS` defines managed file patterns: `*.yaml`, `*-singbox.json`, `sing-box*.json`
- [ ] Not modified without strong reason

### Template Structure
- [ ] `config_template/` modular: base / dns / providers / outbounds / route + inbounds/ variants
- [ ] New variants only require adding a file in `inbounds/`

## Documentation Sync (MANDATORY before declaring done)

After any code change, check and update ALL that apply:

- [ ] Changed module responsibilities or file roles → update AGENTS.md "文件职责"
- [ ] Added/removed/renamed public functions → update AGENTS.md "文件职责"
- [ ] Changed data flow → update AGENTS.md "数据流"
- [ ] Changed key constraints → update AGENTS.md "关键设计约束"
- [ ] Added/modified environment variables → update AGENTS.md "环境变量"
- [ ] Changed data models → update AGENTS.md "数据模型"
- [ ] Design decisions or rationale → update ARCHITECTURE.md "关键设计决策"
- [ ] Code or architecture changes → append to CHANGELOG.md (skip for pure template/ruleset changes)
- [ ] Changes touch README.md content → sync README.md

## Do NOT Suggest Optimizing

These are intentional design choices (see ARCHITECTURE.md "关键设计决策" section):
- Runs entirely on GitHub Actions CI — no dry-run / local preview needed
- No node deduplication (different subscriptions don't share nodes)
- No region distribution stats (template regex already groups by region)
- No subscription expiry warnings (provider sends emails)
- HTTP timeout not configurable (30s hardcoded is sufficient)

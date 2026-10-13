# Vendored from OpenJarvis

Source: https://github.com/open-jarvis/OpenJarvis at commit ecbde4b32091e2a8b02e8acfa41cb5f95e4da560
License: Apache License 2.0 (LICENSE in this folder). Copyright the OpenJarvis authors.

| File | Upstream path | Modified |
|---|---|---|
| types.py | src/openjarvis/skills/types.py | no |
| parser.py | src/openjarvis/skills/parser.py | yes: one import made relative (`from .types import SkillManifest`) |
| tool_translator.py | src/openjarvis/skills/tool_translator.py | no (JARVIS passes its own translation table) |

Used by hq/skills/skillgate.py to parse SKILL.md frontmatter and find tool references.
Not vendored (see docs/01_RESEARCH_AUDIT.md): the importer (no hashes, licence or staging), the runtime skill manager,
the optimizer and the benchmark.

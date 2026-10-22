"""Goose-style routine recipes: parameterised, validated, replayable task descriptions.

A recipe is a YAML or JSON document (see ``load_recipe``).  ``render`` turns a
recipe plus parameter values into a ``RunPlan``; ``fingerprint`` identifies the
exact recipe version; ``replay`` renders a list of recorded runs independently.

Pure Python, no network.  YAML is parsed with ``yaml.safe_load`` only.
Error messages never contain recipe text (only field names / indices).

Provenance: recipe idea from goose (github.com/aaif-goose/goose, Apache-2.0), commit 6835dd4, crates/goose-cli/src/recipes/; reimplemented in Python, no goose code.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MAX_RECIPE_BYTES = 64 * 1024
MAX_VALUE_CHARS = 2000
MAX_TITLE_CHARS = 100

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
INPUT_TYPES = ("string", "number", "boolean", "select")
REQUIREMENTS = ("required", "optional")

_TOP_KEYS = {"version", "title", "description", "parameters", "instructions",
             "prompt", "tools", "steps"}
_PARAM_KEYS = {"key", "input_type", "requirement", "description", "default", "options"}
_STEP_KEYS = {"name", "tool", "args"}


class RecipeError(Exception):
    """Invalid recipe. Messages never include recipe text."""


class RecipeTooLarge(RecipeError):
    """Recipe exceeds the 64 KB size cap."""


class RenderError(Exception):
    """Base class for render-time errors."""


class MissingParameter(RenderError):
    """A required parameter was not supplied."""


class UnknownParameter(RenderError):
    """A value was supplied for a parameter the recipe does not declare."""


class BadValue(RenderError):
    """A value failed type coercion or injection checks."""


@dataclass
class Parameter:
    key: str
    input_type: str
    requirement: str = "required"
    description: str = ""
    default: Any = None
    options: list[str] | None = None


@dataclass
class Step:
    name: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Recipe:
    title: str
    description: str
    instructions: str = ""
    prompt: str | None = None
    tools: list[str] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    steps: list[Step] | None = None
    version: int = 1
    warnings: list[str] = field(default_factory=list)


@dataclass
class RunPlan:
    instructions: str
    prompt: str | None
    steps: list[dict[str, Any]]
    values_used: dict[str, Any]
    tools: list[str]


@dataclass
class ReplayFailure:
    index: int
    error: str
    kind: str


# --------------------------------------------------------------------------
# loading / validation
# --------------------------------------------------------------------------

def _walk_strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def _placeholders(text: str, where: str) -> set[str]:
    """Return placeholder names in text; refuse malformed template markers."""
    names = {m.group(1) for m in PLACEHOLDER_RE.finditer(text)}
    leftover = PLACEHOLDER_RE.sub("", text)
    if "{{" in leftover or "}}" in leftover:
        raise RecipeError(f"{where}: malformed template marker")
    return names


def _check_injection(value: str, where: str, exc: type[Exception]) -> None:
    if len(value) > MAX_VALUE_CHARS:
        raise exc(f"{where}: value exceeds {MAX_VALUE_CHARS} characters")
    if "{{" in value or "}}" in value:
        raise exc(f"{where}: value contains a template marker")
    for ch in value:
        o = ord(ch)
        if ch in "\n\t":
            continue
        if o < 32 or 0x7F <= o <= 0x9F:
            raise exc(f"{where}: value contains a control character")


def _coerce(param: Parameter, value: Any, exc: type[Exception], where: str) -> Any:
    t = param.input_type
    if t == "string":
        if not isinstance(value, str):
            raise exc(f"{where}: expected a string")
        _check_injection(value, where, exc)
        return value
    if t == "select":
        if not isinstance(value, str) or value not in (param.options or []):
            raise exc(f"{where}: not one of the allowed options")
        _check_injection(value, where, exc)
        return value
    if t == "number":
        if isinstance(value, bool):
            raise exc(f"{where}: expected a number")
        if isinstance(value, (int, float)):
            num: Any = value
        elif isinstance(value, str):
            s = value.strip()
            try:
                num = int(s) if re.fullmatch(r"[+-]?\d+", s) else float(s)
            except ValueError:
                raise exc(f"{where}: expected a number") from None
        else:
            raise exc(f"{where}: expected a number")
        if isinstance(num, float) and not math.isfinite(num):
            raise exc(f"{where}: expected a finite number")
        return num
    if t == "boolean":
        if isinstance(value, bool):
            return value
        if value == "true":
            return True
        if value == "false":
            return False
        raise exc(f"{where}: expected a boolean")
    raise exc(f"{where}: unknown input type")


def _parse_text(text: str, fmt: str | None) -> Any:
    if fmt is None:
        fmt = "json" if text.lstrip().startswith(("{", "[")) else "yaml"
    if fmt not in ("json", "yaml"):
        raise RecipeError("unknown format")
    try:
        if fmt == "json":
            return json.loads(text)
        return yaml.safe_load(text)
    except (ValueError, yaml.YAMLError, RecursionError):
        raise RecipeError(f"document is not valid {fmt}") from None


def _read_source(source: str | Path) -> str:
    if isinstance(source, Path):
        path: Path | None = source
    elif "\n" not in source and len(source) < 400 and not source.lstrip().startswith("{"):
        try:
            path = Path(source) if Path(source).is_file() else None
        except (OSError, ValueError):
            path = None
    else:
        path = None
    if path is not None:
        try:
            if path.stat().st_size > MAX_RECIPE_BYTES:
                raise RecipeTooLarge("recipe exceeds the 64 KB size cap")
            data = path.read_bytes()
        except OSError:
            raise RecipeError("cannot read recipe file") from None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise RecipeError("recipe is not valid UTF-8") from None
    return source


def load_recipe(text_or_path: str | Path, fmt: str | None = None) -> Recipe:
    """Load and validate a recipe from text or a file path (YAML or JSON)."""
    text = _read_source(text_or_path)
    if len(text.encode("utf-8", "surrogatepass")) > MAX_RECIPE_BYTES:
        raise RecipeTooLarge("recipe exceeds the 64 KB size cap")
    return from_dict(_parse_text(text, fmt))


def _req_str(d: dict, key: str, where: str, required: bool = True) -> str | None:
    if key not in d or d[key] is None:
        if required:
            raise RecipeError(f"{where}: '{key}' is required")
        return None
    if not isinstance(d[key], str):
        raise RecipeError(f"{where}: '{key}' must be a string")
    return d[key]


def from_dict(data: Any) -> Recipe:
    """Build and validate a Recipe from a plain dict."""
    if not isinstance(data, dict):
        raise RecipeError("recipe must be a mapping")
    if not all(isinstance(k, str) for k in data):
        raise RecipeError("recipe keys must be strings")
    if set(data) - _TOP_KEYS:
        raise RecipeError("recipe has unknown top-level fields")
    version = data.get("version", 1)
    if isinstance(version, bool) or version != 1:
        raise RecipeError("unsupported recipe version")

    title = _req_str(data, "title", "recipe")
    if not title.strip():
        raise RecipeError("recipe: 'title' must not be empty")
    if len(title) > MAX_TITLE_CHARS:
        raise RecipeError(f"recipe: 'title' exceeds {MAX_TITLE_CHARS} characters")
    description = _req_str(data, "description", "recipe")
    instructions = _req_str(data, "instructions", "recipe", required=False) or ""
    prompt = _req_str(data, "prompt", "recipe", required=False)

    # tools
    if "tools" not in data or not isinstance(data["tools"], list):
        raise RecipeError("recipe: 'tools' is required and must be a list")
    tools: list[str] = []
    for i, t in enumerate(data["tools"]):
        if not isinstance(t, str) or not t.strip():
            raise RecipeError(f"tools[{i}]: must be a non-empty string")
        if "*" in t:
            raise RecipeError(f"tools[{i}]: wildcard tools are refused")
        if "{{" in t or "}}" in t:
            raise RecipeError(f"tools[{i}]: placeholders are not allowed in tool names")
        tools.append(t)

    # parameters
    raw_params = data.get("parameters", [])
    if raw_params is None:
        raw_params = []
    if not isinstance(raw_params, list):
        raise RecipeError("recipe: 'parameters' must be a list")
    params: list[Parameter] = []
    seen: set[str] = set()
    for i, p in enumerate(raw_params):
        where = f"parameters[{i}]"
        if not isinstance(p, dict) or not all(isinstance(k, str) for k in p):
            raise RecipeError(f"{where}: must be a mapping")
        if set(p) - _PARAM_KEYS:
            raise RecipeError(f"{where}: unknown fields")
        key = _req_str(p, "key", where)
        if not KEY_RE.match(key):
            raise RecipeError(f"{where}: invalid key (must match [a-z][a-z0-9_]{{0,31}})")
        if key in seen:
            raise RecipeError(f"{where}: duplicate key")
        seen.add(key)
        itype = _req_str(p, "input_type", where)
        if itype not in INPUT_TYPES:
            raise RecipeError(f"{where}: unknown input_type")
        req = p.get("requirement", "required")
        if req not in REQUIREMENTS:
            raise RecipeError(f"{where}: unknown requirement")
        desc = _req_str(p, "description", where, required=False) or ""
        options = p.get("options")
        if itype == "select":
            if (not isinstance(options, list) or not options
                    or not all(isinstance(o, str) for o in options)):
                raise RecipeError(f"{where}: select needs a non-empty list of string options")
        elif options is not None:
            raise RecipeError(f"{where}: options are only valid for select")
        param = Parameter(key, itype, req, desc, None, list(options) if options else None)
        if p.get("default") is not None:
            param.default = _coerce(param, p["default"], RecipeError, f"{where}.default")
        params.append(param)

    # steps
    steps: list[Step] | None = None
    if data.get("steps") is not None:
        if not isinstance(data["steps"], list):
            raise RecipeError("recipe: 'steps' must be a list")
        steps = []
        for i, s in enumerate(data["steps"]):
            where = f"steps[{i}]"
            if not isinstance(s, dict) or not all(isinstance(k, str) for k in s):
                raise RecipeError(f"{where}: must be a mapping")
            if set(s) - _STEP_KEYS:
                raise RecipeError(f"{where}: unknown fields")
            name = _req_str(s, "name", where)
            tool = _req_str(s, "tool", where)
            if "{{" in name or "}}" in name:
                raise RecipeError(f"{where}: placeholders are not allowed in step names")
            if "{{" in tool or "}}" in tool:
                raise RecipeError(f"{where}: placeholders are not allowed in tool names")
            if tool not in tools:
                raise RecipeError(f"{where}: tool is not in the recipe's tools allow-list")
            args = s.get("args", {})
            if args is None:
                args = {}
            if not isinstance(args, dict):
                raise RecipeError(f"{where}: 'args' must be a mapping")
            _check_args_keys(args, where)
            steps.append(Step(name, tool, copy.deepcopy(args)))

    # placeholders
    used: set[str] = set()
    used |= _placeholders(instructions, "instructions")
    if prompt is not None:
        used |= _placeholders(prompt, "prompt")
    for i, s in enumerate(steps or []):
        for text in _walk_strings(s.args):
            used |= _placeholders(text, f"steps[{i}].args")
    declared = {p.key for p in params}
    if used - declared:
        raise RecipeError("a placeholder refers to an undeclared parameter")
    warnings: list[str] = []
    for i, p in enumerate(params):
        if p.key not in used:
            if p.requirement == "required":
                raise RecipeError(f"parameters[{i}]: required parameter is never used")
            warnings.append(f"parameters[{i}]: declared parameter is never used")

    return Recipe(title=title, description=description, instructions=instructions,
                  prompt=prompt, tools=tools, parameters=params, steps=steps,
                  version=1, warnings=warnings)


def _check_args_keys(obj: Any, where: str) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise RecipeError(f"{where}: argument names must be strings")
            if "{{" in k or "}}" in k:
                raise RecipeError(f"{where}: placeholders are not allowed in argument names")
            _check_args_keys(v, where)
    elif isinstance(obj, list):
        for v in obj:
            _check_args_keys(v, where)
    elif not (obj is None or isinstance(obj, (str, int, float, bool))):
        raise RecipeError(f"{where}: unsupported argument value type")


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _subst(obj: Any, text_values: dict[str, str]) -> Any:
    if isinstance(obj, str):
        # single pass: re.sub never rescans replacement text
        return PLACEHOLDER_RE.sub(lambda m: text_values[m.group(1)], obj)
    if isinstance(obj, dict):
        return {k: _subst(v, text_values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_subst(v, text_values) for v in obj]
    return obj


def render(recipe: Recipe, values: dict[str, Any]) -> RunPlan:
    """Render a recipe with parameter values into a RunPlan."""
    if not isinstance(values, dict):
        raise BadValue("values must be a mapping")
    declared = {p.key: p for p in recipe.parameters}
    if any(k not in declared for k in values):
        raise UnknownParameter("values contain parameters the recipe does not declare")
    used: dict[str, Any] = {}
    for p in recipe.parameters:
        if p.key in values:
            used[p.key] = _coerce(p, values[p.key], BadValue, f"parameter '{p.key}'")
        elif p.default is not None:
            used[p.key] = p.default
        elif p.requirement == "required":
            raise MissingParameter(f"missing required parameter '{p.key}'")
    text_values = {p.key: _stringify(used[p.key]) if p.key in used else ""
                   for p in recipe.parameters}
    steps = [{"name": s.name, "tool": s.tool, "args": _subst(s.args, text_values)}
             for s in (recipe.steps or [])]
    return RunPlan(
        instructions=_subst(recipe.instructions, text_values),
        prompt=None if recipe.prompt is None else _subst(recipe.prompt, text_values),
        steps=steps,
        values_used=used,
        tools=list(recipe.tools),
    )


def replay(recipe: Recipe, runs: list[dict[str, Any]]) -> list[RunPlan | ReplayFailure]:
    """Render each run independently; a failing run yields a ReplayFailure with its index."""
    out: list[RunPlan | ReplayFailure] = []
    for i, run in enumerate(runs):
        try:
            out.append(render(recipe, run))
        except RenderError as e:
            out.append(ReplayFailure(i, str(e), type(e).__name__))
    return out


# --------------------------------------------------------------------------
# serialisation / fingerprint
# --------------------------------------------------------------------------

def to_dict(recipe: Recipe) -> dict[str, Any]:
    """Plain-dict form of a recipe (warnings excluded)."""
    d: dict[str, Any] = {
        "version": recipe.version,
        "title": recipe.title,
        "description": recipe.description,
        "parameters": [
            {"key": p.key, "input_type": p.input_type, "requirement": p.requirement,
             "description": p.description, "default": p.default,
             "options": None if p.options is None else list(p.options)}
            for p in recipe.parameters
        ],
        "instructions": recipe.instructions,
        "prompt": recipe.prompt,
        "tools": list(recipe.tools),
        "steps": None if recipe.steps is None else [
            {"name": s.name, "tool": s.tool, "args": copy.deepcopy(s.args)}
            for s in recipe.steps
        ],
    }
    return d


def fingerprint(recipe: Recipe) -> str:
    """sha256 hex digest of the canonical form (sorted keys). Text is hashed exactly as written: whitespace and indentation can matter (a step's
    code argument), and an audit entry must say exactly which recipe ran. Re-formatting the YAML layout does not change it, because the parsed
    strings are what is hashed."""
    canon = json.dumps(to_dict(recipe), sort_keys=True,
                       separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canon.encode("ascii")).hexdigest()


def dump(recipe: Recipe, fmt: str = "yaml") -> str:
    """Canonical text for a recipe in 'yaml' or 'json'."""
    d = to_dict(recipe)
    if fmt == "json":
        return json.dumps(d, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if fmt == "yaml":
        return yaml.safe_dump(d, sort_keys=True, allow_unicode=True, default_flow_style=False)
    raise RecipeError("unknown format")

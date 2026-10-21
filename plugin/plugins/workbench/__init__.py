"""Workbench: learn unfamiliar tasks, turn a client's raw materials into source-grounded requirements and deliverables,
and generate structured prompts. The logic is in service.py and the modules it uses (tested without a model or host).

What this wrapper adds: the local model (only when it is a verified local endpoint), the research plugin, and JARVIS
through jarvis_bridge (the kernel's approvals, audit and kill switch apply to everything JARVIS does). Executing a prompt
or a learned workflow needs ``confirm: true`` (the panel's button) and a reachable JARVIS with the kill switch off; those
entries are also never offered to main_agent's planner (planner.PLANNER_EXCLUDED_ENTRIES).
"""
from __future__ import annotations

import asyncio
from typing import Any

from plugin.plugins._shared import screen_text
from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr, ui

from . import library, localmodel, projects, prompts, service, skillhub
from .learning import grade  # noqa: F401  (re-exported for the panel's source labels)

STR = {"type": "string"}
EXEC_ENTRIES = ("execute_prompt", "run_learned_workflow", "set_provider_policy", "enable_skill", "rollback_skill", "import_skill")


def _err(e: Exception) -> Err:
    return Err(SdkError(screen_text.clean_message(str(e), 300)))


@neko_plugin
class WorkbenchPlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        ok, why = await asyncio.to_thread(localmodel.available)
        return Ok({"status": "ready", "folder": str(projects.root()), "local_model": ok, "local_model_note": why})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    @ui.context(id="dashboard")
    async def dashboard(self):
        ok, why = await asyncio.to_thread(localmodel.available)
        return {"projects": await asyncio.to_thread(projects.list_projects),
                "prompts": await asyncio.to_thread(prompts.list_prompts, None),
                "workflows": await asyncio.to_thread(library.list_workflows),
                "local_model": ok, "local_model_note": why}

    # ---------------------------------------------------------------- host helpers
    async def _call(self, ref: str, args: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        res = await self.plugins.call_entry_json(ref, args, timeout=timeout)
        if isinstance(res, Err):
            raise RuntimeError(screen_text.clean_message(getattr(res, "error", res), 200))
        val = res.value if isinstance(res.value, dict) else {}
        return val.get("data", val) if isinstance(val.get("data", val), dict) else val

    async def _running(self) -> set[str] | None:
        try:
            listed = await self.plugins.list_ids(enabled=True)
        except Exception:  # noqa: BLE001 - unknown: the plan does not claim a tool is missing
            return None
        return None if isinstance(listed, Err) else set(listed.value)

    async def _jarvis_gate(self) -> str:
        """'' when JARVIS is reachable and its kill switch is off; otherwise why nothing may run (fails closed)."""
        try:
            st = await self._call("jarvis_bridge:jarvis_status", {}, timeout=15.0)
        except Exception as e:  # noqa: BLE001
            return f"JARVIS could not be reached to check the kill switch ({str(e)[:80]}); nothing was run"
        if st.get("ready") is not True:              # jarvis_status: ready is false while the kill switch is on
            return f"{screen_text.clean_message(st.get('message') or 'JARVIS is not ready', 160)} Nothing was run."
        return ""

    def _sync_researcher(self, loop: asyncio.AbstractEventLoop):
        def research(question: str) -> dict[str, Any]:
            fut = asyncio.run_coroutine_threadsafe(self._call("research:research_topic", {
                "question": question[:600], "depth": "quick", "deliverable": "none"}, timeout=400.0), loop)
            return fut.result(timeout=420)
        return research

    # ---------------------------------------------------------------- client projects
    @ui.action(id="create_project", label=tr("actions.create_project.label", default="New client project"), tone="primary")
    @plugin_entry(
        id="create_project", name=tr("entry.create_project.name", default="Start a client project"),
        description=tr("entry.create_project.description", default=(
            "Start a client project folder in JARVIS's Workbench. Give a project name, the client's name and the owner's "
            "own name(s) as they appear in chats, so JARVIS can tell the owner's promises from the client's requests.")),
        input_schema={"type": "object", "properties": {"name": STR, "client": STR,
                                                       "owner_names": {"type": "array", "items": STR}}, "required": ["name"]},
        timeout=20, llm_result_fields=["project_id", "policy"])
    async def create_project(self, name: str = "", client: str = "", owner_names: list | None = None, **_):
        try:
            return Ok(await asyncio.to_thread(service.create_project, name, client, list(owner_names or [])))
        except projects.ProjectError as e:
            return _err(e)

    @ui.action(id="add_project_files", label=tr("actions.add_project_files.label", default="Add files"), group="project")
    @plugin_entry(
        id="add_project_files", name=tr("entry.add_project_files.name", default="Add files to a client project"),
        description=tr("entry.add_project_files.description", default=(
            "Copy a client's files into a project: audio recordings, transcripts, chat exports (WhatsApp, Telegram, text), "
            "PDF, Word, Markdown, CSV, spreadsheets and images. Full paths; only the folders JARVIS may read. "
            "`languages` maps an audio file's path to en, hi or te.")),
        input_schema={"type": "object", "properties": {"project_id": STR, "paths": {"type": "array", "items": STR},
                                                       "languages": {"type": "object"}}, "required": ["project_id", "paths"]},
        timeout=120, llm_result_fields=["added", "refused"])
    async def add_project_files(self, project_id: str = "", paths: list | None = None, languages: dict | None = None, **_):
        try:
            return Ok(await asyncio.to_thread(service.add_files, project_id, list(paths or []), languages or {}))
        except projects.ProjectError as e:
            return _err(e)

    @ui.action(id="analyze_project", label=tr("actions.analyze_project.label", default="Understand the client's needs"), tone="primary")
    @plugin_entry(
        id="analyze_project", name=tr("entry.analyze_project.name", default="Understand what the client needs"),
        description=tr("entry.analyze_project.description", default=(
            "Read every file of a client project (transcribing audio on this PC), then write requirements that cite where "
            "each one was said, list conflicting instructions and open questions, and make a plan with a prompt per task. "
            "Text in the files that tries to instruct an AI is ignored and listed.")),
        input_schema={"type": "object", "properties": {"project_id": STR, "use_local_model": {"type": "boolean", "default": True}},
                      "required": ["project_id"]},
        timeout=1800, llm_result_fields=["stats", "deliverables", "conflicts", "open_questions", "files"])
    async def analyze_project(self, project_id: str = "", use_local_model: bool = True, **_):
        ok, _why = await asyncio.to_thread(localmodel.available) if use_local_model else (False, "")
        running = await self._running()
        try:
            return Ok(await asyncio.to_thread(service.analyze, project_id, None, localmodel.suggest if ok else None, running))
        except projects.ProjectError as e:
            return _err(e)

    @ui.action(id="answer_questions", label=tr("actions.answer_questions.label", default="Answer questions"), group="project")
    @plugin_entry(
        id="answer_questions", name=tr("entry.answer_questions.name", default="Answer a project's open questions"),
        description=tr("entry.answer_questions.description", default=(
            "Give the owner's answers to a project's open questions ({\"Q1\": \"answer\"}); requirements, conflicts and "
            "the plan are rebuilt with them.")),
        input_schema={"type": "object", "properties": {"project_id": STR, "answers": {"type": "object"}},
                      "required": ["project_id", "answers"]},
        timeout=600, llm_result_fields=["stats", "open_questions", "conflicts"])
    async def answer_questions(self, project_id: str = "", answers: dict | None = None, **_):
        try:
            return Ok(await asyncio.to_thread(service.answer_questions, project_id, dict(answers or {}), None, await self._running()))
        except projects.ProjectError as e:
            return _err(e)

    @ui.action(id="build_deliverables", label=tr("actions.build_deliverables.label", default="Make and check deliverables"))
    @plugin_entry(
        id="build_deliverables", name=tr("entry.build_deliverables.name", default="Make the client's deliverables"),
        description=tr("entry.build_deliverables.description", default=(
            "Make the deliverables JARVIS can make itself (documents, briefs, outlines, message drafts, sheets) with the "
            "local model, then check each one against the requirements. Says which are verified and which need review. "
            "Code, websites and research wait for the owner to run their prompts.")),
        input_schema={"type": "object", "properties": {"project_id": STR}, "required": ["project_id"]},
        timeout=3600, llm_result_fields=["deliverables", "verified", "needs_review", "files"])
    async def build_deliverables(self, project_id: str = "", **_):
        ok, why = await asyncio.to_thread(localmodel.available)
        try:
            return Ok(await asyncio.to_thread(service.build, project_id, localmodel.writer if ok else None, why))
        except projects.ProjectError as e:
            return _err(e)

    @ui.action(id="project_status", label=tr("actions.project_status.label", default="Project status"), group="project")
    @plugin_entry(
        id="project_status", name=tr("entry.project_status.name", default="Client project status"),
        description=tr("entry.project_status.description", default="Sources, open questions, deliverables and prompts of a client project."),
        input_schema={"type": "object", "properties": {"project_id": STR}, "required": ["project_id"]},
        timeout=20, llm_result_fields=["stats", "open_questions", "deliverables"])
    async def project_status(self, project_id: str = "", **_):
        try:
            return Ok(await asyncio.to_thread(service.status, project_id))
        except projects.ProjectError as e:
            return _err(e)

    @ui.action(id="set_provider_policy", label=tr("actions.set_provider_policy.label", default="AI provider policy"),
               confirm="Client material may then go to the provider you name. Continue?")
    @plugin_entry(
        id="set_provider_policy", name=tr("entry.set_provider_policy.name", default="Set a project's AI provider policy"),
        description=tr("entry.set_provider_policy.description", default=(
            "Owner only (panel). local_only (default) or external_allowed with named providers and confirmed_by_owner.")),
        input_schema={"type": "object", "properties": {"project_id": STR, "mode": STR, "providers": {"type": "array", "items": STR},
                                                       "confirmed_by_owner": {"type": "boolean"}}, "required": ["project_id", "mode"]},
        timeout=20)
    async def set_provider_policy(self, project_id: str = "", mode: str = "", providers: list | None = None,
                                  confirmed_by_owner: bool = False, **_):
        try:
            return Ok(await asyncio.to_thread(service.set_provider_policy, project_id, mode, list(providers or []),
                                              confirmed_by_owner is True))
        except projects.ProjectError as e:
            return _err(e)

    # ---------------------------------------------------------------- prompt generator
    @ui.action(id="generate_prompt", label=tr("actions.generate_prompt.label", default="Generate a prompt"), tone="primary")
    @plugin_entry(
        id="generate_prompt", name=tr("entry.generate_prompt.name", default="Generate a structured prompt"),
        description=tr("entry.generate_prompt.description", default=(
            "Write a structured prompt from a plain-language objective: role, source context, requirements and "
            "exclusions, tools, steps, security boundaries, deliverables, acceptance criteria and failure handling. "
            "kind: single, multi_agent, research, coding or client_deliverable. It is saved as a suggestion; nothing runs.")),
        input_schema={"type": "object", "properties": {
            "objective": STR, "kind": {"type": "string", "enum": list(prompts.KINDS)}, "target": {"type": "object"},
            "source_paths": {"type": "array", "items": STR}, "history": {"type": "array", "items": {"type": "object"}},
            "constraints": {"type": "array", "items": STR}, "exclusions": {"type": "array", "items": STR},
            "budget": STR, "deadline": STR, "output_format": STR, "acceptance": {"type": "array", "items": STR},
            "project_id": STR}, "required": ["objective"]},
        timeout=60, llm_result_fields=["id", "status", "text", "withheld"])
    async def generate_prompt(self, objective: str = "", kind: str = "single", source_paths: list | None = None, **kw):
        spec = {k: v for k, v in kw.items() if k in ("target", "history", "constraints", "exclusions", "budget", "deadline",
                                                     "output_format", "acceptance", "project_id")}
        spec.update(objective=objective, kind=kind)
        try:
            return Ok(await asyncio.to_thread(service.generate_prompt, spec, list(source_paths or [])))
        except (prompts.PromptError, projects.ProjectError) as e:
            return _err(e)

    @ui.action(id="get_prompt", label=tr("actions.get_prompt.label", default="Open prompt"), group="prompt")
    @plugin_entry(
        id="get_prompt", name=tr("entry.get_prompt.name", default="Show a saved prompt"),
        description=tr("entry.get_prompt.description", default="A saved prompt (a version, or the current one) with its status and executions."),
        input_schema={"type": "object", "properties": {"prompt_id": STR, "project_id": STR, "version": {"type": "integer"}},
                      "required": ["prompt_id"]}, timeout=20, llm_result_fields=["id", "version", "status", "text"])
    async def get_prompt(self, prompt_id: str = "", project_id: str = "", version: int | None = None, **_):
        try:
            return Ok(await asyncio.to_thread(service.get_prompt, prompt_id, project_id, version))
        except (prompts.PromptError, projects.ProjectError) as e:
            return _err(e)

    @ui.action(id="edit_prompt", label=tr("actions.edit_prompt.label", default="Save edit"), group="prompt")
    @plugin_entry(
        id="edit_prompt", name=tr("entry.edit_prompt.name", default="Edit a saved prompt"),
        description=tr("entry.edit_prompt.description", default="Save the owner's edit as a new version (whole text, or named sections)."),
        input_schema={"type": "object", "properties": {"prompt_id": STR, "project_id": STR, "text": STR, "sections": {"type": "object"}},
                      "required": ["prompt_id"]}, timeout=20, llm_result_fields=["id", "version"])
    async def edit_prompt(self, prompt_id: str = "", project_id: str = "", text: str | None = None, sections: dict | None = None, **_):
        try:
            return Ok(await asyncio.to_thread(service.edit_prompt, prompt_id, project_id, text, sections))
        except (prompts.PromptError, projects.ProjectError) as e:
            return _err(e)

    @ui.action(id="regenerate_prompt", label=tr("actions.regenerate_prompt.label", default="Regenerate"), group="prompt")
    @plugin_entry(
        id="regenerate_prompt", name=tr("entry.regenerate_prompt.name", default="Regenerate a prompt"),
        description=tr("entry.regenerate_prompt.description", default="Rebuild a saved prompt, optionally with changed fields; saved as a new version."),
        input_schema={"type": "object", "properties": {"prompt_id": STR, "project_id": STR, "changes": {"type": "object"}},
                      "required": ["prompt_id"]}, timeout=30, llm_result_fields=["id", "version"])
    async def regenerate_prompt(self, prompt_id: str = "", project_id: str = "", changes: dict | None = None, **_):
        try:
            return Ok(await asyncio.to_thread(service.regenerate_prompt, prompt_id, project_id, changes or {}))
        except (prompts.PromptError, projects.ProjectError) as e:
            return _err(e)

    @ui.action(id="execute_prompt", label=tr("actions.execute_prompt.label", default="Run this prompt"), tone="danger",
               confirm="Hand this prompt to JARVIS to run? Its approvals still apply.")
    @plugin_entry(
        id="execute_prompt", name=tr("entry.execute_prompt.name", default="Run a saved prompt"),
        description=tr("entry.execute_prompt.description", default=(
            "Owner only (panel). Hands the prompt's compact form to main_agent or JARVIS (kernel approvals apply). "
            "Needs confirm=true; refused while the kill switch is on.")),
        input_schema={"type": "object", "properties": {"prompt_id": STR, "project_id": STR,
                                                       "via": {"type": "string", "enum": ["auto", "main_agent", "jarvis"]},
                                                       "confirm": {"type": "boolean"}}, "required": ["prompt_id", "confirm"]},
        timeout=60, llm_result_fields=["state", "route", "ref", "message"])
    async def execute_prompt(self, prompt_id: str = "", project_id: str = "", via: str = "auto", confirm: bool = False, **_):
        if confirm is not True:
            return Err(SdkError("Running a prompt needs the owner's confirmation (confirm=true from the panel)."))
        try:
            req = await asyncio.to_thread(service.execution_request, prompt_id, project_id, via)
            project = projects.Project(project_id) if project_id else None
        except (prompts.PromptError, projects.ProjectError) as e:
            return _err(e)
        gate = await self._jarvis_gate()
        if gate:
            await asyncio.to_thread(prompts.record_execution, prompt_id, project, req["route"], "", "refused")
            return Ok({"state": "refused", "route": req["route"], "message": gate})
        try:
            if req["route"] == "jarvis":
                got = await self._call("jarvis_bridge:delegate_task", {"task": req["text"]}, timeout=30.0)
                ref = str(got.get("command_id") or "")
            else:
                got = await self._call("main_agent:run_task", {"task": req["text"], "mode": "work"}, timeout=30.0)
                ref = str(got.get("run_id") or got.get("id") or "")
        except Exception as e:  # noqa: BLE001
            await asyncio.to_thread(prompts.record_execution, prompt_id, project, req["route"], "", "failed")
            return Ok({"state": "failed", "route": req["route"], "message": screen_text.clean_message(str(e), 200)})
        row = await asyncio.to_thread(prompts.record_execution, prompt_id, project, req["route"], ref, "submitted")
        return Ok({"state": "submitted", "route": req["route"], "ref": ref, "version": row["version"],
                   "message": "Submitted, not done: check the result with "
                              + ("jarvis_bridge task_result." if req["route"] == "jarvis" else "main_agent run_status.")})

    # ---------------------------------------------------------------- learning
    @ui.action(id="learn_task", label=tr("actions.learn_task.label", default="Learn a new task"))
    @plugin_entry(
        id="learn_task", name=tr("entry.learn_task.name", default="Learn a task JARVIS does not know yet"),
        description=tr("entry.learn_task.description", default=(
            "Analyse an unfamiliar task, research it (official sources first), plan it, draft a workflow, test it in a "
            "sandbox on example cases, refine it (Propose-Probe-Compose) and keep it as a skill only if every case passes. "
            "`examples`: [{name, inputs: {file: text}, checks: [{check, file, text}]}]. Nothing real runs here.")),
        input_schema={"type": "object", "properties": {"task": STR, "examples": {"type": "array", "items": {"type": "object"}},
                                                       "workflow": {"type": "object"}, "research": {"type": "boolean", "default": True},
                                                       "k": {"type": "integer", "default": 2},
                                                       "mode": {"type": "string", "enum": ["parallel", "sequential"]}},
                      "required": ["task"]},
        timeout=1800, llm_result_fields=["status", "why", "familiarity", "workflow", "approvals_needed", "plan"])
    async def learn_task(self, task: str = "", examples: list | None = None, workflow: dict | None = None,
                         research: bool = True, k: int = 2, mode: str = "parallel", **_):
        loop = asyncio.get_running_loop()
        running = await self._running() or set()
        ok, _ = await asyncio.to_thread(localmodel.available)
        researcher = self._sync_researcher(loop) if research and "research" in running else None
        repo_lookup = None
        if research:                                    # public GitHub facts for repositories the task names (no login)
            from plugin.plugins.research.sources import github_repo as repo_lookup
        try:
            return Ok(await asyncio.to_thread(
                service.learn_task, task, list(examples or []), workflow, researcher=researcher,
                drafter=localmodel.drafter if ok else None,
                proposer=localmodel.CombinedProposer(localmodel.ModelProposer() if ok else None), repo_lookup=repo_lookup,
                k=int(k or 2), mode=mode if mode in ("parallel", "sequential") else "parallel"))
        except (ValueError, library.LibraryError) as e:
            return _err(e)

    @ui.action(id="list_learned", label=tr("actions.list_learned.label", default="Learned skills"), group="learn")
    @plugin_entry(
        id="list_learned", name=tr("entry.list_learned.name", default="What JARVIS has learned"),
        description=tr("entry.list_learned.description", default="Validated workflows (skills) and recent lessons."),
        input_schema={"type": "object", "properties": {"query": STR}}, timeout=20, llm_result_fields=["workflows", "lessons"])
    async def list_learned(self, query: str = "", **_):
        return Ok({"workflows": await asyncio.to_thread(library.list_workflows),
                   "lessons": await asyncio.to_thread(library.lessons, query, 10)})

    @ui.action(id="run_learned_workflow", label=tr("actions.run_learned_workflow.label", default="Run a learned workflow"),
               tone="danger", confirm="Hand this workflow to JARVIS to run on real data? Its approvals still apply.")
    @plugin_entry(
        id="run_learned_workflow", name=tr("entry.run_learned_workflow.name", default="Run a learned workflow"),
        description=tr("entry.run_learned_workflow.description", default=(
            "Owner only (panel). Hands a validated workflow to JARVIS (kernel approvals, audit, kill switch). confirm=true.")),
        input_schema={"type": "object", "properties": {"name": STR, "input_note": STR, "confirm": {"type": "boolean"}},
                      "required": ["name", "input_note", "confirm"]},
        timeout=60, llm_result_fields=["state", "command_id", "message"])
    async def run_learned_workflow(self, name: str = "", input_note: str = "", confirm: bool = False, **_):
        if confirm is not True:
            return Err(SdkError("Running a workflow needs the owner's confirmation (confirm=true from the panel)."))
        try:
            req = await asyncio.to_thread(service.workflow_run_text, name, input_note)
        except library.LibraryError as e:
            return _err(e)
        gate = await self._jarvis_gate()
        if gate:
            return Ok({"state": "refused", "message": gate})
        try:
            got = await self._call("jarvis_bridge:delegate_task", {"task": req["text"]}, timeout=30.0)
        except Exception as e:  # noqa: BLE001
            return Ok({"state": "failed", "message": screen_text.clean_message(str(e), 200)})
        return Ok({"state": "submitted", "command_id": got.get("command_id"), "workflow": req["name"], "version": req["version"],
                   "message": "Submitted, not done. When JARVIS reports the output file, check it with check_workflow_output."})

    @ui.action(id="check_workflow_output", label=tr("actions.check_workflow_output.label", default="Check output"), group="learn")
    @plugin_entry(
        id="check_workflow_output", name=tr("entry.check_workflow_output.name", default="Check a learned workflow's output"),
        description=tr("entry.check_workflow_output.description", default=(
            "Apply a learned workflow's success checks to the files a real run produced (full paths). Only then is the run verified.")),
        input_schema={"type": "object", "properties": {"name": STR, "output_paths": {"type": "array", "items": STR}},
                      "required": ["name", "output_paths"]},
        timeout=60, llm_result_fields=["verified", "why"])
    async def check_workflow_output(self, name: str = "", output_paths: list | None = None, **_):
        try:
            return Ok(await asyncio.to_thread(service.verify_run_outputs, name, list(output_paths or [])))
        except library.LibraryError as e:
            return _err(e)

    # ---------------------------------------------------------------- prompt versions
    async def _svc(self, fn, *args, errors=(prompts.PromptError, projects.ProjectError, library.LibraryError, ValueError)):
        try:
            return Ok(await asyncio.to_thread(fn, *args))
        except errors as e:
            return _err(e)

    @ui.action(id="rename_prompt", label=tr("actions.rename_prompt.label", default="Rename"), group="prompt")
    @plugin_entry(id="rename_prompt", name=tr("entry.rename_prompt.name", default="Rename a prompt"),
                  description=tr("entry.rename_prompt.description", default="Give a saved prompt a title."),
                  input_schema={"type": "object", "properties": {"prompt_id": STR, "project_id": STR, "title": STR},
                                "required": ["prompt_id", "title"]}, timeout=20, llm_result_fields=["id", "title"])
    async def rename_prompt(self, prompt_id: str = "", project_id: str = "", title: str = "", **_):
        return await self._svc(service.rename_prompt, prompt_id, project_id, title)

    @ui.action(id="duplicate_prompt", label=tr("actions.duplicate_prompt.label", default="Duplicate"), group="prompt")
    @plugin_entry(id="duplicate_prompt", name=tr("entry.duplicate_prompt.name", default="Duplicate a prompt"),
                  description=tr("entry.duplicate_prompt.description", default="Copy a saved prompt's current version into a new prompt."),
                  input_schema={"type": "object", "properties": {"prompt_id": STR, "project_id": STR}, "required": ["prompt_id"]},
                  timeout=20, llm_result_fields=["id", "version"])
    async def duplicate_prompt(self, prompt_id: str = "", project_id: str = "", **_):
        return await self._svc(service.duplicate_prompt, prompt_id, project_id)

    @ui.action(id="compare_prompt", label=tr("actions.compare_prompt.label", default="Compare versions"), group="prompt")
    @plugin_entry(id="compare_prompt", name=tr("entry.compare_prompt.name", default="Compare prompt versions"),
                  description=tr("entry.compare_prompt.description", default="What changed between two versions of a prompt, and why (how each was made)."),
                  input_schema={"type": "object", "properties": {"prompt_id": STR, "project_id": STR, "a": {"type": "integer"},
                                                                 "b": {"type": "integer"}}, "required": ["prompt_id"]},
                  timeout=20, llm_result_fields=["summary", "diff"])
    async def compare_prompt(self, prompt_id: str = "", project_id: str = "", a: int = 1, b: int | None = None, **_):
        return await self._svc(service.compare_prompt, prompt_id, project_id, a, b)

    @ui.action(id="export_prompt", label=tr("actions.export_prompt.label", default="Export"), group="prompt")
    @plugin_entry(id="export_prompt", name=tr("entry.export_prompt.name", default="Export a prompt"),
                  description=tr("entry.export_prompt.description", default="Write a prompt version to a Markdown file with its status, target and provenance."),
                  input_schema={"type": "object", "properties": {"prompt_id": STR, "project_id": STR, "version": {"type": "integer"}},
                                "required": ["prompt_id"]}, timeout=20, llm_result_fields=["file", "version"])
    async def export_prompt(self, prompt_id: str = "", project_id: str = "", version: int | None = None, **_):
        return await self._svc(service.export_prompt, prompt_id, project_id, version)

    # ---------------------------------------------------------------- skill lifecycle
    @ui.action(id="search_skills", label=tr("actions.search_skills.label", default="Find skills"), group="learn")
    @plugin_entry(id="search_skills", name=tr("entry.search_skills.name", default="Find learned or imported skills"),
                  description=tr("entry.search_skills.description", default="Search the learned skills (with their state) and imported, unverified skill files."),
                  input_schema={"type": "object", "properties": {"query": STR}}, timeout=20, llm_result_fields=["skills"])
    async def search_skills(self, query: str = "", **_):
        return await self._svc(lambda q: {"skills": skillhub.search(q)}, query)

    @ui.action(id="inspect_skill", label=tr("actions.inspect_skill.label", default="Inspect"), group="learn")
    @plugin_entry(id="inspect_skill", name=tr("entry.inspect_skill.name", default="Inspect a learned skill"),
                  description=tr("entry.inspect_skill.description", default="A skill's steps, validation, provenance, state, versions, permissions and usage numbers."),
                  input_schema={"type": "object", "properties": {"name": STR}, "required": ["name"]}, timeout=20,
                  llm_result_fields=["name", "version", "state", "metrics", "validation"])
    async def inspect_skill(self, name: str = "", **_):
        return await self._svc(skillhub.inspect, name)

    @ui.action(id="reuse_skill", label=tr("actions.reuse_skill.label", default="Try on new input (sandbox)"), group="learn")
    @plugin_entry(id="reuse_skill", name=tr("entry.reuse_skill.name", default="Try a learned skill on new input"),
                  description=tr("entry.reuse_skill.description", default=(
                      "Run a validated, active skill in the sandbox on new input text ({file name: text}) and check the result. "
                      "No real files, nothing sent.")),
                  input_schema={"type": "object", "properties": {"name": STR, "inputs": {"type": "object"}}, "required": ["name", "inputs"]},
                  timeout=120, llm_result_fields=["ok", "outputs", "checks"])
    async def reuse_skill(self, name: str = "", inputs: dict | None = None, **_):
        return await self._svc(service.reuse_skill, name, dict(inputs or {}))

    @ui.action(id="regress_skill", label=tr("actions.regress_skill.label", default="Re-test"), group="learn")
    @plugin_entry(id="regress_skill", name=tr("entry.regress_skill.name", default="Re-test a learned skill"),
                  description=tr("entry.regress_skill.description", default="Run a skill's own and regression cases again; a failure quarantines it."),
                  input_schema={"type": "object", "properties": {"name": STR}, "required": ["name"]}, timeout=300,
                  llm_result_fields=["passed", "failed_cases"])
    async def regress_skill(self, name: str = "", **_):
        return await self._svc(skillhub.regress, name)

    @ui.action(id="disable_skill", label=tr("actions.disable_skill.label", default="Disable"), group="learn")
    @plugin_entry(id="disable_skill", name=tr("entry.disable_skill.name", default="Disable a learned skill"),
                  description=tr("entry.disable_skill.description", default="Stop a skill from being used or run (it stays stored; enable it again later)."),
                  input_schema={"type": "object", "properties": {"name": STR, "reason": STR}, "required": ["name"]}, timeout=20,
                  llm_result_fields=["status", "reason"])
    async def disable_skill(self, name: str = "", reason: str = "", **_):
        return await self._svc(skillhub.disable, name, reason)

    @ui.action(id="enable_skill", label=tr("actions.enable_skill.label", default="Enable"), group="learn",
               confirm="Allow this skill to be used again?")
    @plugin_entry(id="enable_skill", name=tr("entry.enable_skill.name", default="Enable a learned skill"),
                  description=tr("entry.enable_skill.description", default="Owner only (panel). After a quarantine it must pass its regression cases first."),
                  input_schema={"type": "object", "properties": {"name": STR, "confirm": {"type": "boolean"}}, "required": ["name", "confirm"]},
                  timeout=300, llm_result_fields=["status"])
    async def enable_skill(self, name: str = "", confirm: bool = False, **_):
        if confirm is not True:
            return Err(SdkError("Enabling a skill needs the owner's confirmation (confirm=true from the panel)."))
        return await self._svc(skillhub.enable, name)

    @ui.action(id="rollback_skill", label=tr("actions.rollback_skill.label", default="Roll back"), group="learn",
               confirm="Make this older validated version the current one?")
    @plugin_entry(id="rollback_skill", name=tr("entry.rollback_skill.name", default="Roll a skill back to an older version"),
                  description=tr("entry.rollback_skill.description", default="Owner only (panel). Only to a version that was validated."),
                  input_schema={"type": "object", "properties": {"name": STR, "version": STR, "confirm": {"type": "boolean"}},
                                "required": ["name", "version", "confirm"]}, timeout=20, llm_result_fields=["current", "previous"])
    async def rollback_skill(self, name: str = "", version: str = "", confirm: bool = False, **_):
        if confirm is not True:
            return Err(SdkError("Rolling back needs the owner's confirmation (confirm=true from the panel)."))
        return await self._svc(skillhub.rollback, name, version)

    @ui.action(id="export_skill", label=tr("actions.export_skill.label", default="Export"), group="learn")
    @plugin_entry(id="export_skill", name=tr("entry.export_skill.name", default="Export a learned skill"),
                  description=tr("entry.export_skill.description", default="One JSON bundle: workflow, cases, SKILL.md, validation, provenance, state, usage."),
                  input_schema={"type": "object", "properties": {"name": STR}, "required": ["name"]}, timeout=20,
                  llm_result_fields=["file", "sha256"])
    async def export_skill(self, name: str = "", **_):
        return await self._svc(skillhub.export, name)

    @ui.action(id="import_skill", label=tr("actions.import_skill.label", default="Import a SKILL.md"), group="learn",
               confirm="Import this skill file as UNTRUSTED (it will not be activated)?")
    @plugin_entry(id="import_skill", name=tr("entry.import_skill.name", default="Import an external SKILL.md (untrusted)"),
                  description=tr("entry.import_skill.description", default=(
                      "Owner only (panel). Reads a SKILL.md from an allowed folder, hashes and rates it, lists its links and scripts, "
                      "and keeps it quarantined. It is never activated here.")),
                  input_schema={"type": "object", "properties": {"path": STR, "confirm": {"type": "boolean"}}, "required": ["path", "confirm"]},
                  timeout=30, llm_result_fields=["name", "status", "gate", "findings"])
    async def import_skill(self, path: str = "", confirm: bool = False, **_):
        if confirm is not True:
            return Err(SdkError("Importing a skill needs the owner's confirmation (confirm=true from the panel)."))
        return await self._svc(skillhub.import_skill_md, path)

    # ---------------------------------------------------------------- briefing
    @ui.action(id="briefing", label=tr("actions.briefing.label", default="Briefing"), group="briefing")
    @plugin_entry(id="briefing", name=tr("entry.briefing.name", default="Briefing: what is pending"),
                  description=tr("entry.briefing.description", default=(
                      "What is waiting: JARVIS approvals, client projects' open questions, conflicts, deadlines as the client "
                      "stated them, deliverables that need review, prompts submitted but not verified, quarantined skills. "
                      "Facts are read from records and name their source; suggestions are marked as suggestions.")),
                  input_schema={"type": "object", "properties": {}}, timeout=60, llm_result_fields=["text"])
    async def briefing(self, **_):
        jarvis: dict = {}
        try:
            st = await self._call("jarvis_bridge:jarvis_status", {}, timeout=15.0)
            ap = await self._call("jarvis_bridge:pending_approvals", {}, timeout=15.0)
            jarvis = {"ready": st.get("ready"), "message": st.get("message", ""),
                      "approvals_waiting": ap.get("count", len(ap.get("approvals") or []))}
        except Exception as e:  # noqa: BLE001 - reported in the briefing as "not read", never guessed
            jarvis = {"error": f"not reachable ({str(e)[:60]})"}
        return await self._svc(service.briefing, jarvis)

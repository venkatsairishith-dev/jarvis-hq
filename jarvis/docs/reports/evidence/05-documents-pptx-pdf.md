# 05: Documents (PPTX and PDF) evidence report

Research date: 2026-10-16. Scope: category 9 (AI PowerPoint generation) and category 10 (PDF and document generation and conversion) for Neko on the owner's Windows 10 laptop. This was research only. Nothing was downloaded, installed or executed, apart from read-only checks of local package lists and browser paths.

Labels used below:
- **VERIFIED**: read in source code, an official spec or docs, a package registry, a release page or an issue tracker.
- **CLAIM**: stated in a README, marketing page or third-party write-up and not checked against code.
- **UNVERIFIED**: my own knowledge or inference. Test it before relying on it.

## 0. Local baseline (VERIFIED by read-only local checks)

| Item | State on this laptop |
|---|---|
| Existing plugin `plugin/plugins/doc_tools/core.py` | Reads PDF (pypdf), DOCX (python-docx), HTML (bs4) and text. Writes only `md`, `txt` and `docx` (`FORMATS = ("md", "txt", "docx")`). The DOCX writer maps `#` headings and bullets only: no tables, images, PPTX or PDF output. Output goes to `D:\jarvis\outputs\neko` and never overwrites. |
| Neko venv `D:\jarvis\tools\neko-venv` (Python 3.11.17) | Already has playwright 1.63.0, pypdf 6.19.0, reportlab 4.4.9, matplotlib 3.10.7, python-docx 1.2.0, lxml 6.1.3, pillow 12.3.0 and fonttools 4.61.0. It does **not** have python-pptx, pdfplumber or pypdfium2. |
| Project pins (`requirements.txt`) | playwright==1.63.0, pypdf==6.19.0, reportlab==4.4.9, python-docx==1.2.0, lxml==6.1.3, matplotlib==3.10.7, markdown-it-py, beautifulsoup4 |
| Browsers | Edge at `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` and Chrome at `C:\Program Files\Google\Chrome\Application\chrome.exe`. No Playwright-managed browser in `%LOCALAPPDATA%\ms-playwright`. |
| Node | v24.19.0. The only global npm package is vercel. |
| Office suites | No PowerPoint and no LibreOffice (owner context) |

---

## 1. PPTX generators

### 1.1 Comparison table

| Tool | URL | Licence | Latest release | Size / deps | Windows / offline | Native charts / tables / notes | Transitions / animations |
|---|---|---|---|---|---|---|---|
| **python-pptx** | https://github.com/scanny/python-pptx | MIT (VERIFIED, [PyPI JSON](https://pypi.org/pypi/python-pptx/json)) | 1.0.2, files uploaded 2024-08-07 (VERIFIED, PyPI). No release in about 26 months. | Pure Python. Needs Pillow, XlsxWriter, lxml and typing-extensions (VERIFIED, PyPI requires_dist). | Yes, fully offline | Charts (column, bar, line, pie and more), tables and notes slides are documented (VERIFIED, [docs](https://python-pptx.readthedocs.io/en/latest/)) | **No API.** The docs do not mention transitions or animations ([docs](https://python-pptx.readthedocs.io/en/latest/)). Requests are open: [#400](https://github.com/scanny/python-pptx/issues/400), [#264](https://github.com/scanny/python-pptx/issues/264), [#1106](https://github.com/scanny/python-pptx/issues/1106), [#942 morph](https://github.com/scanny/python-pptx/issues/942), [#1004](https://github.com/scanny/python-pptx/issues/1004). Raw XML injection through lxml is possible (see 1.3). |
| **PptxGenJS** | https://github.com/gitbrent/PptxGenJS | MIT (VERIFIED, [npm registry](https://registry.npmjs.org/pptxgenjs/latest)) | 4.0.1 (VERIFIED, npm). The release page shows 26 Jun with no year. | About 2.6 MB unpacked. Deps: jszip, image-size, https (VERIFIED, npm). | Node, offline | **Native charts with an embedded Excel workbook**: `zip.file('ppt/embeddings/Microsoft_Excel_Worksheet${id}.xlsx' ...)` plus `<c:chartSpace>` (VERIFIED, [src/gen-charts.ts](https://raw.githubusercontent.com/gitbrent/PptxGenJS/master/src/gen-charts.ts)). Tables, masters and RTL are documented ([site](https://gitbrent.github.io/PptxGenJS/)). Notes via `slide.addNotes()` (VERIFIED, [docs](https://gitbrent.github.io/PptxGenJS/docs/speaker-notes/)). | **None.** `src/gen-xml.ts` contains no `p:transition`, `p:timing` or "transition" (VERIFIED by reading [the source](https://raw.githubusercontent.com/gitbrent/PptxGenJS/master/src/gen-xml.ts)). The maintainer wrote in 2023: "this feature isn't requested enough to warrant coding" ([#54](https://github.com/gitbrent/PptxGenJS/issues/54)). Animation PR [#1431](https://github.com/gitbrent/PptxGenJS/pull/1431) has been open since Nov 2025 and is unmerged. |
| pptxgenjs-plus (fork) | https://github.com/lofcz/pptxgenjs-plus | MIT (VERIFIED, [npm registry](https://registry.npmjs.org/pptxgenjs-plus)) | 4.3.4, 2026-09-24 (VERIFIED). Package created 2026-08-15. | Fork, 19 stars | Node | Same base as PptxGenJS | A comment on PR #1431 says the fork includes the animation PR (CLAIM). The fork README does not mention animations or transitions. Young, single-maintainer fork. |
| **OfficeCLI** | https://github.com/iOfficeAI/OfficeCLI | Apache-2.0 (VERIFIED, [LICENSE](https://raw.githubusercontent.com/iOfficeAI/OfficeCLI/main/LICENSE)) | v1.0.157, 12 Oct (2026 inferred). Releases are frequent: v1.0.155 to v1.0.157 came out 6 to 12 Oct ([releases](https://github.com/iOfficeAI/OfficeCLI/releases)). | **Single self-contained exe: `officecli-win-x64.exe` 32 MB** (VERIFIED, [assets](https://github.com/iOfficeAI/OfficeCLI/releases/expanded_assets/v1.0.157)). Built with .NET, but the release binary is self-contained (CLAIM). | Windows x64 and ARM64 builds. Works offline, though it checks for updates in the background unless that is disabled in config (CLAIM). "no Office required" (CLAIM). | Charts, tables with a built-in style catalogue, and notes are listed (CLAIM, README). There are open chart bugs, mostly for scatter charts plus one schema-invalid `c:dLbls` order bug ([#498–#503](https://github.com/iOfficeAI/OfficeCLI/issues)). | README: "morph + p14 + 12 p15 PowerPoint 2013+ presets" for transitions and "15 emphasis + 16 exit template-backed presets, multi-effect chains, motion-path presets" for animations (CLAIM). Entrance effects such as fade, fly and zoom work via `set ... --prop animation=fade` (CLAIM, third-party write-ups [instagit](https://instagit.com/iofficeai/OfficeCLI/how-to-implement-powerpoint-animations-and-transitions-with-officecli/) and [tessl morph skill](https://tessl.io/registry/skills/github/iOfficeAI/OfficeCLI/morph-ppt)). It also has `validate` ("Validate against OpenXML schema"), `view screenshot` (per-page PNG through a headless browser), `view html` and an MCP server (CLAIM, [README](https://raw.githubusercontent.com/iOfficeAI/OfficeCLI/main/README.md)). |
| md2pptx | https://github.com/MartinPacker/md2pptx | MIT for python-pptx, which it uses. md2pptx's own licence was not confirmed. | Active (user guide lists versions up to 7.x) | python-pptx | Offline | Markdown to PPTX with footnotes (on the slide or on end slides), notes, tables and chart helpers (VERIFIED, [user guide](https://raw.githubusercontent.com/MartinPacker/md2pptx/main/docs/user-guide.md)) | `transition:` metadata with ripple, reveal, honeycomb, shred, wipe, vortex, fracture, split and push. The guide says "A limited number have been successfully tested" (VERIFIED, user guide). No entrance animations were found. This is useful reference code for transition injection on top of python-pptx. |
| Aspose.Slides | https://docs.aspose.com/slides/python-net/slide-transition/ | Commercial (paid; evaluation adds watermark, UNVERIFIED) | n/a | Large .NET-based wheel | Offline | Full | Full transition, morph and animation API ([transitions](https://docs.aspose.com/slides/python-net/slide-transition/), [morph types](https://reference.aspose.com/slides/java/com.aspose.slides/TransitionMorphType)). **Noted only. Paid, so not recommended.** |
| docxtemplater | https://github.com/open-xml-templating/docxtemplater | Core is MIT. Charts, images, HTML and table modules are paid ([site](https://docxtemplater.com/), [jsDelivr](https://www.jsdelivr.com/package/npm/docxtemplater)). | 3.68.7 | Node | Offline | Fills `{tags}` in an existing pptx template | Keeps whatever animations the template already has (UNVERIFIED). Only useful if a designer-made template should be filled. |

### 1.2 AI deck generators (complete apps)

| Project | URL | Licence | Activity | Stack / deps | Offline | PPTX method and output | Animations | Fit for Neko |
|---|---|---|---|---|---|---|---|---|
| **Presenton** | https://github.com/presenton/presenton | Apache-2.0 (CLAIM, README) | v0.9.11-beta and electron-v0.9.11-beta, 21 Sep ([releases](https://github.com/presenton/presenton/releases)). About 11.1k stars. | FastAPI and Next.js, Electron desktop wrapper (Windows x64 .exe), Docker. The backend's `pyproject.toml` lists **python-pptx>=1.0.2**, pdfplumber and fonttools, with no Playwright or LibreOffice (VERIFIED, [pyproject](https://raw.githubusercontent.com/presenton/presenton/main/servers/fastapi/pyproject.toml)). | Ollama and LM Studio supported. Air-gapped mode needs `PRESENTON_COMMUNITY_ENABLED=false`. **Anonymous telemetry is on by default** (`DISABLE_ANONYMOUS_TRACKING`) (CLAIM, README). | Templates are HTML plus Tailwind and can be built from an existing PPTX. Exports "fully editable PPTX" and PDF (CLAIM). Has a REST `POST /api/v1/ppt/presentation/generate` and an `/mcp` endpoint. | Not mentioned anywhere (no evidence of transitions or animations) | Heavy: a second app stack with a DB, plus Electron. Possible as an optional "AI deck studio", but it does not meet the animation requirement and adds telemetry. |
| **PPTAgent / DeepPresenter** | https://github.com/icip-cas/PPTAgent | MIT | News up to 2026/09. About 5.1k stars. EMNLP 2025 and ACL 2026 papers. | Python 3.12, uv, Playwright/Chromium, Node. **LibreOffice required on PATH.** | "offline mode" claimed, but recommends a remote vision model for review (CLAIM) | Edits reference slides. PPTEval scores Content, Design and Coherence (CLAIM). | Not mentioned | **Not supported on Windows**: "Linux (including WSL) or macOS" (CLAIM, README). Its ideas (reference-template editing, render-then-VLM review) are worth copying conceptually. |
| slide-deck-ai | https://github.com/barun-saha/slide-deck-ai | MIT | 377 stars | Streamlit, LiteLLM, python-pptx | Ollama via `RUN_IN_OFFLINE_MODE` (images still need Pexels) (CLAIM) | python-pptx templates (CLAIM) | Not mentioned | Small. Shows the same python-pptx approach. |
| ALLWEONE presentation-ai (Gamma alternative) | https://github.com/allweonedev/presentation-ai | MIT | About 3.0k stars | Next.js, **PostgreSQL**, NextAuth with **Google OAuth** | Partial | The README marks PPTX export "Partially Done" and says it does not translate one to one. PDF export not started. (CLAIM) | No | Not a fit: needs a DB and a cloud login. |
| Anthropic `pptx` skill | https://github.com/anthropics/skills/tree/main/skills/pptx | **Proprietary, source-available**: no redistribution or derivative works (CLAIM via [aggregators](https://www.anavem.com/skills/claude/anthropic-pptx-skill). Read LICENSE.txt before any use.) | n/a | html2pptx, pptxgenjs, LibreOffice thumbnails (UNVERIFIED) | n/a | n/a | n/a | **Do not copy into Neko** (licence). |

### 1.3 Markdown and web slide tools that export PPTX

| Tool | URL | Licence | Latest | PPTX output | Notes |
|---|---|---|---|---|---|
| Marp CLI | https://github.com/marp-team/marp-cli | MIT | v4.5.1 (06 Sep) ([releases](https://github.com/marp-team/marp-cli/releases)) | Default PPTX "usually consists of pre-rendered background images", so it is **not editable**. `--pptx-editable` is experimental, "requires installing both of the browser and LibreOffice Impress", "may throw an error or output the incomplete result", has lower fidelity and **no presenter notes** (VERIFIED, [README](https://github.com/marp-team/marp-cli)). | Needs Chrome, Edge or Firefox. No transitions or animations in PPTX. |
| Slidev | https://sli.dev/guide/exporting | MIT | n/a | `--format pptx` exports every slide as an image, so "text will not be selectable". `pptx-editable` rebuilds shapes, but SVG, canvas and iframes stay pictures. Presenter notes are kept. Needs `playwright-chromium` (VERIFIED, [docs](https://sli.dev/guide/exporting)). | Good HTML slideshow, but its own animations do not become PowerPoint animations (UNVERIFIED). |

**Conclusion for generators:** no maintained open-source generator outputs native charts, notes **and** PowerPoint animations except OfficeCLI (CLAIM, young but very active). python-pptx and PptxGenJS are the mature, well-tested writers for layouts, charts, tables and notes, but both need XML post-processing for transitions and animations.

---

## 2. OOXML for transitions and entrance animations

### 2.1 Where they go (VERIFIED unless marked)

- `p:sld` children are, in order: `p:cSld`, `p:clrMapOvr`, `p:transition`, `p:timing`, `p:extLst`. This order is from ISO/IEC 29500 CT_Slide (UNVERIFIED: the docx4java spec mirror was unreachable because of an expired certificate). **Insert `p:transition` before `p:timing`, never by plain append.** A Japanese python-pptx tutorial appends at the end, which is the classic cause of the PowerPoint "repair" prompt (UNVERIFIED that it triggers repair; [tutorial](https://aurant-technologies.com/blog/blog-ppt-animation/)).
- `p:timing` has the children `tnLst`, `bldLst` and `extLst`, and its parents are `sld`, `sldLayout` and `sldMaster` (VERIFIED, [Open XML SDK Timing class](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.presentation.timing)).
- Office 2010 and later transitions (p14: vortex, switch, flip, ripple, honeycomb, prism, doors, window, ferris, gallery, conveyor, pan, glitter, warp, flythrough, flash, shred, reveal, wheelReverse), **morph** (p159) and `prstTrans` (p15) extend `p:transition`. They are stored inside an `mc:AlternateContent` child of `sld` with Choice namespaces for 2010 (`http://schemas.microsoft.com/office/powerpoint/2010/main`), 2012 (`.../2012/main`) and **2015/09 (`http://schemas.microsoft.com/office/powerpoint/2015/09/main`)**, plus a **Fallback `transition`**. The `p14:dur` attribute extends `transition` (VERIFIED, [MS-PPTX 2.2.1 Slide Transition Extensions](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-pptx/22ebe6b5-2ade-43d9-977a-98fa194725c2), [p14 schema](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-pptx/cc915728-85a4-46a0-a07c-eb5416a5762e)).
- `morph` is a `CT_MorphTransition` in the 2015/09 namespace. The sample uses `option="byObject"` ([MS-PPTX morph](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-pptx/68d26d78-f7f5-47ab-835d-4e6c82ff39f0)). Option values are byObject, byWord and byChar ([Aspose enum](https://reference.aspose.com/slides/java/com.aspose.slides/TransitionMorphType)). A PowerPoint-saved morph slide has a Choice (morph) and a Fallback (fade) ([python-pptx #942](https://github.com/scanny/python-pptx/issues/942)). Morph matching works best with duplicated objects, and the `!!name` prefix forces matches ([Microsoft support](https://support.microsoft.com/en-us/office/bc7f48ff-f152-4ee8-9081-d3121788024f)).

### 2.2 Templates (structure VERIFIED from the spec. The exact attribute set is UNVERIFIED and must be checked against a PowerPoint-saved sample or OfficeCLI output.)

Base-schema transition (no AlternateContent needed):
```xml
<p:transition spd="med"><p:fade/></p:transition>          <!-- or <p:push dir="u"/>, <p:wipe dir="r"/>, <p:cover/>, <p:split/> -->
<p:transition spd="slow" advTm="5000"><p:fade/></p:transition>  <!-- auto-advance after 5 s -->
```

Morph with fallback, placed between `p:clrMapOvr` and `p:timing`:
```xml
<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">
  <mc:Choice xmlns:p159="http://schemas.microsoft.com/office/powerpoint/2015/09/main" Requires="p159">
    <p:transition spd="slow" xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main" p14:dur="2000">
      <p159:morph option="byObject"/>
    </p:transition>
  </mc:Choice>
  <mc:Fallback>
    <p:transition spd="slow"><p:fade/></p:transition>
  </mc:Fallback>
</mc:AlternateContent>
```
(OfficeCLI's wrapper uses `Requires="p15"` according to a [third-party walkthrough](https://instagit.com/iofficeai/OfficeCLI/officecli-powerpoint-animation-morph-transitions/), which conflicts with the `p159` above. Copy whatever a PowerPoint-saved file uses.)

Entrance "Fade" on click for shape id 4 (UNVERIFIED template from knowledge of PowerPoint output: presetID 10 = Fade, presetClass `entr`):
```xml
<p:timing><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>
 <p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>
  <p:par><p:cTn id="3" fill="hold"><p:stCondLst><p:cond delay="indefinite"/></p:stCondLst><p:childTnLst>
   <p:par><p:cTn id="4" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>
    <p:par><p:cTn id="5" presetID="10" presetClass="entr" presetSubtype="0" fill="hold" grpId="0" nodeType="clickEffect">
     <p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>
      <p:set><p:cBhvr><p:cTn id="6" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn>
        <p:tgtEl><p:spTgt spid="4"/></p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr>
        <p:to><p:strVal val="visible"/></p:to></p:set>
      <p:animEffect transition="in" filter="fade"><p:cBhvr><p:cTn id="7" dur="500"/><p:tgtEl><p:spTgt spid="4"/></p:tgtEl></p:cBhvr></p:animEffect>
     </p:childTnLst></p:cTn></p:par>
   </p:childTnLst></p:cTn></p:par>
  </p:childTnLst></p:cTn></p:par>
 </p:childTnLst></p:cTn>
 <p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>
 <p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq>
</p:childTnLst></p:cTn></p:par></p:tnLst>
<p:bldLst><p:bldP spid="4" grpId="0"/></p:bldLst></p:timing>
```
Rules: `cTn/@id` must be unique within the slide. `spid` must match a real `p:cNvPr/@id`. The `grpId` and `spid` pair in `bldLst` must match the time-node tree and be unique ([MS-OI29500 note on bldGraphic](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oi29500/ec82ed17-db49-4761-afc3-d8e7d2930646)). For charts use `p:bldGraphic`, not `bldP`. An issue reporter found that hand-written animation XML "either gets ignored by PowerPoint or causes the file to behave inconsistently" ([python-pptx #1106](https://github.com/scanny/python-pptx/issues/1106)), which is why a tested generator (OfficeCLI) or copying a PowerPoint-saved sample is preferred over writing from scratch.

### 2.3 Playback compatibility

| Viewer | Base transitions (fade, push, wipe) | p14/p15 transitions | Morph | Entrance animations (fade, fly, wipe) | Evidence |
|---|---|---|---|---|---|
| PowerPoint 365 / 2019+ | Yes | Yes | Yes. PowerPoint 2016 needs build 16.0.4358+ and otherwise plays Fade. | Yes | [MS support, morph](https://support.microsoft.com/en-us/PowerPoint/training/use-the-morph-transition-in-powerpoint) (VERIFIED) |
| LibreOffice Impress | Mostly | Partial (old reports of unsupported transitions) | **No.** The feature request was closed as a duplicate. Shows the fallback (fade). | Basic presets import. There are long-standing fidelity bugs: fly-in speed, chart fly-in, exit then entrance. Meta bug [109362](https://www.libreoffice.org/bugzilla/showdependencytree.cgi?id=109362) is open. | Bug reports (VERIFIED, mostly old). The OfficeCLI skill says LibreOffice shows morph as a plain fade (CLAIM). |
| Google Slides (import) | Basic | Simplified or dropped | Dropped | Basic (fade, fly-in, appear). Motion paths and triggers are dropped. | Third-party guides only (CLAIM: [chatslide](https://www.chatslide.ai/guides/how-to-convert-powerpoint-to-google-slides)). Needs a Google account. |
| ONLYOFFICE | Fade, Push, Wipe, Split, Uncover, Cover, Clock, Zoom | Partial | **Not supported** (community gap list, 8.1) | Animation tab since 7.1 (May 2022) | [7.1 release](https://news.itsfoss.com/onlyoffice-7-1-release/), [compat thread](https://community.onlyoffice.com/t/lack-of-compatibility/4928) (CLAIM or forum) |

**Safe subset for "works everywhere":** base-schema transitions (`fade`, `push`, `wipe`, `split`, `cover`) and entrance presets Appear, Fade, Fly In and Wipe on whole shapes. Use morph only with a fade fallback, and tell the owner it only morphs in PowerPoint 365, 2019 or later.

---

## 3. Rendering, validation and slideshow without PowerPoint

| Option | URL / licence | Size | What it gives | Limits |
|---|---|---|---|---|
| **LibreOffice 26.8.1** (stable, files dated 2026-10-08) | https://download.documentfoundation.org/libreoffice/stable/ (MPL-2.0) | **MSI 358 MB** ([listing](https://download.documentfoundation.org/libreoffice/stable/26.8.1/win/x86_64/)). Needs "up to 1.5 GB" disk and admin rights to install. Windows 10 is supported ([sysreq](https://www.libreoffice.org/get-help/system-requirements/)). | `soffice --headless --convert-to pdf`. `--show` "Opens and starts the slideshow of the following presentation documents immediately" ([CLI help](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html)). Use `-env:UserInstallation=file:///D:/...` to keep the profile on D:. | `--convert-to png` exports **only the first slide** ([bug 157425](https://www.libreoffice.org/bugzilla/show_bug.cgi?id=157425)), so go PPTX → PDF → PNG. Renders with LibreOffice's engine, not PowerPoint's: fonts, autofit and chart styling can differ. Install on D: with `msiexec /i ... INSTALLLOCATION="D:\jarvis\tools\LibreOffice" /qn CREATEDESKTOPLINK=0` (CLAIM, [forums](https://ask.libreoffice.org/t/silent-passive-installation/4545)), or with `msiexec /a` admin extract, which runs without installing (CLAIM, [LO bug comment](https://www.libreoffice.org/bugzilla/show_bug.cgi?id=55491)). |
| LibreOffice Portable 26.2.4 | https://portableapps.com/node/82247 | Not confirmed. Install footprint about 0.5 to 1 GB (UNVERIFIED). | Runs with no admin rights | Lags behind the main releases. PortableApps wrapper. |
| ONLYOFFICE Desktop Editors 9.x | https://www.onlyoffice.com/ (AGPL-3.0) | Not confirmed (UNVERIFIED, several hundred MB) | Good PPTX fidelity and animation playback. `--view=path` opens view-only ([flags](https://api.onlyoffice.com/docs/desktop-editors/usage-api/command-line-flags/)). | **No documented flag to start a slideshow.** Users report `--view` does not start it ([forum](https://community.onlyoffice.com/t/opening-a-presentation-via-cli/12779)). No headless render path in Desktop Editors. DocumentBuilder is AGPL ([blog](https://onlyoffice.com/blog/2019/08/script-to-docs-use-free-onlyoffice-docbuilder-to-generate-docx-xlsx-pptx-and-pdf-files)). |
| OfficeCLI `view screenshot` / `view html` | see 1.1 | 32 MB | Per-slide PNG from its own HTML renderer through a headless browser (Edge is available) (CLAIM) | It is OfficeCLI's own approximation, **not an independent renderer**. Good for a quick check, not proof. |
| PowerPoint Viewer | n/a | n/a | n/a | **Retired 30 Apr 2018** and no longer downloadable ([Microsoft](https://support.microsoft.com/en-us/powerpoint/view-a-presentation-without-powerpoint)) |
| PowerPoint for the web | https://www.office.com | 0 | True PowerPoint playback including morph | Needs a Microsoft account and an upload to OneDrive (privacy). Owner-only, manual. |

### 3.1 Text overflow checks (no PowerPoint)

1. **Geometry check in python-pptx** (no render): every shape's `left + width <= slide_width` and `top + height <= slide_height`, and pictures and tables stay inside safe margins.
2. **Text-fit estimate**: measure each paragraph with Pillow `ImageFont.getlength()` using the same TTF the theme uses (fonttools is already installed), wrap it to the box width, and compare the line count times line height against the box height. python-pptx's own `fit_text()` needs the font-file path and makes all sizes uniform ([analysis page](https://python-pptx.readthedocs.io/en/latest/dev/analysis/txt-fit-text.html)). `normAutofit` shrink is computed by PowerPoint at edit time, so do not rely on it (UNVERIFIED).
3. **Rendered check**: run LibreOffice PDF through pdfplumber word boxes and compare each word's bbox with the shape box mapped to PDF coordinates. Words outside the box mean overflow. Then turn pages into PNGs (pdfplumber `page.to_image`, which uses pypdfium2) for **vision-model review** (contrast, crowding, overlapping, empty placeholders).
4. **Schema check**: `officecli validate` (CLAIM), or reopen the file with python-pptx and check for duplicate `cNvPr/@id`. PptxGenJS has an open bug for that ([#1532](https://github.com/gitbrent/PptxGenJS/issues/1532)).

---

## 4. PDF generation

| Tool | URL | Licence | Latest | Size / deps on Windows | Tagged / outline / links / fonts | Notes |
|---|---|---|---|---|---|---|
| **Playwright `page.pdf` (Chromium / Edge)** | https://playwright.dev/python/docs/api/class-page | Apache-2.0 | 1.63.0 **already installed** in neko-venv | **0 new MB** with `channel="msedge"` (uses installed Edge; [docs](https://playwright.dev/python/docs/browsers)) | `tagged` ("generate tagged (accessible) PDF") and `outline` ("embed the document outline") added in v1.42 and **default false** (VERIFIED, [API](https://playwright.dev/docs/api/class-page)). `displayHeaderFooter` and `headerTemplate`/`footerTemplate` support `pageNumber` and `totalPages` classes (VERIFIED). `preferCSSPageSize` and `printBackground` exist (VERIFIED). Links stay clickable and fonts are embedded as subsets (UNVERIFIED, check with pypdf). | Best HarfBuzz shaping for Indic and CJK using Windows fonts (Nirmala UI, Yu Gothic, Microsoft YaHei) (UNVERIFIED). Chrome made tagged PDFs default for "Save as PDF" from v85, and headless needs an explicit flag ([Chromium blog](https://blog.chromium.org/2020/07/using-chrome-to-generate-more.html)). No CSS Paged Media margin boxes, so page numbers come from header/footer templates. |
| WeasyPrint | https://doc.courtbouillon.org/weasyprint/stable/first_steps.html | BSD-3 | v70.0 (08 Sep). Security release. | **Standalone `weasyprint-windows-onefile.zip` 30.1 MB**, the "easiest way" on Windows ([assets](https://github.com/Kozea/WeasyPrint/releases/expanded_assets/v70.0)). The Python library on Windows needs **MSYS2 plus Pango** (VERIFIED, docs). The docs warn it "is regularly marked as malware by different antivirus companies". | PDF/UA and PDF/A support improved a lot from v66 to v70 (VERIFIED, [release notes](https://github.com/Kozea/WeasyPrint/releases)). Full CSS Paged Media (`@page`, counters, running headers). | Good second choice if true print typography is wanted. Install friction on Windows (MSYS2) unless the exe is used through the CLI. |
| Typst | https://github.com/typst/typst | Apache-2.0 | v0.15.1 (2026-07-17) | **Single binary zip 21.4 MB** ([assets](https://github.com/typst/typst/releases/expanded_assets/v0.15.1)) | v0.14: "accessible PDFs out of the box", tagged by default, PDF/UA-1, all PDF/A ([releases](https://github.com/typst/typst/releases)). Uses system fonts plus `--font-path` (VERIFIED). | Excellent quality and speed, but a new markup language for the LLM to write. Indic shaping not verified (UNVERIFIED). |
| Pandoc + LaTeX | https://github.com/jgm/pandoc/releases | GPL-2.0+ | 3.12.1 (2026-10-08). Windows MSI 39.9 MB. | Plus MiKTeX or TeX Live (**multi-GB**, UNVERIFIED) | Excellent | Too heavy for this laptop. Pandoc alone (40 MB) is still useful for md→docx/html. |
| ReportLab | https://pypi.org/project/reportlab/ | BSD | 5.0.1 on PyPI. **4.4.9 already installed.** | 0 new MB | Low-level canvas and Platypus. Outlines and links are possible. Tagged PDF not supported (UNVERIFIED). | Fine for simple, programmatic layouts, but hand-built and with no complex-script shaping (UNVERIFIED). Not for "beautiful" reports. |
| pdfkit / wkhtmltopdf | https://github.com/wkhtmltopdf/wkhtmltopdf | LGPL | **Archived 2 Jan 2023**. Last release 0.12.6 (2020). | n/a | n/a | **Do not use** (unmaintained, old WebKit; [summary](https://www.itechguides.com/wkhtmltopdf-alternatives-how-to-migrate-safely/)) |
| docx → pdf via LibreOffice | see 3 | MPL-2.0 | 26.8.1 | 358 MB MSI | Writer exports a tagged PDF when enabled (UNVERIFIED) | Only worth it if LibreOffice is installed anyway for PPTX. |

### 4.1 Charts and diagrams for PDFs and slides

| Tool | Licence | State | Notes |
|---|---|---|---|
| matplotlib | PSF-style | **3.10.7 installed** | SVG output stays crisp in Chromium PDFs. Use PNG at 2x for slides when native charts are not wanted. |
| vl-convert-python (Vega-Lite) | BSD-3 (UNVERIFIED) | not installed | "dependency-free" SVG and PNG from Vega-Lite specs, no browser ([PyPI](https://pypi.org/project/vl-convert-python)) |
| Mermaid | MIT | not installed | mermaid-cli outputs SVG, PNG and PDF via Puppeteer ([repo](https://github.com/mermaid-js/mermaid-cli)). Lighter option: load a vendored `mermaid.min.js` in the same Playwright page and print, so no extra browser download is needed (UNVERIFIED). |
| Native PPTX charts | n/a | python-pptx / PptxGenJS | Editable in PowerPoint because the data is embedded in an xlsx (VERIFIED for PptxGenJS) |

### 4.2 PDF validation (all offline)

| Check | Tool | State |
|---|---|---|
| Text is searchable (non-empty `extract_text()` per page) | pypdf | installed |
| Clickable links: `/Annots` with `/Subtype /Link` and `/A /URI`. Count them against the hrefs in the HTML. | pypdf | installed |
| Fonts embedded: every font in `/Resources /Font` has a `/FontDescriptor` with `/FontFile`, `/FontFile2` or `/FontFile3` (Type 3 fonts need separate handling) | pypdf | installed |
| Outline (bookmarks) present: `reader.outline` | pypdf | installed |
| Tagged: `/MarkInfo /Marked true` and a `/StructTreeRoot` in the catalog | pypdf | installed |
| Overflow: in the browser before printing, run JS that checks `el.scrollWidth > el.clientWidth` for tables, pre and img. After printing, check pdfplumber word bboxes against page margins. | Playwright plus pdfplumber | pdfplumber needs installing (MIT, 0.11.10, pulls pypdfium2; [PyPI](https://pypi.org/pypi/pdfplumber/json)) |
| Page images for vision review | pypdfium2 (BSD-3/Apache-2.0, 5.14.0) via pdfplumber `to_image` | comes with pdfplumber. **Avoid PyMuPDF**: it is AGPL-3.0 or commercial ([PyPI](https://pypi.org/pypi/pymupdf/json)). |

---

## 5. Recommendation

### 5.1 PPTX pipeline (one recommendation)

1. **Generator: python-pptx 1.0.2** in neko-venv (one `pip install`, a few hundred KB). Reasons: Neko's doc plugin is Python, the library is the most-used MIT writer, it does native charts, tables, notes and hyperlinks, and Presenton and slide-deck-ai use it too (VERIFIED via their manifests and READMEs). **Themes per topic** = a small set of `.pptx` template files with master layouts (for example tech, business, science and education). These are plain theme files, not code. Each fact on a slide gets a numbered footnote text box (source title and domain), plus a full "Sources" slide and the URLs in the speaker notes.
2. **Animations and transitions: OfficeCLI** (Apache-2.0, a 32 MB single exe on `D:\jarvis\tools\officecli\`). Run it as a post-processor on the python-pptx file: `officecli set "/slide[N]" --prop transition=fade`, `officecli set "/slide[N]/shape[K]" --prop animation=fade-entrance-500`, then `officecli validate` (all CLAIM; test first). Reuse-first justification: it is the only maintained open-source tool found that writes PowerPoint transitions, morph and entrance animations. The fallback, used **only if** OfficeCLI output fails validation or PowerPoint repair: a small lxml injector using the spec-ordered XML in section 2.2, modelled on md2pptx's transition code (MIT). That justification must be recorded in `docs/01_RESEARCH_AUDIT.md`.
3. **QA renderer: LibreOffice 26.8.1**, installed to D: (358 MB download, up to 1.5 GB on disk, one-time admin install). Steps: `soffice --headless -env:UserInstallation=file:///D:/jarvis/cache/lo-profile --convert-to pdf`, then pdfplumber/pypdfium2 PNGs per slide, then automatic checks (geometry, text-fit, rendered-bbox overflow, empty placeholders, font substitution warnings), then vision-model review of the PNGs, then fix and re-render (at most 2 loops).
4. **Viewer and slideshow:** `soffice --show deck.pptx` (documented to start the slideshow at once). Also open the folder in Explorer.

**Limitations to state honestly to the owner:**
- Nothing on this laptop is PowerPoint. The PNG check shows **LibreOffice's** rendering. Fonts, autofit shrink and chart styling can differ slightly in real PowerPoint.
- **Morph will not play** in LibreOffice (it shows a fade), in Google Slides or in ONLYOFFICE. It plays only in PowerPoint 365 or 2019 and later. Fancy p14/p15 transitions and some animations may be simplified or dropped in LibreOffice and Google Slides. Default to the safe subset (fade, push and wipe transitions, plus Appear, Fade and Fly-In entrances).
- Animations cannot be proved by a static render. The automated check only proves the XML is valid and that targets match shape ids. The playback check is: owner watches the `--show` run once, or uploads to PowerPoint for the web (Microsoft account, manual, owner's choice).
- OfficeCLI is young (v1.0.x with frequent releases and open chart bugs). Pin a version, verify its SHA256SUMS, and keep charts in python-pptx.
- PowerPoint Viewer no longer exists (retired 2018).

### 5.2 PDF pipeline (one recommendation)

**HTML/CSS template (Jinja-style plus markdown-it-py, already pinned) → Playwright 1.63 with `channel="msedge"` → `page.pdf(tagged=True, outline=True, print_background=True, prefer_css_page_size=True, display_header_footer=True, footer_template="… <span class=pageNumber></span>/<span class=totalPages></span>")` → pypdf and pdfplumber validation → PNG pages for vision review.**

- Cover page, headings (become the outline), figure and table captions (`<figure>/<figcaption>`, `<table><caption>`) and a numbered references list with `<a href>`, so links are clickable.
- Charts are matplotlib SVG (installed). Diagrams use a vendored Mermaid JS rendered in the same page.
- Overflow guard in CSS: `table{table-layout:fixed;width:100%}`, `img,svg{max-width:100%}`, `pre{white-space:pre-wrap}`, `break-inside:avoid` on figures. Run the JS overflow probe before printing.
- New installs: **only pdfplumber** (it brings pypdfium2). No new browser, because the installed Edge is used.

**Limitations to state honestly to the owner:**
- Chromium has no CSS margin boxes, so page numbers come from the header/footer template, which is styled separately and needs explicit font sizes. "Page X of Y" works. A table of contents with page numbers is not automatic. A clickable ToC (links to headings) and the PDF outline sidebar are.
- Tagged output is "accessible-ish". It is **not certified PDF/UA**. If PDF/UA or PDF/A is ever required, Typst (21 MB binary) or the WeasyPrint exe (30 MB) are the upgrade paths.
- Indic and CJK text depends on Windows system fonts. Embedding, link and tag checks run automatically. Visual correctness of complex scripts relies on the vision review (UNVERIFIED until tested).
- Edge must stay installed. Playwright with `msedge` uses whatever Edge version Windows Update installs.

### 5.3 Install footprint summary (all on D:)

| Item | Download | Needed for |
|---|---|---|
| python-pptx (+ XlsxWriter) | < 1 MB | PPTX |
| pdfplumber (+ pdfminer.six, pypdfium2) | about 10 MB (UNVERIFIED) | PPTX and PDF QA |
| OfficeCLI exe | 32 MB | PPTX animations and validation |
| LibreOffice 26.8.1 MSI | 358 MB (up to 1.5 GB installed) | PPTX render and slideshow |
| Total | about 400 MB download, under 1.6 GB disk | D: has about 19 GB free |

---

## 6. Open items to test (not verified in this research)

1. OfficeCLI animation and transition XML opens without a repair prompt in PowerPoint for the web (owner's account) and plays in LibreOffice `--show`.
2. Whether LibreOffice 26.8 honours `normAutofit fontScale` when rendering.
3. Playwright `msedge` headless: `tagged=True` produces `/StructTreeRoot`, links survive, and fonts are embedded (pypdf check).
4. The installed size of LibreOffice when installed with `INSTALLLOCATION` on D:, and whether any of it still lands on C: (the user profile, which `-env:UserInstallation` redirects).
5. Check the Presenton, PPTAgent and OfficeCLI star counts and claims against code if any of them is adopted.

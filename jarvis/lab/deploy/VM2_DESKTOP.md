# VM2 as a dedicated Linux desktop the owner can watch and take over (D79, 2026-10-27)

What this is: VM2 (`hermes-agent-vm-2`, rebuilt as the Agent PC) is JARVIS's own always-on Linux desktop, like Muse. The lab agents drive it; the owner can open it in a browser
over Tailscale, watch, and take it over for the one step an agent must never do (a CAPTCHA, a login, a code, a payment, a cookie box). Everything on the JARVIS side is built and
tested with fakes; **the real VM2 plugs in by `jarvis/config/vm2.json` and the owner's steps below.** Nothing here was run on VM2 or on any server by the session that wrote it.

## The flow, in six lines
1. A wall appears (the browser agent on the PC, or a lab agent on VM2 calling `need_owner`). JARVIS raises a **takeover**: target `pc` or `vm2`, a closed kind, the site's host.
2. All JARVIS input to that target stops first. VM2: the lab's own `take control` freezes every process of the agent user and the answer is checked. PC: the engine waits.
3. ONE card in the ask panel (`Needs you: solve the CAPTCHA on <host>` with `Open VM2 desktop` / `Done, continue` / `Not now` / `Stop`) and ONE quiet notice in the inbox.
4. `Open VM2 desktop` opens the viewer address from `vm2.json` in the owner's default browser; he does the step himself; he presses `Done, continue` (or says "done" / "continue").
5. JARVIS looks again. Still there: it says so once and waits again, at most three more times, then the task pauses with a plain report. Gone: the desktop goes back to the agents
   (`continue task`) and the work goes on from the last verified milestone.
6. No answer in `takeover_minutes` (30): the mission pauses durably (`needs_owner`) and is continued later with the same id. JARVIS never solves, skips or works around a wall.

## What the owner does (deploy steps; none of them is done by Claude)
Do them in this order, on the laptop session or on VM2 over SSH/Tailscale, with the usual rules (list first, one named change at a time, nothing outside VM2).

1. **Copy the lab folder** to VM2 and run `sudo bash provision_vm2_desktop.sh --plan`, then without `--plan`. It creates the persistent Firefox profile
   (`/home/agent/.jarvis-profile/firefox`, mode 0700), the download quarantine (`/home/agent/quarantine`, mode 0700), the Firefox policies (downloads go to the quarantine; no password
   manager), `/usr/local/bin/jarvis-firefox` and `/usr/local/sbin/jarvis-vm2-profile status|reset`, and installs `quarantine.py` and the new `lab_control.py` next to each other
   (root-owned). Re-copy `jarvis_mcp.py` (it has the new `need_owner` tool) to the agent's tool-server folder.
2. **The control route on the Tailscale address.** Install Caddy from Ubuntu's apt, put `vm2-hub.Caddyfile` (replace `@TS_IP@` with `tailscale ip -4`) in place and run it as the
   system user `hub`. It exposes only `/control/*` (state, take, continue, quarantine) and only on the tailnet address. The agent user cannot reach it (`provision_vm2_tailscale.sh`
   rejects its traffic to the tailnet range). Optionally add `tailscale serve`/ACLs so only the laptop reaches the port.
3. **The viewer.** `sudo bash provision_vm2_desktop.sh --viewer` installs noVNC + websockify and the unit `jarvis-vm2-viewer.service` (Tailscale address only, not started). Then:
   - `sudo -u agent vncpasswd` and type the viewer password yourself (twice). JARVIS never sees, reads or stores it and it is never put in an address.
   - a drop-in `/etc/systemd/system/jarvis-desktop.service.d/vncauth.conf` that replaces the unit's `-SecurityTypes None` with `-SecurityTypes VncAuth -rfbauth /home/agent/.vnc/passwd`
     (keep `-localhost yes`), `systemctl daemon-reload && systemctl restart jarvis-desktop`;
   - `systemctl enable --now jarvis-vm2-viewer`.
4. **`jarvis/config/vm2.json`** (you edit it yourself; it is console-protected like `trusted_sites.json`, an agent cannot change it): set `enabled` to `true`,
   `viewer_url` to `http://<tailscale ip>:6080/vnc.html?autoconnect=1&resize=scale` and `control_url` to `http://<tailscale ip>:8088`. **No password, token or key in either address**:
   such an address is dropped. Only a Tailscale address (100.64.0.0/10, `*.ts.net`) or this machine is accepted. The face's network lock then also opens exactly that host and port
   (`face/.../jarvis/vm2-viewer.ts`); nothing else.
5. **`protected.json`** (kernel guard in the secrets folder): add `vm2.json` next to `trusted_sites.json` if your copy lists config files by name. The code-level guard
   (`SAFETY_COMPONENTS`, the push gate) already lists it.
6. **Log in once yourself.** Open the viewer, take over the desktop, open Firefox (the JARVIS profile), sign in to the sites you want the sandbox to use. The sessions stay in the
   profile across restarts. JARVIS never types or reads those passwords and no model reads the profile. To start clean: `sudo jarvis-vm2-profile reset` (the old profile is kept
   beside it, renamed with the date; delete it yourself when sure).
7. **Per-site grants for VM2** (only the owner's PIN in the console makes one): a grant with `target: vm2` for a host, abilities `signin_existing_session`, `accept_terms`,
   `download_to_quarantine`, default 14 days (at most 30), no sign-up. The card says: *this session lives on VM2; a page that talks an agent into acting could use it; the grant
   expires in N days; page text on VM2 is read by the lab's public-lane models.* A PC grant never covers VM2 and the other way round.

## Egress: which hosts a download task needs (a server step for the owner)
The agent user's traffic leaves VM2 only through the egress allow-list (the Smokescreen proxy on the main server, `config/egress.json`; unchanged by this work). A VM2 download or
research task needs, in the allow-list: the site's own host (for example `files.example.test`), the hosts that serve its pages' scripts, fonts and downloads (often a CDN host named in
the page: the first run shows which request was refused), and nothing else. To add a host, the owner edits the proxy's ACL on the server (his step: one named host at a time) and
restarts that service. JARVIS does not edit it. Until a host is allowed the task fails on that host and says so; it never falls back to the PC.

## Downloads (what crosses and when)
Firefox saves into `/home/agent/quarantine`. `lab_control` (root, behind the Tailscale route) lists the files with their type from the **content** (magic bytes), size, sha256, whether the
name lies, and the virus scanner's answer **only if `clamscan` is already installed** (nothing is installed by this work). A file crosses to the PC only after the owner presses
`Copy to my PC quarantine` on a card that names it (name, size, type, source host); the PC re-hashes the bytes and writes nothing unless the sha256 equals the one on the card. It lands
in `outputs\downloads\<host>\`; a program, script, archive, macro file or unknown type gets `.quarantined` added to its name and is **never opened, run, previewed or unpacked** by
JARVIS. The quarantine folder on VM2 is not emptied by JARVIS; clear it from the desktop when you like.

## What only the real VM2 can show (not verified here)
- the viewer (noVNC) opening from the card in the owner's default browser, the VNC password prompt, the view-only switch (`view_only=1`);
- that `take control` freezes a real Hermes + Firefox session and that the lab agent follows "call `need_owner` at a wall" and looks again after the hand-back;
- how the owner's agent words a lab task from the `vm2_task` instruction, and how long a task takes (`task_wait_minutes`, default 40);
- the hub route and Caddy on the Tailscale address; Firefox's download policy; `jarvis-vm2-profile reset`; the real `.ts.net` name against the face's network lock;
- the existing outputs service `jarvis-hub-outputs` mounts the laptop's outputs INTO the (old WSL) lab box, the opposite direction. The VM2-to-PC copy therefore uses the lab control
  service's read-only quarantine routes instead (same hub, same header, same Unix socket), which is new code in `lab_control.py` and `quarantine.py`.

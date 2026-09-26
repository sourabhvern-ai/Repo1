# ComfyUI ↔ Claude Code (Windows GPU PC)

Claude Code skills and a small stdlib Python client that let Claude drive the **local ComfyUI Desktop** on this PC:

- **Text-to-image:** Flux 2 Dev (`flux2-dev.safetensors`)
- **Image-to-video:** WAN 2.2 14B i2v (high/low-noise experts)
- **LoRA comparison:** prompts × strengths grid + HTML gallery

Adapted from [purzbeats/lora_tester](https://github.com/purzbeats/lora_tester). Z-Image Turbo → Flux 2 Dev,
LTX 2.3 → WAN 2.2, `C:/ai/ComfyUI` → ComfyUI Desktop paths, model downloading removed.

| Path | What |
|------|------|
| `.claude/skills/comfy_local/SKILL.md` | Local server, paths, endpoints, error handling |
| `.claude/skills/comfy_workflows/SKILL.md` | Flux 2 Dev + WAN 2.2 i2v pipelines, discovery rules |
| `.claude/skills/lora_tester/SKILL.md` | LoRA testing workflow |
| `comfyui/comfy_client.py` | `discover` / `t2i` / `i2v` / `t2i2v` CLI (never downloads models) |
| `comfyui/lora_test.py`, `comfyui/gallery.html` | LoRA grid runner and viewer |

## Why Claude Code must run on the PC

ComfyUI only listens on `127.0.0.1` of the GPU PC. A Claude session in the cloud (claude.ai/code) can't reach it, so
run **Claude Code locally on the PC**. Nothing needs to be exposed to the internet.

## Setup (once, in PowerShell on the GPU PC)

1. **Python 3.8+ and Git**: `python --version`, `git --version` (install from python.org / git-scm.com if missing).
   ComfyUI Desktop's own `.venv` Python also works.
2. **Claude Code**: follow the Windows install steps at https://code.claude.com/docs (native installer or `npm i -g @anthropic-ai/claude-code`), then run `claude` once and log in.
3. **Clone this repo** (the skills load automatically from `.claude/skills/` when Claude is started inside it):
   ```powershell
   git clone -b claude/practical-newton-js98p8 https://github.com/sourabhvern-ai/Repo1.git
   cd Repo1
   ```
4. **Start ComfyUI Desktop**, then check the connection:
   ```powershell
   python comfyui\comfy_client.py discover
   ```
   This prints the server URL (Desktop defaults to port **8000**, manual installs to 8188), the GPU and VRAM, the real model
   folders, and which files each pipeline will use. If the port differs, run `$env:COMFY_URL="http://127.0.0.1:PORT"`.
5. **First test runs:**
   ```powershell
   python comfyui\comfy_client.py t2i "a steel arch bridge over a river at dawn, photorealistic" --width 1344 --height 768
   python comfyui\comfy_client.py t2i2v "container port at sunset, gantry cranes" --motion "slow aerial drift forward"
   ```
6. **Use it through Claude:** run `claude` in the repo folder and ask, e.g., *"/comfy_local generate 4 variations of …"*.

## Suggested first prompt for the local Claude

> Read `.claude/skills/comfy_local/SKILL.md` and `comfy_workflows/SKILL.md`. Run `python comfyui/comfy_client.py discover`
> and confirm the Flux 2 Dev and WAN 2.2 i2v model picks match what's installed. Resolve the real ComfyUI base path via
> `/internal/folder_paths` and `%APPDATA%/ComfyUI/config.json`, and update the "Paths on this PC" table in the skill.
> Then run one t2i and one i2v test. Do NOT download any models; use only what's installed.

## n8n automation (optional)

`comfyui/n8n/comfyui_flux2_wan22.json` is an importable n8n workflow. It runs the same pipelines as `comfy_client.py`:

**Generate form** → Config → `GET /object_info` (model discovery) → build Flux 2 workflow → `POST /prompt` → poll
`/history` every 5 s → *Video wanted?* → build WAN 2.2 workflow (feeds the still in via `LoadImage "name.png [output]"`)
→ `POST /prompt` → poll every 10 s → result (filenames in ComfyUI's output folder).

1. In n8n, go to **Workflows → Import from File** and select `comfyui_flux2_wan22.json`.
2. Open the **Config** node and set `comfyUrl`:
   - `http://127.0.0.1:8000` for ComfyUI Desktop
   - `:8188` for a manual install
   - `http://host.docker.internal:8000` if n8n runs in Docker
3. Click **Test workflow** (or activate the workflow). Then open the form URL shown on the **Generate form** node and fill in the prompt, mode and aspect.

The form answers straight away and the job carries on in the background. Progress and any errors show under
**Executions**. If a required model isn't installed, the build step stops with a clear error. Nothing is downloaded.
Timeouts are 15 min for the image and 60 min for the video.
To change a Code node, edit `comfyui/n8n/build_n8n_workflow.py` and run it to regenerate the JSON.

## Notes

- **fps:** WAN 2.2 videos are saved at 16 fps (the model's native rate). The original skill's 24 fps default would play motion 1.5× too fast.
- **VRAM:** if `flux2-dev.safetensors` is the full bf16 file (~64 GB) and the GPU has <48 GB, add `--weight-dtype fp8_e4m3fn`.
- **Optional speed-ups:** if the Flux 2 Turbo LoRA or the WAN 2.2 i2v lightx2v 4-step LoRAs are already installed, they're used automatically (`--no-turbo` / `--no-lightx2v` to disable). They're never downloaded.

## Licence

The skills, `lora_test.py` and `gallery.html` are derived from [purzbeats/lora_tester](https://github.com/purzbeats/lora_tester) (MIT licence).
The pipelines are transcribed from [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates)
(`image_flux2_text_to_image.json`, `video_wan2_2_14B_i2v.json`).

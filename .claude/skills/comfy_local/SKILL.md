---
name: comfy_local
description: Generate images and videos through the local ComfyUI Desktop server on this Windows GPU PC (Flux 2 Dev text-to-image, WAN 2.2 14B image-to-video). Use when the user wants to generate, create or render images/video with the locally running ComfyUI.
argument-hint: [prompt or description of what to generate]
allowed-tools: Bash, Read, Write, Glob, Grep, Agent, WebFetch
---

# ComfyUI Local Server Skill (Windows, ComfyUI Desktop)

Adapted from `purzbeats/lora_tester` (`.claude/skills/comfy_local/skill.md`) for this machine:
Windows + ComfyUI Desktop, Flux 2 Dev instead of Z-Image Turbo, WAN 2.2 i2v instead of LTX 2.3.

You drive a local ComfyUI server headlessly through its REST API. **No auth**, localhost only.

## Hard rules for this machine

1. **Never download models.** Not with curl, not with huggingface-cli, not with ComfyUI Manager. Only use what `/object_info` reports as installed. If something is missing, say what is missing and stop.
2. **Discover, don't hardcode.** Filenames come from `/object_info` (see `comfy_workflows`). `comfyui/comfy_client.py discover` does this for you.
3. Don't expose the server (no `--listen 0.0.0.0`, tunnels or port forwards) unless the user explicitly asks.

## Read these first

- `.claude/skills/comfy_workflows/SKILL.md`: API workflow format, model discovery, and the **Flux 2 Dev t2i** and **WAN 2.2 i2v** pipelines (source of truth).
- `comfyui/comfy_client.py`: stdlib client that implements both pipelines, validates every workflow against `/object_info` before submitting, and never downloads anything. **Prefer calling it over writing ad-hoc scripts.**

```bash
python comfyui/comfy_client.py discover                     # server, GPU, model folders, chosen models
python comfyui/comfy_client.py t2i "prompt" --width 1344 --height 768
python comfyui/comfy_client.py i2v C:/path/to/still.png "motion prompt"
python comfyui/comfy_client.py t2i2v "scene prompt" --motion "camera slowly pushes in"
```

## Server address

ComfyUI Desktop listens on **`http://127.0.0.1:8000`** by default (the portable/manual install uses 8188).
The client tries `$COMFY_URL`, then 8000, then 8188. If neither answers, ComfyUI Desktop isn't running, or
its port was changed under Settings → Server-Config. Ask the user to start it, or set `COMFY_URL`.

In the snippets below, `LOCAL` means whichever address answered.

## Paths on this PC

| What | Where |
|------|-------|
| ComfyUI Desktop app (reported by the user) | `C:/Users/LorenzoMandrelli/AppData/Local/Comfy-Desktop` |
| Desktop settings, including `basePath` | `%APPDATA%/ComfyUI/config.json` |
| Extra model folders, if any | `%APPDATA%/ComfyUI/extra_models_config.yaml` |
| Base path (models / input / output / custom_nodes) | the `basePath` value above. The installer default is `%USERPROFILE%/Documents/ComfyUI` |

**The app folder is usually not where models and outputs live.** Resolve the real folders once per session:

```bash
curl -s LOCAL/internal/folder_paths           # authoritative: every model folder ComfyUI scans
python -c "import json,os;print(json.load(open(os.path.expandvars(r'%APPDATA%\ComfyUI\config.json'))).get('basePath'))"
```

Then `<basePath>/output/` holds outputs and `<basePath>/models/{diffusion_models,text_encoders,vae,loras}/` holds models.
Use forward slashes in Bash (`C:/Users/...`). Use raw strings or `\\` in Python.

## Local API endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/prompt` | POST | Submit a workflow (`{"prompt": {...}}`) → `{"prompt_id": "..."}` |
| `/queue` | GET | Queue status |
| `/history/{prompt_id}` | GET | Job status and outputs (poll this) |
| `/object_info` | GET | All node definitions and dropdown options (legacy COMBO format `[[opt, ...], {...}]`) |
| `/object_info/{NodeType}` | GET | One node's inputs/options |
| `/internal/folder_paths` | GET | Model folders on disk |
| `/view?filename=X&subfolder=Y&type=output` | GET | Download an output file |
| `/upload/image` | POST | Upload an input image (multipart, field `image`) |
| `/system_stats` | GET | GPU, VRAM, ComfyUI version |
| `/interrupt` | POST | Stop the running job |

## Submitting and polling

`comfy_client.py` has `submit()`, `wait_for_job()`, `output_files()` and `upload_image()`. Import them instead of
rewriting them (`sys.path.insert(0, "comfyui"); import comfy_client as cc`). Timeouts: 15 min for an image and
60 min for a video, because the first run loads the models from disk.

## After submission

Poll `/history/{prompt_id}` until it finishes and report the saved filename(s). **Don't open or display outputs
unless asked.** The user manages the output folder. Images land in `<basePath>/output/`, videos in `<basePath>/output/video/`.

## Error handling

- **Server unreachable:** ask the user to start ComfyUI Desktop, or check the port.
- **HTTP 400 from `/prompt`:** the body's `node_errors` names the bad input. Fix it against `/object_info/{NodeType}`.
- **Missing node type** (e.g. `Flux2Scheduler`, `EmptyFlux2LatentImage`): ComfyUI is too old for Flux 2. Ask the user to update ComfyUI Desktop. Don't install custom nodes.
- **Missing model file:** report which file the pipeline needs and which similar files *are* installed. **Do not download.** Let the user decide.
- **Out of memory:** for Flux 2 try `--weight-dtype fp8_e4m3fn` or a lower resolution. For WAN lower the resolution/length, keep the lightx2v LoRAs on, and close other GPU apps.

## Checking what's installed (read-only)

```bash
python comfyui/comfy_client.py discover
curl -s LOCAL/object_info/UNETLoader | python -c "import json,sys;d=json.load(sys.stdin);print('\n'.join(d['UNETLoader']['input']['required']['unet_name'][0]))"
```

To check a file's size (e.g. whether `flux2-dev.safetensors` is the ~64 GB bf16 checkpoint or an fp8 one), use
`ls -la` on the folder that `/internal/folder_paths` reports for `diffusion_models`.

## Key principles

1. Local means no auth, no redirects, and one GPU job at a time (the queue is sequential).
2. `/history/{id}` has both status and outputs.
3. The output `filename` is exactly the `filename_prefix` you set plus `_00001_`.
4. Workflow JSON and pipelines live in `comfy_workflows`. LoRA comparison lives in `lora_tester`.
5. **Installed models only.** Never download.

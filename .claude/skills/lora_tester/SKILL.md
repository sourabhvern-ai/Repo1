---
name: lora_tester
description: Systematically test a Flux 2 LoRA across multiple prompts and strengths on the local ComfyUI, producing a self-contained project folder (manifest + images) viewable in a single-file HTML gallery (grid, side-by-side, A/B slider, PNG export). Use when the user wants to test, compare or evaluate a LoRA.
allowed-tools: Bash, Read, Write, Glob, Grep
---

# LoRA Testing Toolkit (local, Flux 2 Dev)

Adapted from `purzbeats/lora_tester`. Here it runs locally only, and the base pipeline is **Flux 2 Dev** instead of Z-Image Turbo, which isn't installed.
The cloud variant (`lora_test_cloud.py`) was not copied. **Never download LoRAs or models.**

Files: `comfyui/lora_test.py` (runner), `comfyui/gallery.html` (viewer), `comfyui/comfy_client.py` (pipeline + discovery).

## Usage

```bash
python comfyui/lora_test.py --list-loras      # LoRAs installed on this ComfyUI
python comfyui/lora_test.py --lora "flux2\\my-lora.safetensors" --strengths "0,0.5,1.0" --name "my lora test"
python comfyui/lora_test.py --lora LORA --strengths "0,0.25,0.5,0.75,1.0" --name "project" --notes "notes"
```

Only use **Flux 2** LoRAs. LoRAs for Flux 1, SDXL, Z-Image and similar won't apply to Flux 2 Dev.
Before running, edit `PROMPTS` at the top of the script to fit the LoRA's subject and add its trigger word if it has one.

Config at the top of `lora_test.py`: `PROMPTS`, `STRENGTHS`, `WIDTH`/`HEIGHT` (1024²), `STEPS` (20), `GUIDANCE` (4.0),
`USE_TURBO_LORA` (off, so comparisons stay clean), `WEIGHT_DTYPE`, `BASE_SEED` (42).

## How it works

1. Discovers the Flux 2 UNet, text encoder and VAE via `/object_info`, and checks the LoRA is installed.
2. Builds one workflow per (prompt × strength). The seed is `BASE_SEED + prompt_index`, so each prompt uses the same seed at every strength.
3. **Strength 0 = baseline**, with no LoRA node.
4. Submits every job up front and polls them. Each image is downloaded into the project folder as it finishes.
5. Writes `manifest.json`.

Flux 2 Dev at 20 steps takes much longer per image than Z-Image Turbo did, so warn the user about the total time for big matrices.

## Project folder

```
comfyui/projects/20260926_my-lora-test/
  manifest.json
  images/p00_s000.png, p00_s050.png, p00_s100.png, p01_s000.png, ...
```

`p{prompt:02d}_s{strength×100:03d}.png`. `projects/` is git-ignored.

## Gallery

Open `comfyui/gallery.html` in a browser, click **Open Project** and pick the project folder. It has Grid, Strips, Side-by-Side
and A/B Slider views, a lightbox, and PNG export (full grid, single strip, 2 or 3 strength comparisons, before/after). No server is needed.

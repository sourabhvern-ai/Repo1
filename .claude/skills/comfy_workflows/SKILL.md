---
name: comfy_workflows
description: Workflow API format, model discovery and the proven pipelines for this PC's ComfyUI: Flux 2 Dev text-to-image and WAN 2.2 14B image-to-video. Loaded by /comfy_local; also usable standalone as a workflow-building reference.
allowed-tools: Bash, Read, Write, Glob, Grep, Agent, WebFetch
---

# ComfyUI Workflows: Shared Knowledge (this PC)

Adapted from `purzbeats/lora_tester` (`.claude/skills/comfy_workflows/skill.md`). The Z-Image Turbo and LTX 2.3
pipelines were replaced because those models are **not installed** here. Flux 2 Dev and WAN 2.2 i2v are used instead.
Both pipelines are implemented in `comfyui/comfy_client.py` (`build_flux2_t2i`, `build_wan22_i2v`).

**Never download models.** Build only from what `/object_info` lists.

## Workflow API format

`/prompt` takes **API format**: a flat dict keyed by string node IDs. Each node has a `class_type` and `inputs`, and links are `["node_id", output_index]`.

```python
prompt = {
    "1": {"class_type": "NodeType", "inputs": {"param": "value", "model": ["other_node_id", 0]}},
}
```

Write `.py` files rather than bash heredocs with embedded Python (quoting breaks, especially on Windows).
UI-only widget values such as `control_after_generate` are not API inputs, so leave them out.

## Saving workflows (two formats)

When asked to save a workflow, save both:
- `name_workflow-api.json`: `{"prompt": {...}}` for headless use (`comfy_client.py ... --save-workflow name` writes this).
- `name_workflow.json`: graph format for the ComfyUI UI (`nodes`, `links`, `groups`, `version: 0.4`). Generate it with a Python builder, never by hand.

## Discovering models

`/object_info` dropdowns come in two shapes: legacy `[[opt, ...], {...}]` and wrapped `["COMBO", {"options": [...]}]`.
Newer ComfyUI builds also have dynamic inputs (`COMFY_DYNAMICCOMBO_V3`). Treat those as "no fixed option list".

```python
def widget_options(node_def, input_name):
    spec = node_def["input"]["required"][input_name]
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1:
        return spec[1].get("options", [])
    return []
```

`comfy_client.discover()` picks files by name tokens:

| Role | Loader | Match |
|------|--------|-------|
| Flux 2 Dev UNet | `UNETLoader` | contains `flux2` (prefers `dev`, excludes `klein`), e.g. `flux2-dev.safetensors` |
| Flux 2 text encoder | `CLIPLoader` (type `flux2`) | contains `mistral`, e.g. `mistral_3_small_flux2_bf16.safetensors` |
| Flux 2 VAE | `VAELoader` | contains `flux2`, or `full_encoder_small_decoder` |
| Flux 2 Turbo LoRA (optional) | `LoraLoaderModelOnly` | `flux` + `2` + `turbo` |
| WAN 2.2 i2v high / low | `UNETLoader` | `wan2.2` + `i2v` + `high` / `low` |
| WAN text encoder | `CLIPLoader` (type `wan`) | contains `umt5` |
| WAN VAE | `VAELoader` | `wan_2.1_vae` (the 14B models use the **2.1** VAE; `wan2.2_vae` is only for the 5B model) |
| WAN lightx2v 4-step LoRAs (optional) | `LoraLoaderModelOnly` | `i2v` + `lightx2v` + `high` / `low` |

Run `python comfyui/comfy_client.py discover` and check the picks before the first generation. If a pick is wrong,
pass the right filename explicitly by building the workflow in Python with an edited `models` dict.

## Sources of truth (priority order)

1. **`Comfy-Org/workflow_templates`**: `templates/image_flux2_text_to_image.json` and `templates/video_wan2_2_14B_i2v.json`
   (`https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/<name>.json`). They use the subgraph
   format: the real nodes sit in `definitions.subgraphs[0].nodes` and `.links`. Ignore their `MarkdownNote` model links,
   since we don't download.
2. The pipelines below, which were transcribed from those templates.
3. **Always validate against `/object_info/{NodeType}`.** `comfy_client.validate()` does this before every submit.

## Flux 2 Dev (text-to-image): proven pipeline

Mirrors `image_flux2_text_to_image.json`. Flux 2 uses a custom-sampler chain, not `KSampler`.

```python
wf = {
    "1":  {"class_type": "UNETLoader", "inputs": {"unet_name": FLUX2_UNET, "weight_dtype": "default"}},
    "2":  {"class_type": "CLIPLoader", "inputs": {"clip_name": FLUX2_TE, "type": "flux2", "device": "default"}},
    "3":  {"class_type": "VAELoader", "inputs": {"vae_name": FLUX2_VAE}},
    "4":  {"class_type": "CLIPTextEncode", "inputs": {"text": PROMPT, "clip": ["2", 0]}},
    "5":  {"class_type": "FluxGuidance", "inputs": {"guidance": 4.0, "conditioning": ["4", 0]}},
    "6":  {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["5", 0]}},
    "7":  {"class_type": "RandomNoise", "inputs": {"noise_seed": SEED}},
    "8":  {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
    "9":  {"class_type": "Flux2Scheduler", "inputs": {"steps": 20, "width": W, "height": H}},
    "10": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": W, "height": H, "batch_size": 1}},
    "11": {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["7", 0], "guider": ["6", 0], "sampler": ["8", 0], "sigmas": ["9", 0], "latent_image": ["10", 0]}},
    "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
    "13": {"class_type": "SaveImage", "inputs": {"filename_prefix": PREFIX, "images": ["12", 0]}},
}
```

- **Steps:** 20 without a LoRA. If a Flux 2 Turbo LoRA is installed, insert `LoraLoaderModelOnly` (strength 1.0) between
  node 1 and `BasicGuider.model` and use **8** steps.
- **Guidance:** 4.0 (template default). `cfg` isn't used, because `BasicGuider` has no negative prompt.
- **Resolution:** Flux2Scheduler's `width`/`height` must equal the latent's. Use multiples of 16, e.g. 1024², 1344×768, 768×1344, 1248×832.
- **VRAM:** the full bf16 `flux2-dev` checkpoint is ~64 GB. ComfyUI offloads automatically, but on cards with <48 GB set
  `weight_dtype: "fp8_e4m3fn"` (`--weight-dtype fp8_e4m3fn`) for speed. If the file is an fp8 build, keep `default`.

### Adding a style LoRA
Insert `LoraLoaderModelOnly` after the UNet (or after the Turbo LoRA) and point `BasicGuider.model` at it. For strength 0, skip the node.

## WAN 2.2 14B (image-to-video): proven pipeline

Mirrors `video_wan2_2_14B_i2v.json`. Two experts: the high-noise model does the first half of the steps and the low-noise model finishes.

```python
wf = {
    "1":  {"class_type": "UNETLoader", "inputs": {"unet_name": WAN_HIGH, "weight_dtype": "default"}},
    "2":  {"class_type": "UNETLoader", "inputs": {"unet_name": WAN_LOW, "weight_dtype": "default"}},
    "3":  {"class_type": "CLIPLoader", "inputs": {"clip_name": UMT5, "type": "wan", "device": "default"}},
    "4":  {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
    "5":  {"class_type": "LoadImage", "inputs": {"image": UPLOADED_NAME}},
    "6":  {"class_type": "CLIPTextEncode", "inputs": {"text": PROMPT, "clip": ["3", 0]}},
    "7":  {"class_type": "CLIPTextEncode", "inputs": {"text": WAN_NEGATIVE, "clip": ["3", 0]}},
    "8":  {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 5.0}},   # or ["16", 0] with LoRA
    "9":  {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 5.0}},   # or ["17", 0] with LoRA
    "10": {"class_type": "WanImageToVideo", "inputs": {
        "positive": ["6", 0], "negative": ["7", 0], "vae": ["4", 0], "start_image": ["5", 0],
        "width": 640, "height": 640, "length": 81, "batch_size": 1}},
    "11": {"class_type": "KSamplerAdvanced", "inputs": {
        "add_noise": "enable", "noise_seed": SEED, "steps": STEPS, "cfg": CFG, "sampler_name": "euler",
        "scheduler": "simple", "start_at_step": 0, "end_at_step": SPLIT, "return_with_leftover_noise": "enable",
        "model": ["8", 0], "positive": ["10", 0], "negative": ["10", 1], "latent_image": ["10", 2]}},
    "12": {"class_type": "KSamplerAdvanced", "inputs": {
        "add_noise": "disable", "noise_seed": 0, "steps": STEPS, "cfg": CFG, "sampler_name": "euler",
        "scheduler": "simple", "start_at_step": SPLIT, "end_at_step": 10000, "return_with_leftover_noise": "disable",
        "model": ["9", 0], "positive": ["10", 0], "negative": ["10", 1], "latent_image": ["11", 0]}},
    "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
    "14": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "fps": 16.0}},
    "15": {"class_type": "SaveVideo", "inputs": {
        "video": ["14", 0], "filename_prefix": "video/" + PREFIX, "format": "auto", "codec": "auto"}},
}
```

| Mode | STEPS | CFG | SPLIT | Template timing (RTX 4090, 640², 81 f) |
|------|-------|-----|-------|------|
| lightx2v 4-step LoRAs installed (nodes 16/17 = `LoraLoaderModelOnly`, strength 1.0) | 4 | 1.0 | 2 | ≈ 70–100 s |
| No LoRAs | 20 | 3.5 | 10 | ≈ 510–540 s |

- **Frames:** `length` must be 4k+1. At 16 fps, 81 frames is about 5 s: `length = int(seconds*16)//4*4 + 1`.
- **fps: 16, not 24.** The upstream skill defaults to 24 fps, but WAN 2.2 14B is trained at 16 fps. Encoding its frames at 24 fps just plays the motion 1.5× too fast. Keep 16 unless the user asks otherwise, or interpolate frames afterwards.
- **Resolution:** 640×640 (template), 832×480 / 480×832 (fast), or 1280×720 (high VRAM, much slower). Multiples of 16. Match the input image's aspect ratio.
- **SaveVideo:** `format: "auto", codec: "auto"` gives mp4/h264. It's valid on both the older flat schema and the newer nested (DynamicCombo) schema. Don't send `mp4`/`h264` blindly.
- **Negative prompt:** the official Chinese Wan negative (`comfy_client.WAN_NEGATIVE`). Keep it.

### Chaining Flux 2 → WAN 2.2 (t2i2v)
`comfy_client.py t2i2v` generates the still, downloads it via `/view`, re-uploads it via `/upload/image` and feeds it to
`LoadImage`. This keeps the jobs separate so the Flux model can be unloaded before WAN loads, which matters for VRAM.
Generate the still at the video's aspect ratio (default 832×480).

## Output file naming

`YYYYMMDD_descriptive-slug`, e.g. `20260926_cable-stayed-bridge-dawn`. ComfyUI appends `_00001_`. Put videos under `video/`.
Never use generic prefixes like `ComfyUI`.

## Batch pattern

Submit every job up front (the queue runs them sequentially on the one GPU and models stay cached), then poll all
`prompt_id`s in one loop and report progress every ~20. Use `seed = BASE_SEED + i` per prompt for reproducibility.
Group by pipeline: run all Flux jobs, then all WAN jobs, so models aren't swapped back and forth.

## Key principles

1. Installed models only. Never download.
2. Discover filenames via `/object_info`, and validate every workflow against it before submitting.
3. Flux 2 uses `SamplerCustomAdvanced` + `Flux2Scheduler` + `FluxGuidance` (no negative prompt).
4. WAN 2.2 i2v uses two `KSamplerAdvanced` passes (high then low), the 2.1 VAE and 16 fps.
5. Write `.py` files, use descriptive prefixes, and save both workflow formats when persisting.

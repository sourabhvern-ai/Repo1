"""
LoRA Testing Script (Flux 2 Dev, local ComfyUI Desktop)

Adapted from purzbeats/lora_tester: the Z-Image Turbo pipeline was swapped for Flux 2 Dev
(comfy_client.build_flux2_t2i) and model filenames are discovered via /object_info.
Never downloads models.

Original description: LoRA Testing Script — Generate images across prompts x strengths.

Creates a self-contained project folder with downloaded images and manifest.

Usage:
    python lora_test.py                          # edit config below
    python lora_test.py --lora "flux2\\my-lora.safetensors"
    python lora_test.py --list-loras             # show available loras
    python lora_test.py --name "my test"         # custom project name
    python lora_test.py --strengths "0,0.25,0.5,0.75,1.0"
"""
import json, urllib.request, time, sys, argparse, os, shutil
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy_client as cc

COMFY_URL = cc.find_server()  # $COMFY_URL, else :8000 (Desktop), else :8188
DATE = date.today().strftime("%Y%m%d")
PROJECTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects")

# ── Configuration ────────────────────────────────────────────────────────────
# Edit these before running, OR pass --lora / --strengths on the CLI.
# Run `python lora_test.py --list-loras` first to see what's installed locally.
# If your lora has a trigger word (e.g. "c64, "), prepend it to each prompt.

LORA = "REPLACE_WITH_YOUR_LORA.safetensors"   # a Flux 2 LoRA, e.g. "flux2\\my-lora.safetensors"

STRENGTHS = [0.0, 0.25, 0.5, 0.75, 1.0]

# Generic prompts covering varied subjects so any lora's effect is visible.
# Replace with subject matter your lora was trained on.
PROMPTS = [
    "a portrait of a person looking thoughtfully into the distance",
    "a wide mountain landscape at golden hour with a river running through it",
    "a futuristic city street at night with neon lights and rain reflections",
    "a fox curled up among autumn leaves in a forest clearing",
    "a cozy interior with bookshelves, a fireplace, and warm afternoon light",
]

# Image dimensions
WIDTH = 1024
HEIGHT = 1024

# Sampler settings (Flux 2 Dev: custom sampler chain, euler, Flux2Scheduler)
STEPS = 20          # 8 if USE_TURBO_LORA and a Flux 2 Turbo LoRA is installed
GUIDANCE = 4.0      # FluxGuidance; Flux 2 has no cfg/negative prompt
USE_TURBO_LORA = False  # stacking the Turbo LoRA changes the look, so keep it off for fair LoRA comparisons
WEIGHT_DTYPE = "default"  # "fp8_e4m3fn" if flux2-dev is the full bf16 file and VRAM < 48 GB
BASE_SEED = 42  # same seed across strengths for fair comparison

# ── CLI ──────────────────────────────────────────────────────────────────────

def list_loras():
    data = cc.get_json(COMFY_URL, "/object_info/LoraLoaderModelOnly")
    loras = cc.options(data, "LoraLoaderModelOnly", "lora_name")
    print(f"Available LoRAs ({len(loras)}):")
    for l in sorted(loras):
        print(f"  {l}")
    sys.exit(0)

def parse_args():
    p = argparse.ArgumentParser(description="LoRA strength tester")
    p.add_argument("--lora", type=str, help="LoRA filename (overrides LORA config)")
    p.add_argument("--list-loras", action="store_true", help="List available loras and exit")
    p.add_argument("--strengths", type=str, help="Comma-separated strengths, e.g. '0,0.5,1.0'")
    p.add_argument("--name", type=str, help="Custom project name (default: lora slug)")
    p.add_argument("--notes", type=str, default="", help="Notes to save in manifest")
    return p.parse_args()

# ── Workflow builder ─────────────────────────────────────────────────────────

def build_workflow(prompt_text, lora_name, strength, seed, prefix):
    """Flux 2 Dev workflow (see comfy_workflows skill) with an optional model-only LoRA."""
    wf = cc.build_flux2_t2i(MODELS, prompt_text, prefix, WIDTH, HEIGHT, seed=seed,
                            steps=STEPS, guidance=GUIDANCE, use_turbo_lora=USE_TURBO_LORA,
                            weight_dtype=WEIGHT_DTYPE)
    if strength > 0 and lora_name:
        # chain after whatever currently feeds BasicGuider (UNet, or Turbo LoRA)
        wf["20"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": wf["6"]["inputs"]["model"], "lora_name": lora_name,
            "strength_model": strength}}
        wf["6"]["inputs"]["model"] = ["20", 0]
    return wf

# ── Download image from ComfyUI ─────────────────────────────────────────────

def download_image(filename, subfolder, dest_path):
    """Download an output image from ComfyUI server to local path."""
    url = f"{COMFY_URL}/view?filename={urllib.request.quote(filename)}&type=output"
    if subfolder:
        url += f"&subfolder={urllib.request.quote(subfolder)}"
    resp = urllib.request.urlopen(url)
    with open(dest_path, "wb") as f:
        f.write(resp.read())

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    if args.list_loras:
        list_loras()

    lora = args.lora or LORA
    global MODELS
    oi = cc.get_json(COMFY_URL, "/object_info", timeout=120)
    found = cc.discover(oi)
    MODELS = found["flux2"]
    cc.require(MODELS, ["unet", "text_encoder", "vae"], "Flux 2 Dev")
    if lora not in found["_all"]["lora"]:
        sys.exit(f"LoRA not installed: {lora!r}. Run with --list-loras.")
    strengths = [float(s) for s in args.strengths.split(",")] if args.strengths else STRENGTHS

    lora_slug = os.path.splitext(os.path.basename(lora))[0]
    project_name = args.name or lora_slug
    project_slug = project_name.replace(" ", "-").lower()[:40]
    project_dir = os.path.join(PROJECTS_DIR, f"{DATE}_{project_slug}")
    images_dir = os.path.join(project_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    total = len(PROMPTS) * len(strengths)
    print(f"LoRA test: {project_name}")
    print(f"  LoRA: {lora}")
    print(f"  {len(PROMPTS)} prompts x {len(strengths)} strengths = {total} images")
    print(f"  Project dir: {project_dir}\n")

    # Submit all jobs
    jobs = []  # (prompt_id, entry_dict)
    idx = 0
    for pi, prompt_text in enumerate(PROMPTS):
        prompt_slug = cc.slugify(prompt_text)
        for si, strength in enumerate(strengths):
            str_label = f"{strength:.2f}".replace(".", "")
            prefix = f"{DATE}_lt_{project_slug}_s{str_label}_{prompt_slug}"
            # Local filename: p{index}_s{strength}.png (clean, predictable)
            local_name = f"p{pi:02d}_s{str_label}.png"

            wf = build_workflow(prompt_text, lora, strength, BASE_SEED + pi, prefix)
            if idx == 0:
                cc.validate(wf, oi)
            payload = json.dumps({"prompt": wf}).encode("utf-8")
            req = urllib.request.Request(
                f"{COMFY_URL}/prompt", data=payload,
                headers={"Content-Type": "application/json"})
            resp = urllib.request.urlopen(req)
            result = json.loads(resp.read())
            pid = result["prompt_id"]

            entry = {
                "prompt_id": pid,
                "prompt": prompt_text,
                "prompt_index": pi,
                "strength": strength,
                "seed": BASE_SEED + pi,
                "comfy_prefix": prefix,
                "comfy_filename": None,
                "local_filename": local_name,
            }
            jobs.append((pid, entry))
            idx += 1
            print(f"  [{idx:3d}/{total}] Queued: str={strength:.2f}  {prompt_slug}")

    print(f"\nAll {total} queued. Waiting for completion...\n")

    # Poll for completion and download images
    completed = set()
    manifest_entries = []
    while len(completed) < len(jobs):
        for pid, entry in jobs:
            if pid in completed:
                continue
            resp = urllib.request.urlopen(f"{COMFY_URL}/history/{pid}")
            history = json.loads(resp.read())
            if pid in history:
                status = history[pid].get("status", {})
                if status.get("completed") or status.get("status_str") in ("success", "error"):
                    ok = status.get("status_str") == "success"
                    if ok:
                        outputs = history[pid].get("outputs", {})
                        for node_id, node_out in outputs.items():
                            images = node_out.get("images", [])
                            if images:
                                comfy_fn = images[0]["filename"]
                                subfolder = images[0].get("subfolder", "")
                                entry["comfy_filename"] = comfy_fn
                                # Download to project folder
                                dest = os.path.join(images_dir, entry["local_filename"])
                                try:
                                    download_image(comfy_fn, subfolder, dest)
                                except Exception as ex:
                                    print(f"  WARN: Failed to download {comfy_fn}: {ex}")
                    tag = "Done" if ok else "FAIL"
                    completed.add(pid)
                    manifest_entries.append(entry)
                    if len(completed) % 5 == 0 or len(completed) == len(jobs):
                        print(f"  Progress: {len(completed)}/{len(jobs)}")
        if len(completed) < len(jobs):
            time.sleep(2)

    # Write manifest
    manifest = {
        "version": 1,
        "name": project_name,
        "date": DATE,
        "lora": lora,
        "lora_slug": lora_slug,
        "strengths": strengths,
        "prompts": PROMPTS,
        "base_seed": BASE_SEED,
        "settings": {
            "width": WIDTH,
            "height": HEIGHT,
            "steps": STEPS,
            "guidance": GUIDANCE,
            "sampler": "euler",
            "scheduler": "Flux2Scheduler",
            "model": MODELS["unet"],
            "clip": MODELS["text_encoder"],
            "vae": MODELS["vae"],
            "turbo_lora": MODELS["turbo_lora"] if USE_TURBO_LORA else None,
        },
        "notes": args.notes,
        "images": sorted(manifest_entries, key=lambda e: (e["prompt_index"], e["strength"])),
    }
    manifest_path = os.path.join(project_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nProject saved: {project_dir}")
    print(f"  Manifest: {manifest_path}")
    print(f"  Images:   {images_dir}")
    print(f"  {len(completed)}/{total} complete.")
    print(f"\nOpen gallery.html and load this project folder to browse results.")

if __name__ == "__main__":
    main()

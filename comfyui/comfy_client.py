"""
ComfyUI local client: Flux 2 Dev (text-to-image) + WAN 2.2 14B (image-to-video).

Stdlib only (Python 3.8+). Designed for ComfyUI Desktop on Windows, but works
with any ComfyUI install. It never downloads models: it discovers what is
installed via /object_info and refuses to run if something required is missing.

Usage:
    python comfyui/comfy_client.py discover
    python comfyui/comfy_client.py t2i "a cable-stayed bridge at dawn" --width 1344 --height 768
    python comfyui/comfy_client.py i2v path/to/image.png "slow camera push-in, fog drifting"
    python comfyui/comfy_client.py t2i2v "a harbour crane at sunset" --motion "camera orbits slowly"
    python comfyui/comfy_client.py t2i "..." --save-workflow flux2_t2i   # writes flux2_t2i_workflow-api.json

Server URL: $COMFY_URL if set, otherwise the first of http://127.0.0.1:8000
(ComfyUI Desktop default) and http://127.0.0.1:8188 (portable/manual default) that answers.
"""
import argparse, json, os, random, sys, time, uuid, urllib.error, urllib.parse, urllib.request
from datetime import date

CANDIDATE_URLS = ["http://127.0.0.1:8000", "http://127.0.0.1:8188"]
DATE = date.today().strftime("%Y%m%d")

WAN_NEGATIVE = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，"
    "JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，"
    "手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)  # official Wan negative prompt, copied from the Comfy-Org template


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _get(base, path, timeout=30):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return r.read()


def get_json(base, path, timeout=30):
    return json.loads(_get(base, path, timeout))


def find_server():
    urls = [os.environ["COMFY_URL"].rstrip("/")] if os.environ.get("COMFY_URL") else CANDIDATE_URLS
    for url in urls:
        try:
            get_json(url, "/system_stats", timeout=3)
            return url
        except Exception:
            continue
    sys.exit("ComfyUI is not reachable at " + ", ".join(urls) +
             ". Start ComfyUI Desktop (or set COMFY_URL to the address shown in its settings).")


def submit(base, workflow):
    payload = json.dumps({"prompt": workflow, "client_id": str(uuid.uuid4())}).encode("utf-8")
    req = urllib.request.Request(base + "/prompt", data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        sys.exit(f"/prompt rejected the workflow (HTTP {e.code}):\n{e.read().decode('utf-8', 'replace')}")


def wait_for_job(base, prompt_id, timeout, interval=2):
    start = time.time()
    while time.time() - start < timeout:
        hist = get_json(base, f"/history/{prompt_id}")
        if prompt_id in hist:
            entry = hist[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                sys.exit(f"Job {prompt_id} failed: {json.dumps(msgs, indent=2)[:3000]}")
            if status.get("completed") or status.get("status_str") == "success":
                return entry
        time.sleep(interval)
    sys.exit(f"Job {prompt_id} timed out after {timeout}s (it may still be running; check the ComfyUI queue).")


def output_files(entry):
    """[(filename, subfolder, type)] for every image/video the job saved."""
    files = []
    for node_out in entry.get("outputs", {}).values():
        for key in ("images", "videos", "gifs", "files"):
            for f in node_out.get(key, []):
                if isinstance(f, dict) and "filename" in f:
                    files.append((f["filename"], f.get("subfolder", ""), f.get("type", "output")))
    return files


def upload_image(base, data, filename):
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + (
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue"
        f"\r\n--{boundary}--\r\n"
    ).encode()
    req = urllib.request.Request(base + "/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req) as r:
        res = json.loads(r.read())
    return f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]


# ── Model discovery ──────────────────────────────────────────────────────────

def options(object_info, node, input_name):
    """Dropdown options, handling legacy [[...], {}] and wrapped ["COMBO", {options}] formats."""
    spec = object_info.get(node, {}).get("input", {}).get("required", {}).get(input_name)
    if not spec:
        return []
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1:
        return spec[1].get("options", [])
    return []


def _pick(names, must, prefer=(), avoid=()):
    """Pick the file whose lowercased name contains every `must` token; rank by `prefer`, drop `avoid`."""
    low = lambda s: s.lower().replace("\\", "/")
    hits = [n for n in names if all(t in low(n) for t in must) and not any(t in low(n) for t in avoid)]
    hits.sort(key=lambda n: (-sum(t in low(n) for t in prefer), len(n)))
    return hits[0] if hits else None


def discover(object_info):
    unets = options(object_info, "UNETLoader", "unet_name")
    clips = options(object_info, "CLIPLoader", "clip_name")
    vaes = options(object_info, "VAELoader", "vae_name")
    loras = options(object_info, "LoraLoaderModelOnly", "lora_name")
    return {
        "flux2": {
            "unet": _pick(unets, ["flux2"], prefer=["dev"], avoid=["klein"]) or _pick(unets, ["flux.2"], avoid=["klein"]),
            "text_encoder": _pick(clips, ["mistral"], prefer=["flux2"]),
            "vae": _pick(vaes, ["flux2"]) or _pick(vaes, ["full_encoder_small_decoder"]),
            "turbo_lora": _pick(loras, ["flux", "2", "turbo"]),
        },
        "wan22_i2v": {
            "unet_high": _pick(unets, ["wan2.2", "i2v", "high"]),
            "unet_low": _pick(unets, ["wan2.2", "i2v", "low"]),
            "text_encoder": _pick(clips, ["umt5"], prefer=["fp8", "scaled"]),
            # 14B uses the Wan 2.1 VAE; wan2.2_vae is only for the 5B model
            "vae": _pick(vaes, ["wan_2.1_vae"]) or _pick(vaes, ["wan2.1", "vae"]),
            "lightx2v_high": _pick(loras, ["i2v", "lightx2v", "high"]),
            "lightx2v_low": _pick(loras, ["i2v", "lightx2v", "low"]),
        },
        "_all": {"unet": unets, "clip": clips, "vae": vaes, "lora": loras},
    }


def require(models, keys, pipeline):
    missing = [k for k in keys if not models[k]]
    if missing:
        sys.exit(f"{pipeline}: required model(s) not found on the server: {', '.join(missing)}.\n"
                 "Run `python comfyui/comfy_client.py discover` to see what's installed. "
                 "This client never downloads models.")


def validate(workflow, object_info):
    """Check node types exist, required inputs are present, and dropdown values are valid."""
    problems = []
    for nid, node in workflow.items():
        spec = object_info.get(node["class_type"])
        if spec is None:
            problems.append(f"node {nid}: {node['class_type']} is not installed")
            continue
        for name, ispec in spec["input"].get("required", {}).items():
            if name not in node["inputs"]:
                problems.append(f"node {nid} ({node['class_type']}): missing input '{name}'")
                continue
            val = node["inputs"][name]
            if node["class_type"] == "LoadImage":  # uploaded after /object_info was fetched
                continue
            opts = options(object_info, node["class_type"], name)
            if opts and not isinstance(val, list) and val not in opts:
                problems.append(f"node {nid} ({node['class_type']}): '{name}'={val!r} not in {opts[:12]}")
    if problems:
        sys.exit("Workflow does not match this ComfyUI install:\n  " + "\n  ".join(problems))


# ── Pipelines ────────────────────────────────────────────────────────────────

def build_flux2_t2i(m, prompt, prefix, width=1024, height=1024, seed=None, steps=None,
                    guidance=4.0, use_turbo_lora=True, weight_dtype="default"):
    """Flux 2 Dev text-to-image, mirroring Comfy-Org workflow_templates/image_flux2_text_to_image.json."""
    turbo = use_turbo_lora and m.get("turbo_lora")
    steps = steps or (8 if turbo else 20)
    seed = random.randint(0, 2**50) if seed is None else seed
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": m["unet"], "weight_dtype": weight_dtype}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["text_encoder"], "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "FluxGuidance", "inputs": {"guidance": guidance, "conditioning": ["4", 0]}},
        "6": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["5", 0]}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "9": {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": width, "height": height}},
        "10": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["7", 0], "guider": ["6", 0], "sampler": ["8", 0], "sigmas": ["9", 0], "latent_image": ["10", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["12", 0]}},
    }
    if turbo:
        wf["14"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": m["turbo_lora"], "strength_model": 1.0}}
        wf["6"]["inputs"]["model"] = ["14", 0]
    return wf


def build_wan22_i2v(m, image_name, prompt, prefix, width=640, height=640, length=81, fps=16,
                    seed=None, negative=WAN_NEGATIVE, use_lightx2v=True):
    """WAN 2.2 14B image-to-video (two-expert high/low noise), mirroring
    Comfy-Org workflow_templates/video_wan2_2_14B_i2v.json."""
    lightning = use_lightx2v and m.get("lightx2v_high") and m.get("lightx2v_low")
    steps, cfg, split = (4, 1.0, 2) if lightning else (20, 3.5, 10)
    seed = random.randint(0, 2**50) if seed is None else seed
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": m["unet_high"], "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": m["unet_low"], "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["text_encoder"], "type": "wan", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["3", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["3", 0]}},
        "8": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 5.0}},
        "9": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 5.0}},
        "10": {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["6", 0], "negative": ["7", 0], "vae": ["4", 0], "start_image": ["5", 0],
            "width": width, "height": height, "length": length, "batch_size": 1}},
        "11": {"class_type": "KSamplerAdvanced", "inputs": {
            "add_noise": "enable", "noise_seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": "euler", "scheduler": "simple", "start_at_step": 0, "end_at_step": split,
            "return_with_leftover_noise": "enable",
            "model": ["8", 0], "positive": ["10", 0], "negative": ["10", 1], "latent_image": ["10", 2]}},
        "12": {"class_type": "KSamplerAdvanced", "inputs": {
            "add_noise": "disable", "noise_seed": 0, "steps": steps, "cfg": cfg,
            "sampler_name": "euler", "scheduler": "simple", "start_at_step": split, "end_at_step": 10000,
            "return_with_leftover_noise": "disable",
            "model": ["9", 0], "positive": ["10", 0], "negative": ["10", 1], "latent_image": ["11", 0]}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "fps": float(fps)}},
        "15": {"class_type": "SaveVideo", "inputs": {
            # "auto"/"auto" = mp4 + h264, and is valid on both the old flat SaveVideo schema and
            # the newer nested (DynamicCombo) one
            "video": ["14", 0], "filename_prefix": prefix, "format": "auto", "codec": "auto"}},
    }
    if lightning:
        wf["16"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": m["lightx2v_high"], "strength_model": 1.0}}
        wf["17"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["2", 0], "lora_name": m["lightx2v_low"], "strength_model": 1.0}}
        wf["8"]["inputs"]["model"] = ["16", 0]
        wf["9"]["inputs"]["model"] = ["17", 0]
    return wf


# ── CLI ──────────────────────────────────────────────────────────────────────

def slugify(text, n=40):
    keep = "".join(c if c.isalnum() else "-" for c in text.lower())
    return "-".join(p for p in keep.split("-") if p)[:n].strip("-") or "untitled"


def run(base, object_info, wf, timeout, save_workflow=None):
    validate(wf, object_info)
    if save_workflow:
        with open(f"{save_workflow}_workflow-api.json", "w", encoding="utf-8") as f:
            json.dump({"prompt": wf}, f, indent=2, ensure_ascii=False)
    pid = submit(base, wf)
    print(f"submitted {pid}; waiting...", flush=True)
    entry = wait_for_job(base, pid, timeout)
    files = output_files(entry)
    for name, sub, typ in files:
        print(f"saved: {typ}/{sub + '/' if sub else ''}{name}")
    return files


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover", help="show server, paths and the models each pipeline would use")

    def common(sp):
        sp.add_argument("--seed", type=int)
        sp.add_argument("--prefix", help="filename prefix (default: YYYYMMDD_<slug>)")
        sp.add_argument("--save-workflow", metavar="NAME", help="also write NAME_workflow-api.json")

    t = sub.add_parser("t2i", help="Flux 2 Dev text-to-image"); common(t)
    t.add_argument("prompt")
    t.add_argument("--width", type=int, default=1024); t.add_argument("--height", type=int, default=1024)
    t.add_argument("--steps", type=int); t.add_argument("--guidance", type=float, default=4.0)
    t.add_argument("--no-turbo", action="store_true", help="ignore the Flux 2 Turbo LoRA even if installed")
    t.add_argument("--weight-dtype", default="default", help="e.g. fp8_e4m3fn for the full bf16 checkpoint on <48 GB VRAM")

    v = sub.add_parser("i2v", help="WAN 2.2 14B image-to-video"); common(v)
    v.add_argument("image", help="local image path, or name of a file already in ComfyUI's input folder")
    v.add_argument("prompt")
    v.add_argument("--width", type=int, default=640); v.add_argument("--height", type=int, default=640)
    v.add_argument("--seconds", type=float, default=5.0); v.add_argument("--fps", type=int, default=16)
    v.add_argument("--no-lightx2v", action="store_true", help="ignore 4-step LoRAs even if installed")

    tv = sub.add_parser("t2i2v", help="Flux 2 image, then animate it with WAN 2.2"); common(tv)
    tv.add_argument("prompt"); tv.add_argument("--motion", help="video prompt (default: same as prompt)")
    tv.add_argument("--width", type=int, default=832); tv.add_argument("--height", type=int, default=480)
    tv.add_argument("--seconds", type=float, default=5.0); tv.add_argument("--fps", type=int, default=16)

    a = p.parse_args()
    base = find_server()
    oi = get_json(base, "/object_info", timeout=120)
    found = discover(oi)

    if a.cmd == "discover":
        stats = get_json(base, "/system_stats")
        print(f"server: {base}")
        print(json.dumps({k: stats.get(k) for k in ("system", "devices")}, indent=2))
        try:
            print("model folders:", json.dumps(get_json(base, "/internal/folder_paths"), indent=2))
        except Exception:
            print("model folders: (endpoint not available on this version)")
        print("\nselected models:")
        print(json.dumps({k: v for k, v in found.items() if k != "_all"}, indent=2))
        print("\nall installed (UNETLoader / CLIPLoader / VAELoader / LoRAs):")
        print(json.dumps(found["_all"], indent=2))
        return

    if a.cmd in ("t2i", "t2i2v"):
        require(found["flux2"], ["unet", "text_encoder", "vae"], "Flux 2 Dev")
    if a.cmd in ("i2v", "t2i2v"):
        require(found["wan22_i2v"], ["unet_high", "unet_low", "text_encoder", "vae"], "WAN 2.2 i2v")

    slug = slugify(a.prompt)
    if a.cmd == "t2i":
        wf = build_flux2_t2i(found["flux2"], a.prompt, a.prefix or f"{DATE}_{slug}", a.width, a.height,
                             a.seed, a.steps, a.guidance, not a.no_turbo, a.weight_dtype)
        run(base, oi, wf, timeout=900, save_workflow=a.save_workflow)
        return

    if a.cmd == "t2i2v":
        wf = build_flux2_t2i(found["flux2"], a.prompt, f"{DATE}_{slug}", a.width, a.height, a.seed)
        files = run(base, oi, wf, timeout=900)
        name, subf, typ = files[0]
        q = urllib.parse.urlencode({"filename": name, "subfolder": subf, "type": typ})
        image_name = upload_image(base, _get(base, f"/view?{q}", timeout=120), name)
        video_prompt = a.motion or a.prompt
    else:
        if os.path.isfile(a.image):
            with open(a.image, "rb") as f:
                image_name = upload_image(base, f.read(), os.path.basename(a.image))
        else:
            image_name = a.image
        video_prompt = a.prompt

    length = int(a.seconds * a.fps) // 4 * 4 + 1  # Wan needs 4k+1 frames
    wf = build_wan22_i2v(found["wan22_i2v"], image_name, video_prompt, a.prefix or f"video/{DATE}_{slug}",
                         a.width, a.height, length, a.fps, a.seed,
                         use_lightx2v=not getattr(a, "no_lightx2v", False))
    run(base, oi, wf, timeout=3600, save_workflow=a.save_workflow)


if __name__ == "__main__":
    main()

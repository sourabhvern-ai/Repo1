"""
Generates comfyui_flux2_wan22.json, an n8n workflow that drives the local ComfyUI:
Form -> Flux 2 Dev image -> (optionally) WAN 2.2 14B i2v video.

Same pipelines as comfyui/comfy_client.py. Models are picked from /object_info at run time
and never downloaded. Re-run this script after editing to regenerate the JSON:

    python comfyui/n8n/build_n8n_workflow.py
"""
import json, os, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "comfyui_flux2_wan22.json")

# ── Code node sources (JavaScript) ───────────────────────────────────────────

JS_SHARED = r"""
// Dropdown options from /object_info (legacy [[...], {}] or wrapped ["COMBO", {options}])
function options(oi, node, input) {
  const spec = oi?.[node]?.input?.required?.[input];
  if (!spec) return [];
  if (Array.isArray(spec[0])) return spec[0];
  if (spec[0] === 'COMBO' && spec[1]) return spec[1].options || [];
  return [];
}
// First file containing every `must` token, ranked by `prefer`, excluding `avoid`
function pick(names, must, prefer = [], avoid = []) {
  const low = s => s.toLowerCase().replace(/\\/g, '/');
  const hits = names.filter(n => must.every(t => low(n).includes(t)) && !avoid.some(t => low(n).includes(t)));
  hits.sort((a, b) => (prefer.filter(t => low(b).includes(t)).length - prefer.filter(t => low(a).includes(t)).length) || a.length - b.length);
  return hits[0] || null;
}
function slugify(t) {
  return (t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/, '')) || 'untitled';
}
"""

JS_BUILD_FLUX = JS_SHARED + r"""
const oi = $input.first().json;
const cfg = $('Config').first().json;
const form = $('Generate form').first().json;

const unets = options(oi, 'UNETLoader', 'unet_name');
const clips = options(oi, 'CLIPLoader', 'clip_name');
const vaes  = options(oi, 'VAELoader', 'vae_name');
const loras = options(oi, 'LoraLoaderModelOnly', 'lora_name');

const flux = {
  unet: pick(unets, ['flux2'], ['dev'], ['klein']) || pick(unets, ['flux.2'], [], ['klein']),
  te:   pick(clips, ['mistral'], ['flux2']),
  vae:  pick(vaes, ['flux2']) || pick(vaes, ['full_encoder_small_decoder']),
  turbo: cfg.useSpeedLoras ? pick(loras, ['flux', '2', 'turbo']) : null,
};
const wan = {
  high: pick(unets, ['wan2.2', 'i2v', 'high']),
  low:  pick(unets, ['wan2.2', 'i2v', 'low']),
  te:   pick(clips, ['umt5'], ['fp8', 'scaled']),
  vae:  pick(vaes, ['wan_2.1_vae']) || pick(vaes, ['wan2.1', 'vae']),
  loraHigh: cfg.useSpeedLoras ? pick(loras, ['i2v', 'lightx2v', 'high']) : null,
  loraLow:  cfg.useSpeedLoras ? pick(loras, ['i2v', 'lightx2v', 'low']) : null,
};

const wantVideo = form['Mode'] === 'Image + video';
const missing = Object.entries({ 'Flux 2 UNet': flux.unet, 'Flux 2 text encoder (mistral)': flux.te, 'Flux 2 VAE': flux.vae })
  .concat(wantVideo ? Object.entries({ 'WAN 2.2 i2v high-noise': wan.high, 'WAN 2.2 i2v low-noise': wan.low,
                                       'WAN text encoder (umt5)': wan.te, 'WAN 2.1 VAE': wan.vae }) : [])
  .filter(([, v]) => !v).map(([k]) => k);
if (missing.length) {
  throw new Error('Not installed in ComfyUI (this workflow never downloads models): ' + missing.join(', '));
}
if (!options(oi, 'CLIPLoader', 'type').includes('flux2') || !oi['Flux2Scheduler']) {
  throw new Error('This ComfyUI build has no Flux 2 support. Update ComfyUI Desktop.');
}

// Image / video sizes per aspect choice (multiples of 16)
const sizes = {
  'Landscape': { img: [1344, 768], vid: [832, 480] },
  'Portrait':  { img: [768, 1344], vid: [480, 832] },
  'Square':    { img: [1024, 1024], vid: [640, 640] },
};
const size = sizes[(form['Aspect'] || 'Landscape').split(' ')[0]] || sizes['Landscape'];
const [W, H] = size.img;
const seed = Math.floor(Math.random() * 2 ** 48);
const date = new Date().toISOString().slice(0, 10).replace(/-/g, '');
const prefix = `${date}_${slugify(form['Prompt'])}`;

const wf = {
  '1':  { class_type: 'UNETLoader', inputs: { unet_name: flux.unet, weight_dtype: cfg.fluxWeightDtype } },
  '2':  { class_type: 'CLIPLoader', inputs: { clip_name: flux.te, type: 'flux2', device: 'default' } },
  '3':  { class_type: 'VAELoader', inputs: { vae_name: flux.vae } },
  '4':  { class_type: 'CLIPTextEncode', inputs: { text: form['Prompt'], clip: ['2', 0] } },
  '5':  { class_type: 'FluxGuidance', inputs: { guidance: 4.0, conditioning: ['4', 0] } },
  '6':  { class_type: 'BasicGuider', inputs: { model: ['1', 0], conditioning: ['5', 0] } },
  '7':  { class_type: 'RandomNoise', inputs: { noise_seed: seed } },
  '8':  { class_type: 'KSamplerSelect', inputs: { sampler_name: 'euler' } },
  '9':  { class_type: 'Flux2Scheduler', inputs: { steps: flux.turbo ? 8 : 20, width: W, height: H } },
  '10': { class_type: 'EmptyFlux2LatentImage', inputs: { width: W, height: H, batch_size: 1 } },
  '11': { class_type: 'SamplerCustomAdvanced', inputs: { noise: ['7', 0], guider: ['6', 0], sampler: ['8', 0], sigmas: ['9', 0], latent_image: ['10', 0] } },
  '12': { class_type: 'VAEDecode', inputs: { samples: ['11', 0], vae: ['3', 0] } },
  '13': { class_type: 'SaveImage', inputs: { filename_prefix: prefix, images: ['12', 0] } },
};
if (flux.turbo) {
  wf['14'] = { class_type: 'LoraLoaderModelOnly', inputs: { model: ['1', 0], lora_name: flux.turbo, strength_model: 1.0 } };
  wf['6'].inputs.model = ['14', 0];
}

return [{ json: { fluxWorkflow: wf, flux, wan, wantVideo, videoSize: size.vid, seed, prefix } }];
"""


def js_check(submit_node, max_polls, label):
    return r"""
// Poll result: {} while queued/running, {<id>: {...}} once finished
const pid = $('%s').first().json.prompt_id;
const entry = $input.first().json[pid];
if (!entry) {
  if ($runIndex >= %d) throw new Error('%s job ' + pid + ' timed out. Check the ComfyUI queue.');
  return [{ json: { done: false } }];
}
const status = entry.status || {};
if (status.status_str === 'error') {
  const err = (status.messages || []).filter(m => m[0] === 'execution_error').map(m => m[1]);
  throw new Error('%s job failed: ' + JSON.stringify(err).slice(0, 2000));
}
const files = [];
for (const out of Object.values(entry.outputs || {})) {
  for (const key of ['images', 'videos', 'gifs', 'files']) {
    for (const f of out[key] || []) if (f && f.filename) files.push({ filename: f.filename, subfolder: f.subfolder || '', type: f.type || 'output' });
  }
}
return [{ json: { done: true, prompt_id: pid, files } }];
""" % (submit_node, max_polls, label, label)


JS_BUILD_WAN = r"""
const job = $('Build Flux 2 workflow').first().json;
const cfg = $('Config').first().json;
const form = $('Generate form').first().json;
const img = $input.first().json.files[0];
if (!img) throw new Error('Flux 2 job finished but saved no image.');

// LoadImage can read straight from the output folder with the " [output]" suffix
const imageRef = `${img.subfolder ? img.subfolder + '/' : ''}${img.filename} [output]`;
const m = job.wan;
const lightning = !!(m.loraHigh && m.loraLow);
const [steps, cfgScale, split] = lightning ? [4, 1.0, 2] : [20, 3.5, 10];
const fps = Number(cfg.videoFps);
const length = Math.floor(Number(cfg.videoSeconds) * fps / 4) * 4 + 1;   // WAN needs 4k+1 frames
const [W, H] = job.videoSize;
const negative = '色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走';

const wf = {
  '1':  { class_type: 'UNETLoader', inputs: { unet_name: m.high, weight_dtype: 'default' } },
  '2':  { class_type: 'UNETLoader', inputs: { unet_name: m.low, weight_dtype: 'default' } },
  '3':  { class_type: 'CLIPLoader', inputs: { clip_name: m.te, type: 'wan', device: 'default' } },
  '4':  { class_type: 'VAELoader', inputs: { vae_name: m.vae } },
  '5':  { class_type: 'LoadImage', inputs: { image: imageRef } },
  '6':  { class_type: 'CLIPTextEncode', inputs: { text: form['Motion prompt'] || form['Prompt'], clip: ['3', 0] } },
  '7':  { class_type: 'CLIPTextEncode', inputs: { text: negative, clip: ['3', 0] } },
  '8':  { class_type: 'ModelSamplingSD3', inputs: { model: ['1', 0], shift: 5.0 } },
  '9':  { class_type: 'ModelSamplingSD3', inputs: { model: ['2', 0], shift: 5.0 } },
  '10': { class_type: 'WanImageToVideo', inputs: { positive: ['6', 0], negative: ['7', 0], vae: ['4', 0], start_image: ['5', 0],
                                                   width: W, height: H, length, batch_size: 1 } },
  '11': { class_type: 'KSamplerAdvanced', inputs: { add_noise: 'enable', noise_seed: job.seed, steps, cfg: cfgScale,
          sampler_name: 'euler', scheduler: 'simple', start_at_step: 0, end_at_step: split, return_with_leftover_noise: 'enable',
          model: ['8', 0], positive: ['10', 0], negative: ['10', 1], latent_image: ['10', 2] } },
  '12': { class_type: 'KSamplerAdvanced', inputs: { add_noise: 'disable', noise_seed: 0, steps, cfg: cfgScale,
          sampler_name: 'euler', scheduler: 'simple', start_at_step: split, end_at_step: 10000, return_with_leftover_noise: 'disable',
          model: ['9', 0], positive: ['10', 0], negative: ['10', 1], latent_image: ['11', 0] } },
  '13': { class_type: 'VAEDecode', inputs: { samples: ['12', 0], vae: ['4', 0] } },
  '14': { class_type: 'CreateVideo', inputs: { images: ['13', 0], fps } },
  // "auto"/"auto" = mp4 + h264; valid on both the old flat and the new nested SaveVideo schema
  '15': { class_type: 'SaveVideo', inputs: { video: ['14', 0], filename_prefix: 'video/' + job.prefix, format: 'auto', codec: 'auto' } },
};
if (lightning) {
  wf['16'] = { class_type: 'LoraLoaderModelOnly', inputs: { model: ['1', 0], lora_name: m.loraHigh, strength_model: 1.0 } };
  wf['17'] = { class_type: 'LoraLoaderModelOnly', inputs: { model: ['2', 0], lora_name: m.loraLow, strength_model: 1.0 } };
  wf['8'].inputs.model = ['16', 0];
  wf['9'].inputs.model = ['17', 0];
}
return [{ json: { wanWorkflow: wf, image: imageRef, frames: length, fps, lightning } }];
"""

# ── Node helpers ─────────────────────────────────────────────────────────────

nodes, connections = [], {}


def node(name, type_, version, params, pos, **extra):
    n = {"parameters": params, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "comfy-n8n/" + name)),
         "name": name, "type": type_, "typeVersion": version, "position": pos}
    n.update(extra)
    nodes.append(n)
    return name


def connect(src, dst, src_output=0):
    outs = connections.setdefault(src, {"main": []})["main"]
    while len(outs) <= src_output:
        outs.append([])
    outs[src_output].append({"node": dst, "type": "main", "index": 0})


def code(name, js, pos):
    return node(name, "n8n-nodes-base.code", 2, {"jsCode": js.strip() + "\n"}, pos)


def http(name, method, url, pos, json_body=None, note=None):
    p = {"method": method, "url": url, "options": {"timeout": 120000}}
    if json_body:
        p.update({"sendBody": True, "specifyBody": "json", "jsonBody": json_body})
    extra = {"notesInFlow": True, "notes": note} if note else {}
    return node(name, "n8n-nodes-base.httpRequest", 4.2, p, pos, **extra)


def wait(name, seconds, pos):
    return node(name, "n8n-nodes-base.wait", 1.1, {"amount": seconds, "unit": "seconds"}, pos,
                webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, "comfy-n8n-wh/" + name)))


def if_node(name, left, operator, pos, right=None):
    cond = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "comfy-n8n-if/" + name)), "leftValue": left, "operator": operator}
    if right is not None:
        cond["rightValue"] = right
    return node(name, "n8n-nodes-base.if", 2.2, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                       "conditions": [cond], "combinator": "and"},
        "options": {}}, pos)


def set_node(name, fields, pos):
    return node(name, "n8n-nodes-base.set", 3.4, {
        "assignments": {"assignments": [
            {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"comfy-n8n-set/{name}/{k}")), "name": k, "value": v, "type": t}
            for k, v, t in fields]},
        "options": {}}, pos)


IS_TRUE = {"type": "boolean", "operation": "true", "singleValue": True}
URL = "={{ $('Config').first().json.comfyUrl }}"

# ── Graph ────────────────────────────────────────────────────────────────────

form = node("Generate form", "n8n-nodes-base.formTrigger", 2.2, {
    "formTitle": "ComfyUI: Flux 2 Dev + WAN 2.2",
    "formDescription": "Generates an image with Flux 2 Dev and, optionally, animates it with WAN 2.2 i2v. "
                       "Results are saved in ComfyUI's output folder. Uses installed models only.",
    "formFields": {"values": [
        {"fieldLabel": "Prompt", "fieldType": "textarea", "requiredField": True,
         "placeholder": "a steel arch bridge over a river at dawn, photorealistic"},
        {"fieldLabel": "Mode", "fieldType": "dropdown", "requiredField": True,
         "fieldOptions": {"values": [{"option": "Image only"}, {"option": "Image + video"}]}},
        {"fieldLabel": "Aspect", "fieldType": "dropdown", "requiredField": True,
         "fieldOptions": {"values": [{"option": "Landscape (1344x768 / video 832x480)"},
                                     {"option": "Portrait (768x1344 / video 480x832)"},
                                     {"option": "Square (1024x1024 / video 640x640)"}]}},
        {"fieldLabel": "Motion prompt",  "fieldType": "textarea",
         "placeholder": "video only: e.g. slow aerial drift forward, mist moving over the water"},
    ]},
    "options": {},
}, [0, 300], webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, "comfy-n8n-form")))

cfg = set_node("Config", [
    ("comfyUrl", "http://127.0.0.1:8000", "string"),
    ("fluxWeightDtype", "default", "string"),
    ("useSpeedLoras", True, "boolean"),
    ("videoSeconds", 5, "number"),
    ("videoFps", 16, "number"),
], [220, 300])
nodes[-1]["notesInFlow"] = True
nodes[-1]["notes"] = ("comfyUrl: ComfyUI Desktop = :8000, manual install = :8188. If n8n runs in Docker use "
                      "http://host.docker.internal:8000. fluxWeightDtype: fp8_e4m3fn for the full bf16 flux2-dev on <48 GB VRAM.")

oi = http("Get object_info", "GET", URL + "/object_info", [440, 300], note="discovers installed models")
bflux = code("Build Flux 2 workflow", JS_BUILD_FLUX, [660, 300])
sflux = http("Submit Flux 2", "POST", URL + "/prompt", [880, 300],
             json_body="={{ JSON.stringify({ prompt: $json.fluxWorkflow }) }}")
wflux = wait("Wait (image)", 5, [1100, 300])
hflux = http("Get Flux 2 history", "GET", URL + "/history/{{ $('Submit Flux 2').first().json.prompt_id }}", [1320, 300])
cflux = code("Check Flux 2 status", js_check("Submit Flux 2", 180, "Flux 2"), [1540, 300])
iflux = if_node("Image done?", "={{ $json.done }}", IS_TRUE, [1760, 300])
imode = if_node("Video wanted?", "={{ $('Build Flux 2 workflow').first().json.wantVideo }}", IS_TRUE, [1980, 200])
done_img = set_node("Image result", [
    ("image", "={{ $('Check Flux 2 status').last().json.files.map(f => (f.subfolder ? f.subfolder + '/' : '') + f.filename).join(', ') }}", "string"),
    ("savedIn", "ComfyUI output folder", "string"),
], [2200, 400])
bwan = code("Build WAN 2.2 workflow", JS_BUILD_WAN, [2200, 100])
swan = http("Submit WAN 2.2", "POST", URL + "/prompt", [2420, 100],
            json_body="={{ JSON.stringify({ prompt: $json.wanWorkflow }) }}")
wwan = wait("Wait (video)", 10, [2640, 100])
hwan = http("Get WAN 2.2 history", "GET", URL + "/history/{{ $('Submit WAN 2.2').first().json.prompt_id }}", [2860, 100])
cwan = code("Check WAN 2.2 status", js_check("Submit WAN 2.2", 360, "WAN 2.2"), [3080, 100])
iwan = if_node("Video done?", "={{ $json.done }}", IS_TRUE, [3300, 100])
done_vid = set_node("Video result", [
    ("image", "={{ $('Build WAN 2.2 workflow').first().json.image }}", "string"),
    ("video", "={{ $json.files.map(f => (f.subfolder ? f.subfolder + '/' : '') + f.filename).join(', ') }}", "string"),
    ("savedIn", "ComfyUI output folder", "string"),
], [3520, 0])

for a, b in [(form, cfg), (cfg, oi), (oi, bflux), (bflux, sflux), (sflux, wflux), (wflux, hflux),
             (hflux, cflux), (cflux, iflux), (bwan, swan), (swan, wwan), (wwan, hwan), (hwan, cwan), (cwan, iwan)]:
    connect(a, b)
connect(iflux, imode, 0); connect(iflux, wflux, 1)       # not done -> wait again
connect(imode, bwan, 0);  connect(imode, done_img, 1)
connect(iwan, done_vid, 0); connect(iwan, wwan, 1)

workflow = {
    "name": "ComfyUI - Flux 2 Dev image + WAN 2.2 video",
    "nodes": nodes,
    "connections": connections,
    "pinData": {},
    "settings": {"executionOrder": "v1"},
    "active": False,
    "meta": {"templateCredsSetupCompleted": True},
    "tags": [],
}

if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(workflow, f, indent=2, ensure_ascii=False)
    print("wrote", OUT)

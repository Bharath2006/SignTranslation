import os
import re
import json
import uuid
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, send_file, abort
from PIL import Image
import io

try:
    from aksharamukha.transliterate import process as aksha_process
except Exception:
    aksha_process = None

try:
    import pytesseract
except Exception:
    pytesseract = None

app = Flask(__name__)
PHRASEBOOK_FILE = "phrasebooks.json"

UI_SCRIPTS = {
    "Devanagari (हिन्दी, मराठी, नेपाली)": "Devanagari",
    "Bengali (বাংলা)": "Bengali",
    "Gurmukhi (ਪੰਜਾਬੀ)": "Gurmukhi",
    "Gujarati (ગુજરાતી)": "Gujarati",
    "Odia (ଓଡ଼ିଆ)": "Oriya",
    "Tamil (தமிழ்)": "Tamil",
    "Telugu (తెలుగు)": "Telugu",
    "Kannada (ಕನ್ನಡ)": "Kannada",
    "Malayalam (മലയാളം)": "Malayalam",
    "Roman (Latin) — ISO/IAST": "ISO"
}

# Unicode ranges to detect scripts -> aksharamukha script name
UNICODE_RANGES = [
    (re.compile(r'[\u0900-\u097F]'), "Devanagari"),
    (re.compile(r'[\u0980-\u09FF]'), "Bengali"),
    (re.compile(r'[\u0A00-\u0A7F]'), "Gurmukhi"),
    (re.compile(r'[\u0A80-\u0AFF]'), "Gujarati"),
    (re.compile(r'[\u0B00-\u0B7F]'), "Oriya"),
    (re.compile(r'[\u0B80-\u0BFF]'), "Tamil"),
    (re.compile(r'[\u0C00-\u0C7F]'), "Telugu"),
    (re.compile(r'[\u0C80-\u0CFF]'), "Kannada"),
    (re.compile(r'[\u0D00-\u0D7F]'), "Malayalam"),
    (re.compile(r'[\u0D80-\u0DFF]'), "Sinhala"),
    (re.compile(r'[A-Za-z]'), "ISO"),
]

# Map detected script -> suggested tesseract language code(s)
# Values are strings accepted by pytesseract's 'lang' param (comma-separated allowed)
TESS_LANG_SUGGEST = {
    "Devanagari": "hin",     
    "Bengali": "ben",
    "Gurmukhi": "pan",
    "Gujarati": "guj",
    "Oriya": "ori",
    "Tamil": "tam",
    "Telugu": "tel",
    "Kannada": "kan",
    "Malayalam": "mal",
    "Sinhala": "sin",
    "ISO": "eng"
}

if not os.path.exists(PHRASEBOOK_FILE):
    with open(PHRASEBOOK_FILE, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)


def detect_script_with_confidence(text: str):
    """
    Detect predominant script and compute a simple confidence score:
    - Count characters matched per script
    - confidence = top_count / total_matched_chars
    Returns (detected_script, top_count, total_matched_chars, breakdown_dict)
    """
    if not text:
        return ("ISO", 0, 0, {})
    counts = {}
    total_matched = 0
    for pattern, scriptname in UNICODE_RANGES:
        found = pattern.findall(text)
        if found:
            counts[scriptname] = counts.get(scriptname, 0) + len(found)
            total_matched += len(found)
    if not counts:
        return ("ISO", 0, 0, {})
    top_script, top_count = max(counts.items(), key=lambda kv: kv[1])
    confidence = top_count / total_matched if total_matched > 0 else 0.0
    return (top_script, top_count, total_matched, counts, confidence)


def ocr_image_with_smart_lang(img: Image.Image):
    """
    Run Tesseract OCR on PIL image.
    Strategy:
    1. Run OCR with default language (eng) to get initial text
    2. Detect script from the initial extraction
    3. If detected script suggests a tesseract lang and it's not 'eng',
       attempt OCR again with that language for better extraction.
    Return dict: {'ok':True,'text':..., 'used_lang': 'hin', 'hint': '...'} or {'ok':False,'error':...}
    """
    if pytesseract is None:
        return {"ok": False, "error": "pytesseract not available. Install pytesseract and Tesseract."}
    try:
        txt0 = pytesseract.image_to_string(img)
    except Exception as e:
        return {"ok": False, "error": f"Tesseract initial OCR failed: {e}"}

    txt0 = txt0.strip()
    detected, top_count, total_matched, breakdown, _ = detect_script_with_confidence(txt0)
    suggested_lang = TESS_LANG_SUGGEST.get(detected, "eng")

    if suggested_lang != "eng":
        try:
            txt1 = pytesseract.image_to_string(img, lang=suggested_lang)
            txt1 = txt1.strip()
            if len(txt1) > len(txt0):
                used = suggested_lang
                final_text = txt1
            elif txt1.strip() != "" and txt0.strip() == "":
                used = suggested_lang
                final_text = txt1
            else:
                used = "eng" 
                final_text = txt0
        except Exception:
            used = "eng"
            final_text = txt0
    else:
        used = "eng"
        final_text = txt0

    return {"ok": True, "text": final_text, "used_lang": used, "detected_script": detected, "breakdown": breakdown}


def perform_transliteration(src_script, tgt_script, text):
    """
    Use Aksharamukha to transliterate text.
    Raises RuntimeError if aksharamukha not installed.
    """
    if aksha_process is None:
        raise RuntimeError("Aksharamukha is not installed on server. Install with `pip install aksharamukha`.")
    if src_script == "ISO":
        detected, top_count, total_matched, breakdown, _ = detect_script_with_confidence(text)
        if detected != "ISO":
            src_script = detected
    return aksha_process(src_script, tgt_script, text)


def load_phrasebooks():
    try:
        with open(PHRASEBOOK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_phrasebooks(pb_list):
    with open(PHRASEBOOK_FILE, "w", encoding="utf-8") as f:
        json.dump(pb_list, f, ensure_ascii=False, indent=2)


INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Script Transliterator — Bharat (Enhanced)</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body{padding:20px;background:#f8f9fa}
    .monospace{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, 'Roboto Mono', monospace;}
    #logArea{background:#f1f1f1;padding:10px;height:130px;overflow:auto}
  </style>
</head>
<body>
<div class="container">
  <h1>Script Transliterator — Bharat (Enhanced)</h1>
  <p class="text-muted">Transliterate (not translate) between Indian scripts. Features: smart OCR language selection, detection confidence, manual override, phrasebook save.</p>

  <div class="row g-3">
    <div class="col-md-6">
      <div class="card shadow-sm mb-3">
        <div class="card-body">
          <h5>Input</h5>
          <div class="mb-2">
            <label class="form-label">Type or paste text</label>
            <textarea id="textInput" class="form-control" rows="6" placeholder="Paste text or use image upload"></textarea>
          </div>

          <div class="mb-2">
            <label class="form-label">Upload image (OCR)</label>
            <input id="imageInput" type="file" accept="image/*" class="form-control">
            <div class="form-text">Tesseract is used on the server. Server will try suggested language after initial extraction.</div>
            <div class="mt-2">
              <button id="doOCRBtn" class="btn btn-outline-primary btn-sm">Extract text from image</button>
              <span id="ocrInfo" class="ms-2 text-muted"></span>
            </div>
          </div>

          <hr>

          <div class="mb-2">
            <label class="form-label">Detected source script (auto)</label>
            <input id="detectedScript" class="form-control" readonly>
            <div class="form-text">Confidence: <span id="detConfidence">-</span> (higher is better). You may manually override below.</div>
          </div>

          <div class="mb-2">
            <label class="form-label">Override source script (optional)</label>
            <select id="overrideSrc" class="form-select">
              <option value="">-- Use detected --</option>
              {% for display, code in ui_scripts.items() %}
              <option value="{{code}}">{{display}}</option>
              {% endfor %}
            </select>
            <div class="form-text">If detection is wrong, pick the actual source script here.</div>
          </div>

          <div class="mb-2">
            <label class="form-label">Target script</label>
            <select id="targetScript" class="form-select">
            {% for display, code in ui_scripts.items() %}
              <option value="{{code}}">{{display}}</option>
            {% endfor %}
            </select>
          </div>

          <div class="mb-2">
            <button id="transliterateBtn" class="btn btn-primary">Transliterate</button>
            <button id="clearBtn" class="btn btn-secondary">Clear</button>
            <button id="savePhraseBtn" class="btn btn-success">Save to Phrasebook</button>
          </div>

          <div class="mb-2">
            <label class="form-label">Phrase title (for phrasebook)</label>
            <input id="phraseTitle" class="form-control" placeholder="Ex: Road sign - near station">
          </div>

        </div>
      </div>

      <div class="card shadow-sm">
        <div class="card-body">
          <h5>Phrasebook</h5>
          <div class="mb-2">
            <button id="refreshPB" class="btn btn-outline-secondary btn-sm">Refresh list</button>
            <button id="downloadAll" class="btn btn-outline-info btn-sm">Download All (JSON)</button>
          </div>
          <div id="phraseList" style="margin-top:.5rem"></div>
        </div>
      </div>

    </div>

    <div class="col-md-6">
      <div class="card shadow-sm mb-3">
        <div class="card-body">
          <h5>Output</h5>
          <div class="mb-2">
            <label class="form-label">Transliterated text</label>
            <textarea id="outputText" class="form-control monospace" rows="12" readonly></textarea>
          </div>
          <div class="mb-2">
            <label class="form-label">Logs</label>
            <div id="logArea"></div>
          </div>
        </div>
      </div>

      <div class="card shadow-sm">
        <div class="card-body">
          <h6>Samples</h6>
          <div class="d-flex gap-2 flex-wrap">
            <button class="btn btn-outline-secondary sample">హలో (Telugu)</button>
            <button class="btn btn-outline-secondary sample">வணக்கம் (Tamil)</button>
            <button class="btn btn-outline-secondary sample">ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ (Punjabi)</button>
            <button class="btn btn-outline-secondary sample">नमस्ते (Hindi)</button>
            <button class="btn btn-outline-secondary sample">മലയാളം (Malayalam)</button>
          </div>
        </div>
      </div>

    </div>
  </div>

  <footer class="mt-4 text-muted"><small>Demo: uses Aksharamukha (server) and Tesseract. For production add security, rate limits and size checks.</small></footer>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<script>
const textInput = document.getElementById('textInput');
const outputText = document.getElementById('outputText');
const detectedScript = document.getElementById('detectedScript');
const detConfidence = document.getElementById('detConfidence');
const overrideSrc = document.getElementById('overrideSrc');
const targetScript = document.getElementById('targetScript');
const transliterateBtn = document.getElementById('transliterateBtn');
const clearBtn = document.getElementById('clearBtn');
const imageInput = document.getElementById('imageInput');
const doOCRBtn = document.getElementById('doOCRBtn');
const ocrInfo = document.getElementById('ocrInfo');
const logArea = document.getElementById('logArea');
const savePhraseBtn = document.getElementById('savePhraseBtn');
const phraseTitle = document.getElementById('phraseTitle');
const phraseList = document.getElementById('phraseList');
const refreshPB = document.getElementById('refreshPB');
const downloadAll = document.getElementById('downloadAll');

function log(msg){
  const t = new Date().toLocaleTimeString();
  logArea.innerHTML += `<div>[${t}] ${msg}</div>`;
  logArea.scrollTop = logArea.scrollHeight;
}

async function detectServer(text){
  const resp = await fetch('/api/detect',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text})});
  return resp.json();
}

transliterateBtn.addEventListener('click', async ()=>{
  const txt = textInput.value.trim();
  if(!txt){ alert('Enter or extract some text first.'); return; }
  log('Detecting source script...');
  const det = await detectServer(txt);
  if(!det.ok){ alert('Detection failed'); return; }
  const detected = det.script;
  detectedScript.value = detected;
  detConfidence.textContent = (det.confidence*100).toFixed(1) + '%';
  const src_override = overrideSrc.value || detected;
  const tgt = targetScript.value;
  log(`Transliterating: ${src_override} -> ${tgt}`);
  const resp = await fetch('/api/transliterate', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({text: txt, src: src_override, tgt: tgt})
  });
  const data = await resp.json();
  if(data.ok){
    outputText.value = data.result;
    log('Transliteration complete.');
  } else {
    log('Error: ' + data.error);
    alert('Transliteration error: ' + data.error);
  }
});

clearBtn.addEventListener('click', ()=>{
  textInput.value=''; outputText.value=''; detectedScript.value=''; detConfidence.textContent='-'; overrideSrc.value=''; phraseTitle.value='';
  logArea.innerHTML=''; log('Cleared');
});

doOCRBtn.addEventListener('click', async ()=>{
  const file = imageInput.files[0];
  if(!file){ alert('Choose an image first'); return; }
  ocrInfo.textContent = 'Uploading...';
  const fd = new FormData();
  fd.append('image', file);
  try{
    const r = await fetch('/api/ocr', {method:'POST', body: fd});
    const data = await r.json();
    if(!data.ok){ alert('OCR failed: ' + (data.error||'')); ocrInfo.textContent='Failed'; log('OCR failed: '+(data.error||'')); return; }
    textInput.value = data.text;
    detectedScript.value = data.detected_script || '';
    detConfidence.textContent = (data.confidence? (data.confidence*100).toFixed(1)+'%':'-');
    ocrInfo.textContent = `OCR done (lang used: ${data.used_lang || 'unknown'})`;
    log('OCR: extracted text. Detected: ' + (data.detected_script || 'ISO'));
  } catch(e){
    ocrInfo.textContent = 'Error';
    log('OCR request error: ' + e);
  }
});

// Phrasebook functions
async function refreshPhrasebook(){
  const r = await fetch('/api/phrasebook/list');
  const data = await r.json();
  if(!data.ok){ phraseList.innerHTML = '<div class="text-danger">Failed to load phrasebook</div>'; return; }
  const items = data.items;
  if(items.length === 0){ phraseList.innerHTML = '<div class="text-muted">No phrases saved yet.</div>'; return; }
  phraseList.innerHTML = '';
  items.forEach(it=>{
    const div = document.createElement('div');
    div.className = 'border rounded p-2 mb-2';
    div.innerHTML = `<strong>${it.title||'(no title)'}</strong> <small class="text-muted">[${it.src} → ${it.tgt}]</small>
      <div style="white-space:pre-wrap;margin-top:.5rem">${it.text}</div>
      <div class="mt-2">
        <button class="btn btn-sm btn-outline-primary useBtn" data-id="${it.id}">Use</button>
        <a class="btn btn-sm btn-outline-secondary" href="/api/phrasebook/download/${it.id}">Download</a>
        <button class="btn btn-sm btn-outline-danger delBtn" data-id="${it.id}">Delete</button>
      </div>`;
    phraseList.appendChild(div);
  });
  // attach events
  document.querySelectorAll('.useBtn').forEach(b=>{
    b.onclick = async ()=> {
      const id = b.getAttribute('data-id');
      const r = await fetch('/api/phrasebook/get/'+id);
      const d = await r.json();
      if(d.ok){
        textInput.value = d.item.text;
        detectedScript.value = d.item.src;
        overrideSrc.value = '';
        targetScript.value = d.item.tgt;
        log('Loaded phrase into input: ' + (d.item.title || 'untitled'));
      } else alert('Failed to load phrase');
    };
  });
  document.querySelectorAll('.delBtn').forEach(b=>{
    b.onclick = async ()=> {
      if(!confirm('Delete this phrase?')) return;
      const id = b.getAttribute('data-id');
      const r = await fetch('/api/phrasebook/delete/'+id, {method:'DELETE'});
      const d = await r.json();
      if(d.ok){ refreshPhrasebook(); log('Phrase deleted'); } else alert('Delete failed');
    };
  });
}

savePhraseBtn.addEventListener('click', async ()=>{
  const txt = textInput.value.trim();
  if(!txt){ alert('No text to save'); return; }
  const title = phraseTitle.value.trim() || 'Untitled';
  // pick src: override or detected
  const src = overrideSrc.value || detectedScript.value || 'ISO';
  const tgt = targetScript.value;
  const r = await fetch('/api/phrasebook/save', {
    method:'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({title, text: txt, src, tgt})
  });
  const d = await r.json();
  if(d.ok){ log('Saved phrase: ' + title); refreshPhrasebook(); } else alert('Save failed: ' + (d.error||''));
});

refreshPB.addEventListener('click', refreshPhrasebook);
downloadAll.addEventListener('click', ()=> window.location = '/api/phrasebook/download_all');

document.querySelectorAll('.sample').forEach(b=>{
  b.onclick = ()=> { textInput.value = b.textContent; log('Sample loaded'); };
});

// initial load
refreshPhrasebook();
log('App ready');
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX_HTML, ui_scripts=UI_SCRIPTS)


@app.route("/api/detect", methods=["POST"])
def api_detect():
    data = request.get_json(force=True)
    text = data.get("text", "") if data else ""
    detected, top_count, total_matched, breakdown, confidence = detect_script_with_confidence(text)
    return jsonify({"ok": True, "script": detected, "top_count": top_count, "total_matched": total_matched, "breakdown": breakdown, "confidence": confidence})


@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    if 'image' not in request.files:
        return jsonify({"ok": False, "error": "No image uploaded"}), 400
    if pytesseract is None:
        return jsonify({"ok": False, "error": "pytesseract not installed on server"}), 500
    file = request.files['image']
    try:
        img = Image.open(file.stream).convert('RGB')
    except Exception as e:
        return jsonify({"ok": False, "error": f"Invalid image: {e}"}), 400

    # smart OCR
    res = ocr_image_with_smart_lang(img)
    if not res.get("ok"):
        return jsonify({"ok": False, "error": res.get("error")}), 500

    text = res.get("text","")
    detected_script = res.get("detected_script","ISO")
    # compute confidence from the final text
    detected, top_count, total_matched, breakdown, confidence = detect_script_with_confidence(text)

    return jsonify({
        "ok": True,
        "text": text,
        "used_lang": res.get("used_lang"),
        "detected_script": detected_script,
        "breakdown": breakdown,
        "confidence": confidence
    })


@app.route("/api/transliterate", methods=["POST"])
def api_transliterate():
    data = request.get_json(force=True)
    text = data.get("text","")
    src = data.get("src","")
    tgt = data.get("tgt","")
    if not text:
        return jsonify({"ok": False, "error": "Empty text"}), 400
    if not aksha_process:
        return jsonify({"ok": False, "error": "Aksharamukha not available on server. Install `aksharamukha`."}), 500
    try:
        result = perform_transliteration(src, tgt, text)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---- Phrasebook endpoints ----
@app.route("/api/phrasebook/save", methods=["POST"])
def api_phrasebook_save():
    data = request.get_json(force=True)
    title = data.get("title","Untitled")
    text = data.get("text","")
    src = data.get("src","ISO")
    tgt = data.get("tgt","ISO")
    if not text:
        return jsonify({"ok": False, "error": "Empty text"}), 400
    item = {
        "id": str(uuid.uuid4()),
        "title": title,
        "text": text,
        "src": src,
        "tgt": tgt,
        "created_at": datetime.utcnow().isoformat() + "Z"
    }
    pb = load_phrasebooks()
    pb.insert(0, item)
    save_phrasebooks(pb)
    return jsonify({"ok": True, "item": item})


@app.route("/api/phrasebook/list", methods=["GET"])
def api_phrasebook_list():
    pb = load_phrasebooks()
    # return limited metadata
    items = [{"id":it["id"], "title":it.get("title"), "src":it.get("src"), "tgt":it.get("tgt"), "text": it.get("text")[:400]} for it in pb]
    return jsonify({"ok": True, "items": items})


@app.route("/api/phrasebook/get/<pid>", methods=["GET"])
def api_phrasebook_get(pid):
    pb = load_phrasebooks()
    for it in pb:
        if it["id"] == pid:
            return jsonify({"ok": True, "item": it})
    return jsonify({"ok": False, "error": "Not found"}), 404


@app.route("/api/phrasebook/delete/<pid>", methods=["DELETE"])
def api_phrasebook_delete(pid):
    pb = load_phrasebooks()
    new = [it for it in pb if it["id"] != pid]
    if len(new) == len(pb):
        return jsonify({"ok": False, "error": "Not found"}), 404
    save_phrasebooks(new)
    return jsonify({"ok": True})


@app.route("/api/phrasebook/download/<pid>", methods=["GET"])
def api_phrasebook_download(pid):
    pb = load_phrasebooks()
    for it in pb:
        if it["id"] == pid:
            # stream as JSON file
            fname = f"phrase_{pid}.json"
            tmp_path = os.path.join("/tmp", fname) if os.path.isdir("/tmp") else fname
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(it, f, ensure_ascii=False, indent=2)
            return send_file(tmp_path, as_attachment=True, download_name=fname)
    return abort(404)

@app.route("/api/phrasebook/download_all", methods=["GET"])
def api_phrasebook_download_all():
    pb = load_phrasebooks()
    fname = "phrasebooks_all.json"
    tmp_path = os.path.join("/tmp", fname) if os.path.isdir("/tmp") else fname
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(pb, f, ensure_ascii=False, indent=2)
    return send_file(tmp_path, as_attachment=True, download_name=fname)


if __name__ == "__main__":
    print("Starting Transliterator app...")
    print("Requirements: system Tesseract + tesseract-language-packs for non-Latin OCR (optional), python packages: flask pillow pytesseract aksharamukha")
    app.run(debug=True, host="0.0.0.0", port=5000)

COMFY ENGINE - API WORKFLOWS (DEV08)

1. Export a workflow in ComfyUI using "Save (API Format)" / "Export API".
2. Put its .json file into THIS folder (no subfolders in DEV08).
3. Restart Forge Neo or click "Refresh workflows and models" in the Comfy Engine tab.
4. Select "API - <file>" and click Generate.

ComfyUI must know your model paths; Forge default model dirs and
comfy_bridge.json -> extra_model_dirs are available to the private worker.

Editable parameters are opt-in tokens *inside the API JSON's input values*:
  {{prompt}}          prompt string
  {{negative_prompt}} negative prompt string
  {{width}}           integer
  {{height}}          integer
  {{frames}}          integer
  {{steps}}           integer
  {{seed}}            integer (-1 in tab generates a random seed)
  {{cfg}}             floating-point number
  {{image}}           selected first image uploaded into private Comfy input folder
  {{last_image}}      selected last image uploaded likewise

For a numeric input, replace its entire value by the string "{{steps}}" etc.
Only explicitly marked fields are modified. Other values stay unchanged.

EXAMPLE (partial API JSON):
  "3": {"class_type": "KSampler", "inputs": {
     "seed": "{{seed}}", "steps": "{{steps}}", "cfg": "{{cfg}}", ...}}

Important:
- An editor-format JSON with top-level "nodes": [...] is not an API JSON.
- Only installed core/Comfy-extra nodes will work. No third-party nodes
  are installed or loaded by this embedded worker yet.
- A save/output node is required for results to appear in the gallery.
- Don't load workflows from untrusted sources without reviewing their nodes.
- MiniMax H3 presets run through the existing, unchanged H3 backend.

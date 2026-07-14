import json, requests, time
from bench3 import MODELS, load_tmpl, build_prompt
from router_set import catalog_for
cat = catalog_for("low")
names=[t["function"]["name"] for t in cat]
schema={"type":"object","properties":{"tool":{"anyOf":[{"type":"string","enum":names},{"type":"null"}]},"args":{"type":"object"}},"required":["tool","args"]}
for mk in ("qwen35-4b-q8","qwen35-4b-q4","xlam2-3b-q8"):
    cfg=MODELS[mk]; tmpl=load_tmpl(cfg["tmpl"])
    p=build_prompt(tmpl,cfg["vars"],"turn off the lights in the living room","grammar",cat)
    body={"model":cfg["mid"],"prompt":p,"raw":True,"stream":False,"keep_alive":"5m","format":schema,
          "options":{"temperature":0,"num_predict":384,"stop":cfg["stop"],"num_ctx":8192}}
    for _ in range(2): requests.post("http://localhost:11434/api/generate",json=body,timeout=300)
    r=requests.post("http://localhost:11434/api/generate",json=body,timeout=300).json()
    ms=lambda k: r.get(k,0)/1e6
    print("%-14s prompt_tok=%4d  prefill=%6.0fms | gen_tok=%3d decode=%6.0fms | load=%5.0fms | total=%6.0fms" % (
        mk, r.get("prompt_eval_count",0), ms("prompt_eval_duration"),
        r.get("eval_count",0), ms("eval_duration"), ms("load_duration"), ms("total_duration")))
    requests.post("http://localhost:11434/api/generate",json={"model":cfg["mid"],"prompt":"","raw":True,"keep_alive":0,"stream":False},timeout=60)

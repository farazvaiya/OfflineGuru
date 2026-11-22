#!/usr/bin/env python3
"""Quick, reproducible offline directional MT/TTS benchmark.

Input references are the bundled author-written parallel set, not a human-rated
or independently verified test set. Results are regression/profiling evidence,
not acceptance evidence for translation quality.
"""
from __future__ import annotations
import argparse, io, json, statistics, sys, time, wave
from pathlib import Path
import psutil
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from guru.bilingual import BidirectionalPiperTts, BidirectionalTranslator
from guru.config import load_settings

def pct(values,p):
    a=sorted(values)
    return round(a[min(len(a)-1,round((len(a)-1)*p))],2) if a else None

def chrf2(hyp,ref):
    hyp=' '.join(hyp.split()); ref=' '.join(ref.split())
    if not hyp or not ref:return 0.0
    ps=[];rs=[]
    for n in range(1,7):
        h=[hyp[i:i+n] for i in range(max(0,len(hyp)-n+1))]
        r=[ref[i:i+n] for i in range(max(0,len(ref)-n+1))]
        if not h or not r: continue
        c={}
        for g in r:c[g]=c.get(g,0)+1
        hit=0
        for g in h:
            if c.get(g,0):hit+=1;c[g]-=1
        ps.append(hit/len(h));rs.append(hit/len(r))
    if not ps:return 0.0
    p,r=statistics.fmean(ps),statistics.fmean(rs)
    return 5*p*r/(4*p+r) if p+r else 0.0

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repeats',type=int,default=2);ap.add_argument('--out',default='reports/results/bidirectional_benchmark.json');a=ap.parse_args()
    data=json.loads((ROOT/'data/eval/en_bn_quality.json').read_text()); entries=data['entries']; settings=load_settings(); proc=psutil.Process()
    resource=lambda:{'rss_mb':round(proc.memory_info().rss/1e6,1),'swap_used_mb':round(psutil.swap_memory().used/1e6,1),'cpu_pct':round(psutil.cpu_percent(interval=.02),1)}
    models=BidirectionalTranslator(settings); trans={}
    for src,tgt in [('en','bn'),('bn','en')]:
        model=models._build(src,tgt); samples=[]; lat=[]; rows=[]
        t=time.perf_counter();model.load();load_ms=(time.perf_counter()-t)*1000
        for e in entries:
            text=e['en'] if src=='en' else e['reference_bn'];ref=e['reference_bn'] if tgt=='bn' else e['en']
            answers=[]
            for run in range(a.repeats+1):
                samples.append(resource());t=time.perf_counter();result=model.translate(text);elapsed=(time.perf_counter()-t)*1000
                if run:lat.append(elapsed)
                answers.append(result.text)
            out=answers[-1];rows.append({'id':e['id'],'source':text,'reference':ref,'hypothesis':out,'chrF2':round(chrf2(out,ref),4)})
        trans[f'{src}_{tgt}']={'source_language':src,'target_language':tgt,'model':str(model.model_dir),'model_license':'Apache-2.0','dataset':'data/eval/en_bn_quality.json','dataset_entries':len(entries),'dataset_reference':'author-written; no independent native-speaker verification; not a held-out test set','warmup_runs_per_entry':1,'repeat_runs_per_entry':a.repeats,'load_ms':round(load_ms,1),'latency_ms':{'p50':pct(lat,.5),'p95':pct(lat,.95),'n':len(lat)},'chrF2_mean':round(statistics.fmean(r['chrF2'] for r in rows),4),'human_rating':None,'human_rating_status':'not run','resources':samples,'rows':rows}
    voices=BidirectionalPiperTts(settings); tts={}
    for lang in ('en','bn'):
        voice=voices._voice(lang);t=time.perf_counter();voice.load();load_ms=(time.perf_counter()-t)*1000;lat=[];rtf=[];samples=[]
        texts=[e['en'] if lang=='en' else e['reference_bn'] for e in entries]
        for text in texts:
            for run in range(a.repeats+1):
                samples.append(resource());t=time.perf_counter();audio,_=voice.synthesize(text,use_cache=False);elapsed=(time.perf_counter()-t)*1000
                with wave.open(io.BytesIO(audio),'rb') as wf:duration=wf.getnframes()/wf.getframerate()
                if run:lat.append(elapsed);rtf.append(elapsed/(duration*1000) if duration else 0)
        tts[lang]={'voice':str(voice.voice_path),'available':voice.available,'load_ms':round(load_ms,1),'warmup_runs_per_entry':1,'repeat_runs_per_entry':a.repeats,'latency_ms':{'p50':pct(lat,.5),'p95':pct(lat,.95),'n':len(lat)},'rtf':{'mean':round(statistics.fmean(rtf),3),'p50':pct(rtf,.5),'p95':pct(rtf,.95)},'resources':samples}
    result={'created_at':time.strftime('%Y-%m-%dT%H:%M:%S'),'benchmark_profile':{'device':'current host; not confirmed to be target i5-7300U','inference':'CPU int8','repeats':a.repeats,'warmup':'one per phrase','models':'one backend/model loaded at a time; direction instances reused within pass'},'translation':trans,'tts':tts,'asr':'See separate report. A Bengali-human ASR reference dataset is unavailable; synthetic Bengali Piper ASR must be identified as a proxy.','acceptance':{'WER_max':.20,'translation_human_rating_min':4,'first_audio_p50_ms':2000,'first_audio_p95_ms':4000,'all_goals':'NOT ASSESSED from MT/TTS microbench; end-to-end streaming evaluation on target hardware and human speech unavailable.'}}
    out=ROOT/a.out;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'created_at':result['created_at'],'translation':{k:{'latency_ms':v['latency_ms'],'chrF2_mean':v['chrF2_mean']} for k,v in trans.items()},'tts':tts,'out':str(out)},ensure_ascii=False,indent=2))
if __name__=='__main__':main()

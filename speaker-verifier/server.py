from __future__ import annotations

import json, os, re, sqlite3, threading, time, uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F
import torchaudio
from speechbrain.inference.speaker import EncoderClassifier

VERSION="2.0.0"
HOST="0.0.0.0"
PORT=int(os.getenv("JARVIS_SPEAKER_PORT","8091"))
DATA=Path(os.getenv("JARVIS_SPEAKER_DATA_DIR","/data"))
DB=Path(os.getenv("JARVIS_SPEAKER_DB",str(DATA/"jarvis_speakers.db")))
MODEL_DIR=Path(os.getenv("JARVIS_SPEAKER_MODEL_DIR",str(DATA/"models/spkrec-ecapa-voxceleb")))
MODEL_SOURCE=os.getenv("JARVIS_SPEAKER_MODEL_SOURCE","speechbrain/spkrec-ecapa-voxceleb")
INPUT_RATE=24000; MODEL_RATE=16000; SAMPLE_WIDTH=2; MAX_PCM=4*1024*1024
ID_MIN=float(os.getenv("JARVIS_SPEAKER_IDENTIFY_MIN_SECONDS","0.80"))
ID_MAX=float(os.getenv("JARVIS_SPEAKER_IDENTIFY_MAX_SECONDS","15.0"))
ID_THRESHOLD=float(os.getenv("JARVIS_SPEAKER_IDENTIFY_THRESHOLD","0.340"))
MARGIN=float(os.getenv("JARVIS_SPEAKER_AMBIGUITY_MARGIN","0.035"))
BG_THRESHOLD=float(os.getenv("JARVIS_SPEAKER_BACKGROUND_THRESHOLD","0.270"))
ENROLL_N=int(os.getenv("JARVIS_SPEAKER_ENROLL_SAMPLES","5"))
ENROLL_MIN=float(os.getenv("JARVIS_SPEAKER_ENROLL_MIN_SAMPLE_SECONDS","1.50"))
ENROLL_MAX=float(os.getenv("JARVIS_SPEAKER_ENROLL_MAX_SAMPLE_SECONDS","9.0"))
ENROLL_TOTAL=float(os.getenv("JARVIS_SPEAKER_ENROLL_MIN_TOTAL_SECONDS","10.0"))
MIN_RMS=float(os.getenv("JARVIS_SPEAKER_ENROLL_MIN_RMS","0.006"))
MAX_CLIP=float(os.getenv("JARVIS_SPEAKER_ENROLL_MAX_CLIP_RATIO","0.04"))
CONS_MEAN=float(os.getenv("JARVIS_SPEAKER_ENROLL_MIN_MEAN_CONSISTENCY","0.280"))
CONS_WORST=float(os.getenv("JARVIS_SPEAKER_ENROLL_MIN_WORST_CONSISTENCY","0.160"))
SESSION_TTL=int(os.getenv("JARVIS_SPEAKER_ENROLL_TTL_SECONDS","600"))
AUTO_ADAPT=False  # Deliberately fixed off in production v1 to prevent profile poisoning.

PHRASES=(
 "The quick brown fox jumps over the lazy dog.",
 "Jarvis, turn on the living room lights and tell me the time.",
 "Tomorrow morning remind me to check the weather before I leave.",
 "Seven blue cars waited quietly beside the old station.",
 "I usually speak naturally, even when the room is a little noisy.",
)
MODEL_LOCK=threading.Lock(); STORE_LOCK=threading.RLock()


def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def sid(v): return re.sub(r"[^a-z0-9]+","_",str(v or "").casefold()).strip("_")[:64]
def dname(v): return re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ' -]+",""," ".join(str(v or "").split())).strip()[:80]
def norm(e): return F.normalize(e.squeeze().detach().cpu().float(),dim=0)
def blob(e): return e.detach().cpu().numpy().astype("<f4",copy=False).tobytes()
def unblob(b): return F.normalize(torch.from_numpy(np.frombuffer(b,dtype="<f4").copy()).float(),dim=0)
def cos(a,b): return float(torch.dot(F.normalize(a,dim=0),F.normalize(b,dim=0)).item())


def pcm_quality(pcm,min_s,max_s,levels=False):
    if not pcm: return {"ok":False,"reason":"empty_audio"}
    if len(pcm)>MAX_PCM: return {"ok":False,"reason":"audio_too_large"}
    if len(pcm)%2: return {"ok":False,"reason":"unaligned_audio"}
    x=np.frombuffer(pcm,dtype="<i2"); dur=x.size/INPUT_RATE
    if dur<min_s: return {"ok":False,"reason":"too_short","duration_seconds":round(dur,3)}
    if dur>max_s: return {"ok":False,"reason":"too_long","duration_seconds":round(dur,3)}
    y=x.astype(np.float32)/32768.0
    rms=float(np.sqrt(np.mean(np.square(y),dtype=np.float64))); clip=float(np.mean(np.abs(y)>=.985))
    q={"ok":True,"duration_seconds":round(dur,3),"rms":round(rms,6),"clip_ratio":round(clip,6)}
    if levels and rms<MIN_RMS: q.update(ok=False,reason="too_quiet")
    elif levels and clip>MAX_CLIP: q.update(ok=False,reason="clipping")
    return q


def pcm_wave(pcm):
    y=np.frombuffer(pcm,dtype="<i2").astype(np.float32)/32768.0
    w=torch.from_numpy(y).float().unsqueeze(0)
    return torchaudio.functional.resample(w,INPUT_RATE,MODEL_RATE)

def wav_wave(path):
    y,rate=sf.read(path,dtype="float32",always_2d=False); y=np.asarray(y)
    if y.ndim==2: y=y.mean(axis=1)
    if y.ndim!=1 or not y.size: raise ValueError("invalid_audio")
    w=torch.from_numpy(y).float().unsqueeze(0)
    return w if rate==MODEL_RATE else torchaudio.functional.resample(w,int(rate),MODEL_RATE)

DATA.mkdir(parents=True,exist_ok=True); DB.parent.mkdir(parents=True,exist_ok=True); MODEL_DIR.parent.mkdir(parents=True,exist_ok=True)
def con():
    c=sqlite3.connect(DB,timeout=10); c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA busy_timeout=5000")
    return c

def init_db():
    with STORE_LOCK,con() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS speakers(
          speaker_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
          enabled INTEGER NOT NULL DEFAULT 1, model_source TEXT NOT NULL, embedding BLOB NOT NULL,
          sample_count INTEGER NOT NULL, enrollment_seconds REAL NOT NULL,
          consistency_mean REAL, consistency_worst REAL, source TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS speaker_embeddings(
          id INTEGER PRIMARY KEY AUTOINCREMENT, speaker_id TEXT NOT NULL, embedding BLOB NOT NULL,
          source TEXT NOT NULL, duration_seconds REAL NOT NULL DEFAULT 0, quality_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL, FOREIGN KEY(speaker_id) REFERENCES speakers(speaker_id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_speaker_embeddings_speaker ON speaker_embeddings(speaker_id,id);
        '''); c.commit()

@dataclass
class Session:
    session_id:str; speaker_id:str; display_name:str; is_admin:bool; replace:bool; created:float
    embeddings:list[torch.Tensor]=field(default_factory=list); durations:list[float]=field(default_factory=list); quality:list[dict]=field(default_factory=list)
SESSIONS={}; PROFILES={}

def public(p):
    return {k:p.get(k) for k in ("speaker_id","display_name","is_admin","sample_count","enrollment_seconds","consistency_mean","consistency_worst","source","created_at","updated_at")}
def refresh():
    global PROFILES
    out={}
    with STORE_LOCK,con() as c:
        for r in c.execute("SELECT * FROM speakers WHERE enabled=1 ORDER BY speaker_id"):
            out[r["speaker_id"]]={**dict(r),"is_admin":bool(r["is_admin"]),"embedding":unblob(r["embedding"])}
    PROFILES=out

def clean_sessions():
    t=time.monotonic()
    for k in [k for k,v in SESSIONS.items() if t-v.created>SESSION_TTL]: SESSIONS.pop(k,None)

print("========== JARVIS VOICE ID ==========",flush=True)
torch.set_num_threads(max(1,min(4,os.cpu_count() or 1)))
t0=time.perf_counter()
classifier=EncoderClassifier.from_hparams(source=MODEL_SOURCE,savedir=str(MODEL_DIR),run_opts={"device":"cpu"})
print("PASS: ECAPA loaded",f"ms={round((time.perf_counter()-t0)*1000,1)}",flush=True)
def encode(w):
    with MODEL_LOCK:
        with torch.inference_mode(): return norm(classifier.encode_batch(w))

def save_profile(speaker_id,display_name,is_admin,embs,durs,qualities,source,mean,worst,replace):
    stack=torch.stack([F.normalize(e,dim=0) for e in embs]); centroid=F.normalize(stack.mean(dim=0),dim=0); stamp=now()
    with STORE_LOCK,con() as c:
        old=c.execute("SELECT created_at FROM speakers WHERE speaker_id=?",(speaker_id,)).fetchone()
        if old and not replace: raise FileExistsError(speaker_id)
        created=old["created_at"] if old else stamp
        if old: c.execute("DELETE FROM speaker_embeddings WHERE speaker_id=?",(speaker_id,))
        c.execute('''INSERT INTO speakers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(speaker_id) DO UPDATE SET display_name=excluded.display_name,is_admin=excluded.is_admin,enabled=1,
          model_source=excluded.model_source,embedding=excluded.embedding,sample_count=excluded.sample_count,
          enrollment_seconds=excluded.enrollment_seconds,consistency_mean=excluded.consistency_mean,
          consistency_worst=excluded.consistency_worst,source=excluded.source,updated_at=excluded.updated_at''',
          (speaker_id,display_name,int(is_admin),1,MODEL_SOURCE,blob(centroid),len(embs),sum(durs),mean,worst,source,created,stamp))
        for i,e in enumerate(embs):
            c.execute("INSERT INTO speaker_embeddings(speaker_id,embedding,source,duration_seconds,quality_json,created_at) VALUES(?,?,?,?,?,?)",
              (speaker_id,blob(e),source,durs[i] if i<len(durs) else 0,json.dumps(qualities[i] if i<len(qualities) else {},separators=(",",":")),stamp))
        c.commit()
    refresh(); return public(PROFILES[speaker_id])

def bootstrap_aaron():
    if PROFILES: return
    dirs=[DATA/"enroll",DATA/"short-enroll",DATA/"aaron"/"enroll",DATA/"aaron"/"short-enroll",DATA/"aaron-long",DATA/"aaron-short"]
    paths=[]; seen=set()
    for d in dirs:
        if d.is_dir():
            for p in sorted(d.glob("*.wav")):
                key=str(p.resolve())
                if key not in seen: seen.add(key); paths.append(p)
    if len(paths)<3:
        print("INFO: no legacy Aaron profile import available",flush=True); return
    embs=[]; durs=[]; qs=[]
    for p in paths:
        try:
            info=sf.info(p); embs.append(encode(wav_wave(p))); durs.append(float(info.duration)); qs.append({"legacy_file":p.name})
        except Exception as exc: print("WARN: legacy skip",p.name,str(exc),flush=True)
    if len(embs)<3: return
    centroid=F.normalize(torch.stack(embs).mean(dim=0),dim=0); sims=[cos(e,centroid) for e in embs]
    profile=save_profile("aaron","Aaron",True,embs,durs,qs,"legacy_reference_import",sum(sims)/len(sims),min(sims),False)
    print("PASS: imported legacy Aaron profile",f"samples={profile['sample_count']}",flush=True)


def identify(pcm):
    t0=time.perf_counter(); q=pcm_quality(pcm,ID_MIN,ID_MAX,False)
    if not q["ok"]: return {"ok":True,"recognized":False,"reason":q["reason"],"quality":q,"speaker_count":len(PROFILES),"inference_ms":0.0}
    if not PROFILES: return {"ok":True,"recognized":False,"reason":"no_profiles","quality":q,"speaker_count":0,"inference_ms":0.0}
    cand=encode(pcm_wave(pcm)); scored=sorted([(cos(cand,p["embedding"]),p) for p in PROFILES.values()],key=lambda x:x[0],reverse=True)
    top,p=scored[0]; second=scored[1][0] if len(scored)>1 else None; margin=top-second if second is not None else 1.0
    recognized=top>=ID_THRESHOLD and (second is None or margin>=MARGIN)
    reason="recognized" if recognized else ("ambiguous" if top>=ID_THRESHOLD and second is not None else "below_threshold")
    out={"ok":True,"recognized":recognized,"reason":reason,"score":round(top,6),"second_score":round(second,6) if second is not None else None,
         "margin":round(margin,6),"threshold":ID_THRESHOLD,"ambiguity_margin":MARGIN,"quality":q,"speaker_count":len(scored),
         "inference_ms":round((time.perf_counter()-t0)*1000,1),
         "top_candidates":[{"speaker_id":x[1]["speaker_id"],"display_name":x[1]["display_name"],"score":round(x[0],6)} for x in scored[:3]]}
    if recognized: out["speaker"]=public(p)
    return out


def start_enroll(payload):
    clean_sessions(); name=dname(payload.get("display_name")); speaker_id=sid(payload.get("speaker_id") or name); replace=bool(payload.get("replace")); admin=bool(payload.get("is_admin"))
    if len(name)<2 or not speaker_id: raise ValueError("invalid_name")
    if speaker_id in PROFILES and not replace: raise FileExistsError(speaker_id)
    session_id=uuid.uuid4().hex; SESSIONS[session_id]=Session(session_id,speaker_id,name,admin,replace,time.monotonic())
    return {"ok":True,"session_id":session_id,"speaker_id":speaker_id,"display_name":name,"target_samples":ENROLL_N,"phrases":list(PHRASES[:ENROLL_N]),"expires_seconds":SESSION_TTL,"replace":replace}


def add_sample(session_id,phrase_index,pcm):
    clean_sessions(); s=SESSIONS.get(session_id)
    if not s: raise KeyError("enrollment_session_not_found")
    if phrase_index!=len(s.embeddings): raise ValueError("unexpected_phrase_index")
    q=pcm_quality(pcm,ENROLL_MIN,ENROLL_MAX,True)
    if not q["ok"]: return {"ok":False,"accepted":False,"reason":q["reason"],"quality":q,"accepted_samples":len(s.embeddings),"target_samples":ENROLL_N}
    e=encode(pcm_wave(pcm))
    if s.embeddings:
        centroid=F.normalize(torch.stack(s.embeddings).mean(dim=0),dim=0); consistency=cos(e,centroid)
        if consistency<0.080:
            return {"ok":False,"accepted":False,"reason":"voice_inconsistent","consistency":round(consistency,6),"quality":q,"accepted_samples":len(s.embeddings),"target_samples":ENROLL_N}
    s.embeddings.append(e); s.durations.append(float(q["duration_seconds"])); s.quality.append(q)
    return {"ok":True,"accepted":True,"accepted_samples":len(s.embeddings),"target_samples":ENROLL_N,"total_seconds":round(sum(s.durations),3),"quality":q,"complete":len(s.embeddings)>=ENROLL_N}


def finish_enroll(payload):
    clean_sessions(); session_id=str(payload.get("session_id") or ""); s=SESSIONS.get(session_id)
    if not s: raise KeyError("enrollment_session_not_found")
    if len(s.embeddings)<ENROLL_N: raise ValueError("not_enough_samples")
    if sum(s.durations)<ENROLL_TOTAL: raise ValueError("not_enough_speech")
    centroid=F.normalize(torch.stack(s.embeddings).mean(dim=0),dim=0); sims=[cos(e,centroid) for e in s.embeddings]; mean=sum(sims)/len(sims); worst=min(sims)
    if mean<CONS_MEAN or worst<CONS_WORST:
        SESSIONS.pop(session_id,None); return {"ok":False,"reason":"enrollment_inconsistent","consistency_mean":round(mean,6),"consistency_worst":round(worst,6)}
    profile=save_profile(s.speaker_id,s.display_name,s.is_admin,s.embeddings,s.durations,s.quality,"voice_pe_guided_enrollment",mean,worst,s.replace)
    SESSIONS.pop(session_id,None); return {"ok":True,"enrolled":True,"speaker":profile,"consistency_mean":round(mean,6),"consistency_worst":round(worst,6)}


def delete_profile(payload):
    speaker_id=sid(payload.get("speaker_id"))
    if not speaker_id: raise ValueError("invalid_speaker_id")
    with STORE_LOCK,con() as c:
        row=c.execute("SELECT display_name FROM speakers WHERE speaker_id=?",(speaker_id,)).fetchone()
        if not row: raise KeyError("speaker_not_found")
        name=row["display_name"]; c.execute("DELETE FROM speakers WHERE speaker_id=?",(speaker_id,)); c.commit()
    refresh(); return {"ok":True,"deleted":True,"speaker_id":speaker_id,"display_name":name}


def legacy_score(pcm):
    r=identify(pcm); q=r.get("quality") if isinstance(r.get("quality"),dict) else {}; dur=float(q.get("duration_seconds") or 0); score=float(r.get("score") or 0)
    if r.get("recognized"):
        p=r.get("speaker") if isinstance(r.get("speaker"),dict) else {}; classification="STRONG_AARON" if p.get("speaker_id")=="aaron" else "STRONG_SPEAKER"; decision="TRUSTED_SPEAKER"; suppress=False
    elif PROFILES and dur>=1.0 and score<=BG_THRESHOLD: classification="BACKGROUND"; decision="STRONG_BACKGROUND"; suppress=True
    else: classification="AMBIGUOUS"; decision="UNCERTAIN"; suppress=False
    return {**r,"score":round(score,6),"classification":classification,"policy_version":"V1.3","policy_mode":"MULTIUSER_COMPATIBILITY","policy_decision":decision,
            "policy_suppress_candidate":suppress,"window_short_score":round(score,6),"strong_aaron_threshold":ID_THRESHOLD,"background_threshold":BG_THRESHOLD,"gate_enabled":False,"action":"OBSERVE_ONLY"}

init_db(); refresh()
try: bootstrap_aaron()
except Exception as exc: print("WARN: legacy bootstrap failed",str(exc),flush=True)
READY=time.time(); print("PASS: Jarvis Voice ID ready",f"profiles={len(PROFILES)}",f"db={DB}",flush=True)

class ApiError(Exception):
    def __init__(self,status,code,message=None): self.status=status; self.code=code; self.message=message or code; super().__init__(self.message)

class Handler(BaseHTTPRequestHandler):
    server_version=f"JarvisVoiceID/{VERSION}"
    def log_message(self,fmt,*args): print("HTTP",self.address_string(),fmt%args,flush=True)
    def sendj(self,status,payload):
        body=json.dumps(payload,separators=(",",":"),ensure_ascii=False,default=str).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(body))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(body)
    def body(self,max_bytes=MAX_PCM):
        try: n=int(self.headers.get("Content-Length") or 0)
        except ValueError: raise ApiError(400,"invalid_content_length")
        if n<=0: raise ApiError(400,"empty_body")
        if n>max_bytes: raise ApiError(413,"body_too_large")
        b=self.rfile.read(n)
        if len(b)!=n: raise ApiError(400,"incomplete_body")
        return b
    def jsonbody(self):
        try: x=json.loads(self.body(65536))
        except ApiError: raise
        except Exception: raise ApiError(400,"invalid_json")
        if not isinstance(x,dict): raise ApiError(400,"json_object_required")
        return x
    def parsed(self):
        p=urlparse(self.path); return p.path,parse_qs(p.query)
    def err(self,exc):
        if isinstance(exc,ApiError): self.sendj(exc.status,{"ok":False,"error":exc.code,"message":exc.message})
        elif isinstance(exc,KeyError): self.sendj(404,{"ok":False,"error":str(exc.args[0])})
        elif isinstance(exc,ValueError): self.sendj(400,{"ok":False,"error":str(exc)})
        else: print("ERROR",self.command,self.path,str(exc),flush=True); self.sendj(500,{"ok":False,"error":"internal_error"})
    def do_GET(self):
        try:
            path,_=self.parsed()
            if path=="/health": self.sendj(200,{"ok":True,"service":"jarvis-voice-id","version":VERSION,"model":MODEL_SOURCE,"speaker_count":len(PROFILES),"enrollment_sessions":len(SESSIONS),"identify_threshold":ID_THRESHOLD,"ambiguity_margin":MARGIN,"auto_adapt":AUTO_ADAPT,"uptime_seconds":round(time.time()-READY)}); return
            if path=="/speakers": self.sendj(200,{"ok":True,"count":len(PROFILES),"speakers":[public(p) for p in PROFILES.values()]}); return
            raise ApiError(404,"not_found")
        except Exception as exc: self.err(exc)
    def do_POST(self):
        try:
            path,q=self.parsed()
            if path=="/identify": self.sendj(200,identify(self.body())); return
            if path=="/score": self.sendj(200,legacy_score(self.body())); return
            if path=="/enroll/start":
                try: r=start_enroll(self.jsonbody())
                except FileExistsError as exc: raise ApiError(409,"speaker_exists",f"speaker profile already exists: {exc}")
                self.sendj(201,r); return
            if path=="/enroll/sample":
                session_id=(q.get("session_id") or [""])[0]
                try: idx=int((q.get("phrase_index") or ["-1"])[0])
                except ValueError: raise ApiError(400,"invalid_phrase_index")
                r=add_sample(session_id,idx,self.body()); self.sendj(200 if r.get("ok") else 422,r); return
            if path=="/enroll/finish":
                try: r=finish_enroll(self.jsonbody())
                except ValueError as exc: raise ApiError(422,str(exc))
                self.sendj(200 if r.get("ok") else 422,r); return
            if path=="/enroll/cancel":
                session_id=str(self.jsonbody().get("session_id") or ""); self.sendj(200,{"ok":True,"cancelled":SESSIONS.pop(session_id,None) is not None}); return
            if path=="/speakers/delete": self.sendj(200,delete_profile(self.jsonbody())); return
            raise ApiError(404,"not_found")
        except Exception as exc: self.err(exc)

if __name__=="__main__":
    srv=ThreadingHTTPServer((HOST,PORT),Handler); print(f"Listening on {HOST}:{PORT}",flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally: srv.server_close()

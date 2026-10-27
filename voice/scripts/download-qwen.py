import hashlib
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent.parent / 'models'
root.mkdir(exist_ok=True)
import json
manifest = json.loads((Path(__file__).parent/'qwen-manifest.json').read_text())
files = [("qwen3-asr-gguf/"+f['rfilename'], f"https://huggingface.co/{manifest['repo']}/resolve/{manifest['revision']}/{f['rfilename']}", f.get('lfs',{}).get('sha256'), f['blobId']) for f in manifest['files']]
files.append(('silero_vad.onnx', 'https://raw.githubusercontent.com/sheldonix/silero-vad-rust/fb8719a6bcee4252a69753810a92b3336f7eda6d/src/silero_vad/data/silero_vad.onnx', '1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3', None))
for name, url, expected, blob in files:
 path = root/name
 path.parent.mkdir(parents=True, exist_ok=True)
 if not path.exists():
  part = path.with_suffix(path.suffix+'.part')
  subprocess.run(['curl','-fL','--retry','3','-C','-',url,'-o',str(part)],check=True)
  candidate = part
 else:
  candidate = path
 h = hashlib.sha256() if expected else hashlib.sha1()
 if not expected:h.update(f'blob {candidate.stat().st_size}\0'.encode())
 with candidate.open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
 if h.hexdigest()!=(expected or blob):raise RuntimeError(f'Checksum mismatch: {candidate}')
 if candidate!=path:candidate.replace(path)
 print('Verified:', name, flush=True)

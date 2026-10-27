"""Build pinned llama.cpp. Python 3.12+, CMake, C++ compiler and curl required."""
import argparse
import hashlib
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile

REVISION = '8172e6577ac2b35de1ec1e5d1c0aaad6c4a2129f'
SHA256 = 'c5c6b91e02e6d15af2a5481c3ebadb964f6bcade95b4eec9467a511f6c43b23a'
parser = argparse.ArgumentParser()
parser.add_argument('--backend', choices=['metal', 'cpu', 'cuda', 'vulkan'], default='metal' if platform.system() == 'Darwin' else 'cpu')
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
vendor = root/'vendor'
source = vendor/'llama.cpp'
vendor.mkdir(exist_ok=True)
if not source.exists():
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp)/'llama.tar.gz'
        subprocess.run(['curl', '-fL', '--retry', '3', f'https://github.com/ggml-org/llama.cpp/archive/{REVISION}.tar.gz', '-o', str(archive)], check=True)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('Source archive checksum mismatch')
        with tarfile.open(archive) as tar:
            tar.extractall(vendor, filter='data')
        (vendor/f'llama.cpp-{REVISION}').rename(source)
    (source/'.bot-revision').write_text(REVISION)
if (source/'.bot-revision').read_text().strip() != REVISION:
    raise RuntimeError('Unexpected llama.cpp source revision')
flags = [f'-DGGML_{name.upper()}={"ON" if args.backend == name else "OFF"}' for name in ['metal', 'cuda', 'vulkan']]
subprocess.run(['cmake', '-S', str(source), '-B', str(source/'build'), '-DCMAKE_BUILD_TYPE=Release', '-DLLAMA_BUILD_TESTS=OFF', '-DLLAMA_BUILD_EXAMPLES=OFF', *flags], check=True)
subprocess.run(['cmake', '--build', str(source/'build'), '--config', 'Release', '-j', '4', '--target', 'llama-server'], check=True)

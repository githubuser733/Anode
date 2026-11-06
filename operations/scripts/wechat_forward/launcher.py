"""GUI-subsystem shortcut entry; the controller and all descendants stay hidden."""
import argparse
from pathlib import Path
import subprocess
import uuid

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action',choices=['Start','Stop'])
args = parser.parse_args()
directory = root/'artifacts/wechat-wecom/launcher'
directory.mkdir(parents=True,exist_ok=True)
with (directory/(uuid.uuid4().hex+'.log')).open('xb') as output:
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass',
                    '-File',str(root/'scripts/Control-WeChatWeComForward.ps1'),'-Action',args.action],
                   cwd=root,stdin=subprocess.DEVNULL,stdout=output,stderr=output,
                   creationflags=subprocess.CREATE_NO_WINDOW,check=True)

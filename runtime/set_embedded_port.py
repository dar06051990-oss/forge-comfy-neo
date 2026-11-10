"""Change only the embedded_port setting. Requires Forge to be stopped."""
from pathlib import Path
import json,sys
ext=Path(__file__).resolve().parent.parent
path=ext/'comfy_bridge.json'
try:
 if len(sys.argv)!=2:raise ValueError('Usage: set_embedded_port.py 8191')
 port=int(sys.argv[1])
 if not 1024<=port<=65535:raise ValueError('port must be in 1024..65535')
 obj=json.loads(path.read_text(encoding='utf-8'))
 obj['embedded_port']=port
 path.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
 print(f'[OK] Embedded port set to {port}. Restart Forge to apply.')
except Exception as e:
 print('[ERROR] Bad port or config:',e)
 sys.exit(1)

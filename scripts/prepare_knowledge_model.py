import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.knowledge.models import embed

root=Path(sys.argv[1]) if len(sys.argv)>1 else Path('.local/knowledge-runtime')
root.mkdir(parents=True,exist_ok=True)
vectors,key=embed(root,['古籍中的赋税与财政制度','The history of taxation'])
print({'model_key':key,'dimensions':len(vectors[0]),'samples':len(vectors)})

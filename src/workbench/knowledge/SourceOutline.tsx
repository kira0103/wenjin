type Block={id:string;section_path:string;page_index:number;asset_id:string};
type Node={name:string;children:Map<string,Node>;blocks:Block[]};

export default function SourceOutline({blocks,onSelect}:{blocks:Block[];onSelect(block:Block):void}) {
  const roots=new Map<string,Node>();
  for(const block of blocks){
    if(!block.section_path.trim())continue;
    let level=roots,node:Node|undefined;
    for(const name of block.section_path.split('/').map(s=>s.trim()).filter(Boolean)){
      node=level.get(name);
      if(!node){node={name,children:new Map(),blocks:[]};level.set(name,node);}
      level=node.children;
    }
    node?.blocks.push(block);
  }
  function render(nodes:Map<string,Node>,prefix=''):React.ReactNode {
    return <ul>{Array.from(nodes.values()).map(node=><li key={prefix+node.name}><details open><summary>{node.name}</summary>
      {node.blocks.map(block=><button className="button-quiet" key={block.id} onClick={()=>onSelect(block)}>第 {block.page_index} 页 / 段</button>)}
      {node.children.size>0&&render(node.children,prefix+node.name+'/')}</details></li>)}</ul>;
  }
  return <details className="kb-outline"><summary>卷篇 / 章节目录</summary>{roots.size?render(roots):<p className="scope-note">尚未整理目录。校对段落的章节路径可以填写“卷一 / 某门 / 条目”，保存新修订后形成目录。</p>}</details>;
}

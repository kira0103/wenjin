import { platform } from '../platform';
import { createElement, type ReactNode } from 'react';

function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https:\/\/[^)]+\))/g).map((part,index) => {
    if (part.startsWith('**')) return <strong key={index}>{part.slice(2,-2)}</strong>;
    if (part.startsWith('`')) return <code key={index}>{part.slice(1,-1)}</code>;
    const link = /^\[([^\]]+)\]\((https:\/\/[^)]+)\)$/.exec(part);
    return link ? <button className="button-quiet" key={index} onClick={()=>void platform.openExternal(link[2])}>{link[1]}</button> : part;
  });
}

export default function MarkdownPreview({text}:{text:string}) {
  const elements:ReactNode[]=[];let code:string[]|null=null;
  for(const [index,line] of text.split('\n').entries()) {
    if(line.startsWith('```')) { if(code){elements.push(<pre key={index}><code>{code.join('\n')}</code></pre>);code=null;}else code=[];continue; }
    if(code){code.push(line);continue;}
    const heading=/^(#{1,6})\s+(.+)$/.exec(line);
    if(heading)elements.push(createElement('h'+heading[1].length,{key:index},inline(heading[2])));
    else if(line.startsWith('> '))elements.push(<blockquote key={index}>{inline(line.slice(2))}</blockquote>);
    else if(/^[-*] /.test(line))elements.push(<div key={index}>• {inline(line.slice(2))}</div>);
    else if(line.trim())elements.push(<p key={index}>{inline(line)}</p>);
  }
  if(code)elements.push(<pre key="unfinished">{code.join('\n')}</pre>);
  return <div className="kb-markdown">{elements}</div>;
}

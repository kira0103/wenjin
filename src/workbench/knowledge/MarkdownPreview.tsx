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

type ListBuffer={ordered:boolean;items:string[]};

export default function MarkdownPreview({text}:{text:string}) {
  const elements:ReactNode[]=[];let code:string[]|null=null;let list:ListBuffer|null=null;let quotes:string[]|null=null;let key=0;
  const flushList=()=>{if(!list)return;const items=list.items;elements.push(createElement(list.ordered?'ol':'ul',{key:key++},items.map((item,index)=><li key={index}>{inline(item)}</li>)));list=null;};
  const flushQuotes=()=>{if(!quotes)return;const lines=quotes;elements.push(<blockquote key={key++}>{lines.map((line,index)=><p key={index}>{inline(line)}</p>)}</blockquote>);quotes=null;};
  const flush=()=>{flushList();flushQuotes();};
  for(const raw of text.split('\n')) {
    const line=raw.replace(/\r$/,'');
    if(line.startsWith('```')) { flush(); if(code){elements.push(<pre key={key++}><code>{code.join('\n')}</code></pre>);code=null;}else code=[];continue; }
    if(code){code.push(line);continue;}
    const heading=/^(#{1,6})\s+(.+)$/.exec(line);
    // 页面已有 H1；草稿里的标题统一降 3 级，避免与页面标题抢层级。
    if(heading){flush();elements.push(createElement('h'+Math.min(6,heading[1].length+3),{key:key++},inline(heading[2])));continue;}
    const quote=/^>\s?(.*)$/.exec(line);
    if(quote){flushList();quotes=[...(quotes||[]),quote[1]];continue;}
    const unordered=/^[-*+]\s+(.+)$/.exec(line);
    if(unordered){flushQuotes();if(!list||list.ordered){flushList();list={ordered:false,items:[]};}list.items.push(unordered[1]);continue;}
    const ordered=/^\d+[.)]\s+(.+)$/.exec(line);
    if(ordered){flushQuotes();if(!list||!list.ordered){flushList();list={ordered:true,items:[]};}list.items.push(ordered[1]);continue;}
    if(/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)){flush();elements.push(<hr key={key++}/>);continue;}
    if(!line.trim()){flush();continue;}
    flush();
    elements.push(<p key={key++}>{inline(line)}</p>);
  }
  flush();
  if(code)elements.push(<pre key="unfinished"><code>{code.join('\n')}</code></pre>);
  return <div className="kb-markdown">{elements}</div>;
}

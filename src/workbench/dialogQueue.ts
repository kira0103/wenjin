import type {ReactNode} from 'react';

export type DialogOptions={title:string;description?:ReactNode;details?:{label:string;value:ReactNode}[];notes?:ReactNode[];confirmLabel?:string;cancelLabel?:string;tone?:'normal'|'danger'};
export type TextDialogOptions=DialogOptions&{label:string;defaultValue?:string;maxLength?:number;required?:boolean;match?:string};
export type DialogEntry={id:number;kind:'confirm'|'input';options:DialogOptions|TextDialogOptions};
type Result=boolean|string|null;

export function validDialogInput(value:string,options:TextDialogOptions){return (!options.required||value.trim().length>0)&&(options.maxLength===undefined||value.length<=options.maxLength)&&(options.match===undefined||value===options.match);}

/** Resolve only after a page dialog action; cancelling never starts the caller's operation. */
export class DialogQueue{
  private sequence=0;
  private pending:{entry:DialogEntry;resolve(value:Result):void}[]=[];
  private listeners=new Set<()=>void>();
  snapshot=()=>this.pending[0]?.entry??null;
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
  private emit(){for(const listener of this.listeners)listener();}
  request(kind:DialogEntry['kind'],options:DialogOptions|TextDialogOptions){return new Promise<Result>(resolve=>{this.pending.push({entry:{id:++this.sequence,kind,options},resolve});this.emit();});}
  settle(value:Result){
    const current=this.pending[0];if(!current)return false;
    if(current.entry.kind==='input'&&value!==null&&(typeof value!=='string'||!validDialogInput(value,current.entry.options as TextDialogOptions)))return false;
    if(current.entry.kind==='confirm'&&typeof value!=='boolean')return false;
    this.pending.shift();current.resolve(value);this.emit();return true;
  }
  cancel(){this.settle(this.snapshot()?.kind==='input'?null:false);}
  cancelAll(){const pending=this.pending;this.pending=[];for(const item of pending)item.resolve(item.entry.kind==='input'?null:false);this.emit();}
}

export const appDialogQueue=new DialogQueue();
export const isAppDialogOpen=()=>appDialogQueue.snapshot()!==null;
export async function confirmDialog(options:DialogOptions){return await appDialogQueue.request('confirm',options)===true;}
export async function inputDialog(options:TextDialogOptions){const result=await appDialogQueue.request('input',options);return typeof result==='string'?result:null;}
export function confirmDiscard(description:ReactNode='尚未保存的修改会被丢弃。'){return confirmDialog({title:'放弃未保存的修改？',description,confirmLabel:'放弃修改',cancelLabel:'继续编辑',tone:'danger'});}

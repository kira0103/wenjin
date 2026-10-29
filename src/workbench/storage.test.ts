import { afterEach, expect, it, vi } from 'vitest';
import { clearStorageAccount, setStorageAccount, workspaceStorage } from './storage';

function memoryStorage(){const values=new Map<string,string>();return {getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>{values.set(key,value);},removeItem:(key:string)=>{values.delete(key);},key:(index:number)=>[...values.keys()][index]??null,get length(){return values.size;}};}
afterEach(()=>{setStorageAccount('');vi.unstubAllGlobals();});
it('isolates account drafts and clears them on logout while keeping desktop preferences',()=>{
  const local=memoryStorage(),tab=memoryStorage();vi.stubGlobal('localStorage',local);vi.stubGlobal('sessionStorage',tab);
  workspaceStorage.setItem('draft','desktop');setStorageAccount('alice');workspaceStorage.setItem('draft','alice-private');
  setStorageAccount('bob');expect(workspaceStorage.getItem('draft')).toBeNull();workspaceStorage.setItem('draft','bob-private');
  clearStorageAccount('alice');setStorageAccount('alice');expect(workspaceStorage.getItem('draft')).toBeNull();
  setStorageAccount('bob');expect(workspaceStorage.getItem('draft')).toBe('bob-private');setStorageAccount('');expect(workspaceStorage.getItem('draft')).toBe('desktop');
});

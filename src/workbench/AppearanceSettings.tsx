import { Sun, Moon, Monitor } from 'lucide-react';
import type { ThemeChoice } from './appearance';
export default function AppearanceSettings({theme,onChange}:{theme:ThemeChoice;onChange(v:ThemeChoice):void}){
  return <section className="page-container appearance-settings"><h1>外观</h1><p>选择工作台主题，设置会保存在本机。</p><div className="theme-options" role="radiogroup" aria-label="界面主题">{([['light','浅色',Sun],['dark','深色',Moon],['system','跟随系统',Monitor]] as const).map(([id,label,Icon])=><button key={id} role="radio" aria-checked={theme===id} onClick={()=>onChange(id)}><Icon size={22}/><strong>{label}</strong><div className={'theme-preview theme-preview-'+id}><i/><span/><span/></div></button>)}</div></section>;
}

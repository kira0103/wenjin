export type ThemeChoice='light'|'dark'|'system';
export type LayoutPreferences={leftOpen:boolean;rightOpen:boolean;leftWidth:number;rightWidth:number};
const themeKey='shixue-appearance-v1',layoutKey='shixue-layout-v1';
export function readTheme():ThemeChoice {try{const v=localStorage.getItem(themeKey);return v==='dark'||v==='system'?v:'light';}catch{return 'light';}}
export function applyTheme(choice:ThemeChoice){
  const theme=choice==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):choice;
  document.documentElement.dataset.theme=theme;document.documentElement.style.colorScheme=theme;
  void window.desktop?.setTheme?.(theme).catch(()=>{});return theme;
}
export function saveTheme(choice:ThemeChoice){try{localStorage.setItem(themeKey,choice);}catch{}applyTheme(choice);}
export function readLayout():LayoutPreferences {try{const v=JSON.parse(localStorage.getItem(layoutKey)||'{}');return {leftOpen:v.leftOpen!==false,rightOpen:v.rightOpen!==false,leftWidth:Math.max(184,Math.min(320,Number(v.leftWidth)||224)),rightWidth:Math.max(320,Math.min(620,Number(v.rightWidth)||380))};}catch{return {leftOpen:true,rightOpen:true,leftWidth:224,rightWidth:380};}}
export function saveLayout(value:LayoutPreferences){try{localStorage.setItem(layoutKey,JSON.stringify(value));}catch{}}

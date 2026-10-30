/* Keep the two maintained guides linked to current files and startup commands. */
const fs=require('node:fs'),path=require('node:path'),{execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const maintained=['README.md','docs/MAINTENANCE.md'];
const read=file=>fs.readFileSync(file,'utf8').replace(/^\uFEFF/,'');
const npmPattern=/\bnpm\s+run\s+([A-Za-z0-9][\w:-]*)/g;
const modulePattern=/(?:^|\s)-m\s+(backend[\w.]*)/;

function anchors(content){
  const result=new Set(),counts=new Map();let fenced=false;
  for(const line of content.split(/\r?\n/)){
    if(/^(```|~~~)/.test(line))fenced=!fenced;
    if(fenced||!/^#{1,6} /.test(line))continue;
    const heading=line.replace(/^#+\s+/,'').trim().replace(/\s*#+\s*$/,'');
    const slug=heading.replace(/[^\p{L}\p{N}\p{M}_ -]/gu,'').toLowerCase().replace(/ /g,'-');
    const count=counts.get(slug)||0;counts.set(slug,count+1);result.add(count?`${slug}-${count}`:slug);
  }
  return result;
}

function validateGuide(name,content,scripts,guides){
  const errors=[];
  for(const match of content.matchAll(/(?<!!)\[[^\]\n]+\]\(([^)\n]+)\)/g)){
    const link=match[1].replace(/^<|>$/g,'');
    if(/^[\w+.-]+:|^\/\//.test(link))continue;
    const [filename,fragment]=link.split('#');
    const target=path.resolve(path.dirname(path.join(root,name)),decodeURIComponent(filename||path.basename(name)));
    const relative=path.relative(root,target).replace(/\\/g,'/');
    if(relative==='..'||relative.startsWith('../')||path.isAbsolute(relative)||!fs.existsSync(target)){
      errors.push(`${name}: link target missing: ${link}`);continue;
    }
    if(fragment&&guides[relative]&&!anchors(guides[relative]).has(decodeURIComponent(fragment)))errors.push(`${name}: heading missing: ${link}`);
  }
  for(const [,command] of content.matchAll(npmPattern))if(!Object.hasOwn(scripts,command))errors.push(`${name}: npm script missing: ${command}`);
  for(const line of content.split(/\r?\n/)){
    const invoked=[...line.matchAll(npmPattern)][0];
    const module=modulePattern.exec(invoked?scripts[invoked[1]]||'':line);
    if(!module)continue;
    const source=path.join(root,module[1].replace(/\./g,'/')+'.py');
    if(!fs.existsSync(source)){errors.push(`${name}: Python module missing: ${module[1]}`);continue;}
    const flags=new Set(['--help']);
    // Read literal positional arguments before add_argument's keyword options.
    for(const call of read(source).matchAll(/\.add_argument\(\s*((?:(?:'[^'\r\n]*'|"[^"\r\n]*")\s*,?\s*)+)/g)){
      for(const [,argument] of call[1].matchAll(/['"]([^'"]+)['"]/g))flags.add(argument);
    }
    for(const [flag] of line.split(' #')[0].matchAll(/(?<![\w-])--[a-z][\w-]*/g))if(!flags.has(flag))errors.push(`${name}: ${module[1]} has no argument ${flag}`);
  }
  return errors;
}

function main(){
  const files=execFileSync('git',['ls-files','-z','--cached','--others','--exclude-standard','--','*.md'],{cwd:root,encoding:'utf8'}).split('\0');
  const present=new Set(files.filter(name=>name&&fs.existsSync(path.join(root,name))));
  // Ignored root notes can still clutter the user's workspace.
  for(const entry of fs.readdirSync(root,{withFileTypes:true}))if(entry.isFile()&&entry.name.endsWith('.md'))present.add(entry.name);
  const errors=[];
  for(const name of present)if(!maintained.includes(name))errors.push(`Unexpected guide; consolidate or archive it: ${name}`);
  for(const name of maintained)if(!present.has(name))errors.push(`Maintained guide missing: ${name}`);
  const guides=Object.fromEntries(maintained.filter(name=>fs.existsSync(path.join(root,name))).map(name=>[name,read(path.join(root,name))]));
  const scripts=JSON.parse(read(path.join(root,'package.json'))).scripts;
  for(const [name,content] of Object.entries(guides))errors.push(...validateGuide(name,content,scripts,guides));
  if(errors.length){console.error(errors.join('\n'));process.exitCode=1;return;}
  console.log('Documentation OK: 2 guides; links, npm scripts and backend CLI arguments verified.');
}

module.exports={anchors,validateGuide};
if(require.main===module)main();

"""Text-only local loaders. No macros, scripts, formulas or remote links execute."""
import csv
import io
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
from fastapi import HTTPException


class HtmlText(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[];self.skipped=0
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','noscript'}:self.skipped+=1
        if not self.skipped and tag in {'p','div','br','h1','h2','h3','li','tr','section','article'}:self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in {'script','style','noscript'}:self.skipped=max(0,self.skipped-1)
        if not self.skipped and tag in {'p','div','h1','h2','h3','li','tr','section','article'}:self.parts.append('\n')
    def handle_data(self,data):
        if not self.skipped:self.parts.append(data)


def load(suffix,data):
    from backend.importers import read_txt
    if suffix in {'.html','.htm'}:
        text,_=read_txt(data);parser=HtmlText();parser.feed(text)
        return re.sub(r'\n{3,}','\n\n',''.join(parser.parts))
    if suffix=='.csv':
        text,_=read_txt(data)
        try:return '\n'.join(f'行 {i}：'+'\t'.join(row) for i,row in enumerate(csv.reader(io.StringIO(text)),1))
        except csv.Error:raise HTTPException(400,'CSV 内容无法解析，请核对分隔与单元格长度') from None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info=archive.infolist()
            if len(info)>30000 or sum(item.file_size for item in info)>250*1024*1024:raise HTTPException(400,'Office 文件解压后超过处理上限')
            names=set(archive.namelist());parts=[]
            if suffix=='.pptx':
                files=sorted((name for name in names if re.fullmatch(r'ppt/slides/slide\d+\.xml',name)),key=lambda name:int(re.search(r'(\d+)\.xml',name)[1]))
                for i,name in enumerate(files,1):
                    node=ET.fromstring(archive.read(name))
                    text='\n'.join(item.text or '' for item in node.iter() if item.tag.endswith('}t'))
                    parts.append(f'幻灯片 {i}\n{text}')
            elif suffix=='.xlsx':
                namespace='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
                shared=[]
                if 'xl/sharedStrings.xml' in names:
                    shared=[''.join(item.text or '' for item in node.iter(namespace+'t')) for node in ET.fromstring(archive.read('xl/sharedStrings.xml')).iter(namespace+'si')]
                sheets=sorted((name for name in names if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',name)),key=lambda name:int(re.search(r'(\d+)\.xml',name)[1]))
                for name in sheets:
                    parts.append('工作表 '+name.rsplit('/',1)[-1].removesuffix('.xml'))
                    for row in ET.fromstring(archive.read(name)).iter(namespace+'row'):
                        cells=[]
                        for cell in row.findall(namespace+'c'):
                            item=cell.find(namespace+'v');text=item.text or '' if item is not None else ''
                            if cell.get('t')=='s':text=shared[int(text)] if text else ''
                            elif cell.get('t')=='inlineStr':text=''.join(item.text or '' for item in cell.iter(namespace+'t'))
                            if cell.find(namespace+'f') is not None and not text:text='[公式无缓存值]'
                            cells.append(cell.get('r','')+'：'+text)
                        if cells:parts.append('行 '+row.get('r','?')+'\t'+'\t'.join(cells))
            text='\n\n'.join(parts)
            if not text.strip():raise HTTPException(400,'文件中没有可提取的文本或缓存单元格值')
            return text
    except HTTPException:raise
    except (zipfile.BadZipFile,KeyError,ValueError,IndexError,ET.ParseError,RuntimeError):
        raise HTTPException(400,'无法解析 Office 原件，请使用未加密的 PPTX 或 XLSX 文件') from None

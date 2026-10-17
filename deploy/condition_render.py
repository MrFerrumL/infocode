"""Faithful sanitized source HTML for native 2x browser screenshots."""
from html import escape

from html.parser import HTMLParser

import json

from pathlib import Path

import re

# Some source conditions embed illustrations hosted on K. Polyakov's site.
POLYAKOV_IMAGE = r'https://kpolyakov\.spb\.ru/cms/images/\d+\.(?:gif|png|jpe?g)'
# ...and on the Uploadcare CDN, addressed by file UUID without an extension.
UPLOADCARE_IMAGE = r'https://ucarecdn\.com/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/'
# ...and on VK's photo CDN with a signed size/quality query.
VK_IMAGE = r'https://sun\d+-\d+\.userapi\.com/impg/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+\.(?:jpg|png)\?size=\d+x\d+&quality=\d+&sign=[0-9a-f]{32}&type=album'
EXTERNAL_IMAGES = (POLYAKOV_IMAGE, UPLOADCARE_IMAGE, VK_IMAGE)

class Condition(HTMLParser):
    tags = {'p','br','table','colgroup','col','tbody','tr','td','strong','em','sub','sup','div','span','pre','code','ul','ol','li','blockquote','u'}
    void = {'br','col','img','hr','wbr'}
    properties = {'border-collapse','width','border-width','height','padding','text-align','border-spacing'}
    def __init__(self, image_resolver=None):
        super().__init__(convert_charrefs=False); self.parts=[]; self.image_resolver=image_resolver; self.skip=0
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if self.skip:
            if tag not in self.void: self.skip+=1
            return
        if tag=='span' and 'ql-formula' in (a.get('class') or '').split() and (a.get('data-value') or '').strip():
            # Quill stores a pre-rendered KaTeX copy; keep only its TeX source for the page's own KaTeX.
            self.parts.append(escape('\\('+a['data-value']+'\\)')); self.skip=1
            return
        if tag=='img':
            src=a.get('src','')
            if not src:
                # Empty editor placeholders contain no source illustration.
                if a.get('alt', '').strip():
                    raise ValueError('Image has text but no source')
                return
            if not (re.fullmatch(r'data:image/(?:png|gif|jpeg);base64,[A-Za-z0-9+/=\s]+',src) or re.fullmatch(r'https://kompege\.ru/images/[A-Za-z0-9_.-]+',src) or any(re.fullmatch(pattern,src) for pattern in EXTERNAL_IMAGES)):
                raise ValueError('Unrecognised image origin')
            if self.image_resolver:
                src=self.image_resolver(src)
            self.parts.append('<img src="'+escape(src,quote=True)+'" alt="Иллюстрация из оригинального условия">')
        elif tag in self.tags:
            allowed={key:value for key,value in attrs if key in ('border','width','height','align','dir') and re.fullmatch(r'[\w. %+-]+',value or '')}
            styles=[]
            for item in a.get('style','').split(';'):
                if ':' in item:
                    key,value=item.split(':',1)
                    if key.strip() in self.properties and re.fullmatch(r'[\w. %+-]+',value.strip()): styles.append(item)
            if styles: allowed['style']=';'.join(styles)
            self.parts.append('<'+tag+''.join(' '+key+'="'+escape(value,quote=True)+'"' for key,value in allowed.items())+'>')
        elif tag!='a': raise ValueError('Unrecognised original tag: '+tag)
    def handle_endtag(self,tag):
        if self.skip:
            if tag not in self.void: self.skip-=1
            return
        if tag in self.tags and tag not in ('br','col'): self.parts.append('</'+tag+'>')
    def handle_data(self,data):
        if not self.skip: self.parts.append(escape(data))
    def handle_entityref(self,name):
        if not self.skip: self.parts.append('&'+name+';')
    def handle_charref(self,name):
        if not self.skip: self.parts.append('&#'+name+';')

HEAD='''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Условие из оригинала</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.7/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.7/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.7/dist/contrib/auto-render.min.js"></script>
<style>*{box-sizing:border-box}html,body{margin:0;padding:0;background:white;color:#171717;width:1920px}#condition{width:960px;zoom:2;padding:16px;font:18px/1.65 Arial,"Segoe UI",sans-serif;overflow-wrap:break-word}p{margin:0 0 16px}p:last-child{margin-bottom:0}table{max-width:100%;border-collapse:collapse;font-size:18px;line-height:1.5;margin:12px 0 18px}td{min-width:24px;padding:3px 8px!important;border-color:#333;height:auto!important}tr{height:auto!important}img{max-width:100%;height:auto;vertical-align:middle}#status{width:960px;margin:0;font:14px Arial} .katex{font-size:1.06em}pre{font:16px/1.55 ui-monospace,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;tab-size:4;margin:12px 0}code{font-family:ui-monospace,Consolas,monospace}ul,ol{padding-left:28px}blockquote{margin:12px 0 16px 28px}</style>
</head><body><main id="condition">'''

TAIL='''</main><p id="status">Подготовка изображения…</p><script>
addEventListener('load',async()=>{try{
if(typeof renderMathInElement!=='function')throw Error('Не загружена библиотека формул');
renderMathInElement(document.getElementById('condition'),{delimiters:[{left:'\\\\(',right:'\\\\)',display:false},{left:'\\\\[',right:'\\\\]',display:true}],throwOnError:true});
await Promise.all([...document.images].map(image=>image.decode()));await document.fonts.ready;
document.getElementById('status').textContent='Готово к снимку 2×';
}catch(error){document.getElementById('status').textContent='Ошибка подготовки: '+error.message;}});
</script></body></html>'''

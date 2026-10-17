from django import template
from django.urls import reverse

register = template.Library()
# Screenshots of source conditions (KOMPEGE pages, K. Polyakov's DOC): zoomable, inverted in the dark theme.
ORIGINAL_IMAGES = ('imports/kompege/', 'imports/polyakov/')

@register.inclusion_tag('partials/task_blocks.html')
def task_blocks(obj, purpose='statement'):
    if hasattr(obj, 'content_blocks'):
        source = obj.catalog_blocks if hasattr(obj, 'catalog_blocks') else obj.content_blocks.all()
        blocks = [dict(b.snapshot(), url=reverse('download', args=['block', b.pk]),
                       original_image=b.kind == 'image' and str(b.image).startswith(ORIGINAL_IMAGES))
                  for b in source if b.purpose == purpose]
    else:
        blocks = [dict(b, url=reverse('download', args=['snapshot', obj.pk])+f'?block={i}',
                       original_image=b.get('kind') == 'image' and str(b.get('image', '')).startswith(ORIGINAL_IMAGES))
                  for i,b in enumerate(obj.blocks) if b.get('purpose', 'statement') == purpose]
    return {'blocks': blocks}

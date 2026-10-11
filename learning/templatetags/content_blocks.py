from django import template
from django.urls import reverse

register = template.Library()

@register.inclusion_tag('partials/task_blocks.html')
def task_blocks(obj, purpose='statement'):
    if hasattr(obj, 'content_blocks'):
        blocks = [dict(b.snapshot(), url=reverse('download', args=['block', b.pk])) for b in obj.content_blocks.filter(purpose=purpose)]
    else:
        blocks = [dict(b, url=reverse('download', args=['snapshot', obj.pk])+f'?block={i}') for i,b in enumerate(obj.blocks) if b.get('purpose', 'statement') == purpose]
    return {'blocks': blocks}

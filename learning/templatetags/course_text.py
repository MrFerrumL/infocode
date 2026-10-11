from django import template
from django.utils.safestring import mark_safe
from learning.rich_text import sanitize_html

register = template.Library()


@register.filter
def course_html(value):
    return mark_safe(sanitize_html(value))

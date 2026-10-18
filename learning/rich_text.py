"""Small, canonical HTML vocabulary for authored course text. No embedded media."""
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

TAGS = {'p', 'br', 'h2', 'h3', 'strong', 'em', 'u', 's', 'sup', 'sub', 'pre',
        'code', 'blockquote', 'ol', 'ul', 'li', 'hr', 'a', 'mark',
        # Notebooks imported from Colab: tables and folded blocks ("Текст условия").
        'details', 'summary', 'table', 'thead', 'tbody', 'tr', 'th', 'td'}
ALIASES = {'b': 'strong', 'i': 'em', 'strike': 's', 'div': 'p', 'h1': 'h2', 'h4': 'h3', 'h5': 'h3', 'h6': 'h3'}
VOID = {'br', 'hr'}


def safe_link(value):
    value = (value or '').strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or '\\' in value:
        return ''
    try:
        parts = urlsplit(value)
        if parts.scheme in ('https', 'http') and parts.netloc and not parts.username:
            return value
        if parts.scheme == 'mailto' and parts.path:
            return value
    except ValueError:
        pass
    return ''


class RichParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        tag = ALIASES.get(tag, tag)
        if tag not in TAGS:
            return
        attrs = dict(attrs)
        rendered = ''
        if tag == 'a':
            url = safe_link(attrs.get('href'))
            if url:
                rendered = ' href="' + escape(url, quote=True) + '" rel="noopener noreferrer"'
        self.output.append('<' + tag + rendered + '>')
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        tag = ALIASES.get(tag, tag)
        if tag in self.stack:
            while self.stack:
                closing = self.stack.pop()
                self.output.append('</' + closing + '>')
                if closing == tag:
                    break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        self.output.append(escape(data))


def sanitize_html(value):
    parser = RichParser()
    parser.feed(str(value or ''))
    parser.close()
    return ''.join(parser.output + ['</' + tag + '>' for tag in reversed(parser.stack)])


def plain_html(value):
    return '<p>' + escape(value).replace('\n', '<br>') + '</p>' if value else ''

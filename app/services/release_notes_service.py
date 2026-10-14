"""Render GitHub release notes, which are untrusted Markdown, into safe HTML.

The text comes from the GitHub API, so it is rendered without raw HTML and
then sanitised with an allowlist. The card shows only a preview: whole
top-level blocks (and whole list items) until a small budget is spent, so
the text is never cut inside a word or a tag.
"""

import nh3
from markdown_it import MarkdownIt
from markupsafe import Markup


MAX_SOURCE_CHARS = 20_000
PREVIEW_BUDGET = 7
_CHARS_PER_EXTRA_UNIT = 120

# Release headings sit inside a card whose own title is an h2.
_HEADING_TAGS = {'h1': 'h3', 'h2': 'h3', 'h3': 'h4', 'h4': 'h5', 'h5': 'h5', 'h6': 'h5'}
_ALLOWED_TAGS = {
    'h3', 'h4', 'h5', 'p', 'ul', 'ol', 'li', 'strong', 'em', 'code', 'pre',
    'a', 'blockquote', 'br', 'hr',
}
_ALLOWED_ATTRIBUTES = {'a': {'href', 'title'}, 'ol': {'start'}}

_markdown = MarkdownIt('commonmark', {'html': False}).disable('image')


def _block_end(tokens, start):
    """Return the index just past the block that opens at ``start``."""
    token = tokens[start]
    if token.nesting == 0:
        return start + 1
    depth = 0
    for index in range(start, len(tokens)):
        depth += tokens[index].nesting
        if depth == 0:
            return index + 1
    return len(tokens)


def _cost(tokens):
    inline = [token for token in tokens if token.type == 'inline' or token.type in ('fence', 'code_block')]
    return sum(1 + len(token.content) // _CHARS_PER_EXTRA_UNIT for token in inline) or 1


def _preview_tokens(tokens, budget):
    """Take whole blocks, splitting top-level lists only between items."""
    preview = []
    spent = 0
    index = 0
    while index < len(tokens):
        end = _block_end(tokens, index)
        block = tokens[index:end]
        if block[0].type in ('bullet_list_open', 'ordered_list_open'):
            items = []
            item_index = index + 1
            while item_index < end - 1:
                item_end = _block_end(tokens, item_index)
                item = tokens[item_index:item_end]
                if (items or preview) and spent + _cost(item) > budget:
                    break
                items.extend(item)
                spent += _cost(item)
                item_index = item_end
            if items:
                preview.extend([block[0], *items, block[-1]])
            if item_index < end - 1:
                return preview, True
        else:
            cost = _cost(block)
            if preview and spent + cost > budget:
                return preview, True
            preview.extend(block)
            spent += cost
        index = end
        if spent >= budget:
            return preview, index < len(tokens)
    return preview, False


def _sanitize(html):
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={'http', 'https', 'mailto'},
        url_relative='deny',
        link_rel='noopener noreferrer',
        set_tag_attribute_values={'a': {'target': '_blank'}},
    )


def render_release_notes(source, *, budget=PREVIEW_BUDGET):
    """Return ``(Markup, truncated)`` for a preview of the release body."""
    text = str(source or '')[:MAX_SOURCE_CHARS].strip()
    if not text:
        return Markup(''), False
    env = {}
    tokens = _markdown.parse(text, env)
    for token in tokens:
        if token.type in ('heading_open', 'heading_close'):
            token.tag = _HEADING_TAGS.get(token.tag, token.tag)
    preview, truncated = _preview_tokens(tokens, budget)
    html = _markdown.renderer.render(preview, _markdown.options, env)
    return Markup(_sanitize(html).strip()), truncated

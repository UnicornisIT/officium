import unittest
from html.parser import HTMLParser

from markupsafe import Markup

from app.services.release_notes_service import render_release_notes


class _TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _tags(html):
    collector = _TagCollector()
    collector.feed(str(html))
    return collector.tags


class ReleaseNotesRenderingTestCase(unittest.TestCase):
    def test_markdown_is_rendered_with_card_level_headings(self):
        html, truncated = render_release_notes(
            '## officium v1.0\n\n### Fixes\n- **bold** and *italic*\n- `code` and [docs](https://example.org/docs)\n'
        )
        self.assertIsInstance(html, Markup)
        self.assertFalse(truncated)
        self.assertIn('<h3>officium v1.0</h3>', html)
        self.assertIn('<h4>Fixes</h4>', html)
        self.assertIn('<li><strong>bold</strong> and <em>italic</em></li>', html)
        self.assertIn('<code>code</code>', html)
        self.assertIn(
            '<a href="https://example.org/docs" target="_blank" rel="noopener noreferrer">docs</a>',
            html,
        )
        self.assertNotIn('##', html)

    def test_untrusted_html_and_scripted_links_are_neutralised(self):
        html, _truncated = render_release_notes(
            '<script>alert(1)</script>\n\n'
            '<img src=x onerror="alert(2)">\n\n'
            '<a href="javascript:alert(3)">raw</a>\n\n'
            '[md](javascript:alert(4)) [mixed](JaVaScRiPt:alert(5)) '
            '[data](data:text/html,<script>alert(6)</script>) [rel](/admin)\n\n'
            '<svg onload="alert(7)"></svg> <iframe src="https://example.org"></iframe>\n\n'
            '![image](https://example.org/x.png)\n\n'
            '[ref][1]\n\n[1]: javascript:alert(8)\n',
            budget=100,
        )
        tags = _tags(html)
        self.assertTrue({tag for tag, _attrs in tags} <= {'p', 'a'})
        for tag, attrs in tags:
            self.assertFalse(any(name.startswith('on') for name in attrs))
            if tag == 'a' and 'href' in attrs:
                self.assertTrue(attrs['href'].startswith('https://'), attrs['href'])
                self.assertEqual(attrs.get('target'), '_blank')
                self.assertEqual(attrs.get('rel'), 'noopener noreferrer')
        self.assertIn('&lt;script&gt;', html)

    def test_preview_keeps_whole_blocks_and_list_items(self):
        items = '\n'.join(f'- item number {index} with several words' for index in range(1, 21))
        html, truncated = render_release_notes(f'## Title\n\nIntro paragraph.\n\n{items}\n\n## Later\n\nTail.')
        self.assertTrue(truncated)
        self.assertIn('<li>item number 1 with several words</li>', html)
        self.assertNotIn('item number 20', html)
        self.assertNotIn('Later', html)
        self.assertEqual(html.count('<ul>'), html.count('</ul>'))
        self.assertEqual(html.count('<li>'), html.count('</li>'))
        self.assertTrue(html.endswith('</ul>'))

    def test_long_first_block_is_kept_whole(self):
        paragraph = ' '.join(['слово'] * 400)
        html, truncated = render_release_notes(f'{paragraph}\n\nSecond block.')
        self.assertTrue(truncated)
        self.assertEqual(html, f'<p>{paragraph}</p>')

    def test_cut_preview_never_ends_with_an_orphan_heading(self):
        paragraph = 'Short intro.'
        html, truncated = render_release_notes(
            f'### Checked\n\n{paragraph}\n\n### Fixed\n\n- one\n- two\n- three\n- four\n',
            budget=3,
        )
        self.assertTrue(truncated)
        self.assertTrue(html.endswith('</p>'), html)
        self.assertNotIn('Fixed', html)

    def test_empty_body(self):
        self.assertEqual(render_release_notes(None), (Markup(''), False))
        self.assertEqual(render_release_notes('   '), (Markup(''), False))


if __name__ == '__main__':
    unittest.main()

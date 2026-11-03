import json
from pathlib import Path
from flask import render_template


def init_app(app):
    @app.route('/documentation')
    def documentation():
        try:
            manifest = json.loads((Path(app.static_folder)/'images/documentation/manifest.json').read_text(encoding='utf-8'))
            images = {image['file']: image for image in manifest['images']}
        except (OSError, ValueError, KeyError, TypeError):
            images = {}
        return render_template('documentation.html', documentation_images=images)

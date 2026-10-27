"""The complete offline replay must be served without a database connection."""
import base64
import gzip
import json
from pathlib import Path
import sys
from html.parser import HTMLParser

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routes import main  # noqa: E402


@pytest.fixture()
def client():
    application = Flask('spacewalk-route-test')
    application.root_path = str(Path(__file__).resolve().parents[1] / 'app')
    application.config['TESTING'] = True
    application.register_blueprint(main)
    return application.test_client()


@pytest.mark.parametrize('path', ['/spacewalk', '/spacewalk/'])
def test_spacewalk_serves_the_complete_html_without_database(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.mimetype == 'text/html'
    assert response.headers['Content-Language'] == 'hu'
    assert response.headers['X-Robots-Tag'] == 'noindex, nofollow'
    assert 'no-cache' in response.headers['Cache-Control']
    assert b'sr-packed-data' in response.data
    assert b'srcdoc' in response.data


def test_spacewalk_revalidates_cached_versions(client):
    first = client.get('/spacewalk')
    assert first.headers.get('ETag')
    assert client.get('/spacewalk', headers={'If-None-Match': first.headers['ETag']}).status_code == 304
    assert client.head('/spacewalk').status_code == 200


def test_deployed_snapshot_keeps_the_routes_controls_and_outcomes(client):
    class ExportParser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'iframe' and 'data-srcdoc' in attrs:
                self.inner = attrs['data-srcdoc']

    parser = ExportParser()
    parser.feed(client.get('/spacewalk').get_data(as_text=True))
    inner = parser.inner
    for control in ['sr-player', 'sr-result', 'sr-family', 'sr-target', 'sr-trial', 'sr-play',
                    'sr-current-swatch', 'sr-target-swatch', 'sr-outcome-label',
                    'sr-shortest-summary', 'sr-target-recipe']:
        assert f'id="{control}"' in inner
    prefix = '<script type="application/octet-stream" id="sr-packed-data">'
    packed = inner.split(prefix, 1)[1].split('</script>', 1)[0]
    bundle = json.loads(gzip.decompress(base64.b64decode(packed)))
    data = bundle['data']
    for target in data['targets']:
        assert len(target['recipe']) == 5
        assert all(type(count) is int and count >= 0 for count in target['recipe'])
        assert sum(target['recipe']) > 0
    assert data['people'] == 63
    assert len(data['trials']) == 5720
    assert sum(len(trial[6]) for trial in data['trials']) == data['actions'] == 241416
    assert {trial[8]['code'] for trial in data['trials']} == {
        'perfect', 'identical', 'acceptable', 'unacceptable', 'stopped', 'unknown',
    }

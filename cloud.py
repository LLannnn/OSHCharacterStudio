"""Comfy Cloud compatibility API. Never retries a submit or stores credentials."""
import json
import mimetypes
import os
from pathlib import Path
import httpx

BASE = 'https://cloud.comfy.org'


def read_key(config):
    key = os.environ.get('COMFY_API_KEY') or os.environ.get('COMFY_CLOUD_API_KEY')
    if not key:
        path = Path(config.get('key_file', '')).expanduser()
        if not path.is_file():
            raise ValueError('Set the Comfy API key file in Settings.')
        key = path.read_text(encoding='utf-8-sig').strip()
        if key.startswith('{'):
            data = json.loads(key)
            key = next((data.get(k) for k in ['COMFY_API_KEY', 'COMFY_CLOUD_API_KEY', 'api_key']
                        if data.get(k)), '')
        elif '=' in key:
            rows = [r.strip().removeprefix('export ').strip() for r in key.splitlines()
                    if r.strip() and not r.lstrip().startswith('#')]
            key = next((r.split('=', 1)[1].strip().strip('\"\'') for r in rows
                        if r.split('=', 1)[0].strip() in ['COMFY_API_KEY', 'COMFY_CLOUD_API_KEY']), '')
    if not isinstance(key, str) or not key or any(c.isspace() for c in key):
        raise ValueError('Invalid Comfy key file. Use a plain key, JSON object or environment assignment.')
    return key


class Cloud:
    def __init__(self, config, transport=None):
        self.key = read_key(config)
        self.http = httpx.Client(base_url=BASE, headers={'X-API-Key': self.key},
                                 timeout=45, follow_redirects=False, transport=transport)

    def close(self):
        self.http.close()

    def safe_error(self, error):
        text = str(error).replace(self.key, '[redacted]')
        return text[:1800]

    def request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        if response.is_error:
            # Only propagate the server message, never a full history or request body.
            detail = 'Cloud request failed.'
            try:
                data = response.json()
                error = data.get('error', {})
                detail = error.get('message', str(error)) if isinstance(error, dict) else str(error)
                if data.get('node_errors'):
                    detail += '; Node validation error: ' + json.dumps(data['node_errors'], ensure_ascii=False)
            except (ValueError, AttributeError):
                pass
            raise ValueError(self.safe_error(f'HTTP {response.status_code}: {detail}'))
        return response.json()

    def upload(self, path):
        with open(path, 'rb') as f:
            data = self.request('POST', '/api/upload/image', timeout=180,
                                data={'type': 'input', 'overwrite': 'false'},
                                files={'image': (path.name, f, mimetypes.guess_type(path)[0])})
        return '/'.join(x for x in [data.get('subfolder', ''), data['name']] if x)

    def submit(self, graph, client_id):
        data = self.request('POST', '/api/prompt', json={
            'prompt': graph, 'client_id': client_id,
            'extra_data': {'api_key_comfy_org': self.key}})
        if not data.get('prompt_id'):
            raise ValueError('Cloud returned no job ID. Check Cloud history before submitting again.')
        return data['prompt_id']

    def history(self, prompt_id):
        data = self.request('GET', '/api/history_v2/' + prompt_id)
        return data.get(prompt_id, data) if isinstance(data, dict) else {}

    def status(self, prompt_id):
        return self.request('GET', '/api/jobs/' + prompt_id)

    def download(self, item, dest):
        params = {k: item.get(k, '') for k in ['filename', 'subfolder', 'type']}
        params['type'] = params['type'] or 'output'
        with self.http.stream('GET', '/api/view', params=params, timeout=180) as response:
            if response.is_redirect:
                url = response.headers.get('location', '')
                if not url.startswith('https://'):
                    raise ValueError('Cloud returned an unsafe output URL.')
                # Do not forward the Comfy credential to signed storage URLs.
                with httpx.stream('GET', url, timeout=180, follow_redirects=True) as output:
                    output.raise_for_status()
                    with open(dest, 'wb') as f:
                        for chunk in output.iter_bytes():
                            f.write(chunk)
                return
            response.raise_for_status()
            with open(dest, 'wb') as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)

import asyncio
import copy
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ['ANTHROPIC_API_KEY'] = 'fake-anthropic-key'
os.environ['ANTHROPIC_WORKSPACE_ID'] = 'fake-workspace'
os.environ['CODEX_GATEWAY_TEST_MODE'] = '1'
os.environ['CODEX_GPT_UPSTREAM'] = 'https://gpt-test.invalid/backend-api/codex'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'true'

import httpx

task_temp = tempfile.TemporaryDirectory(prefix="codex-claude-test-")
root = Path(task_temp.name)
os.environ['CODEX_CLAUDE_DATA_DIR'] = str(root)
# These fixtures are synthetic, not copied from any account.
(root/'gpt_catalog_gpt.json').write_text(json.dumps({'models':[{
 'slug':'gpt-6.1-sol','display_name':'GPT test default','priority':1,
 'visibility':'list','base_instructions':'Synthetic test instructions'}]}))
models=[{'line':line,'id':model,'created_at':'2026-01-01T00:00:00Z',
 'effort_levels':['low','medium','high','xhigh','max'],'adaptive':True}
 for line,model in [('fable','claude-fable-5-2'),('haiku','claude-haiku-5-5'),('opus','claude-opus-5-5'),('sonnet','claude-sonnet-5-5')]]
(root/'claude_models_gpt.json').write_text(json.dumps(models))
source = Path(__file__).resolve().parents[1] / 'gateway_gpt.py'
import sys
sys.path.insert(0, str(source.parent))
spec = importlib.util.spec_from_file_location('claude_picker_gpt_test', source)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
original_gpt_slugs = {x['slug'] for x in mod.gateway.gpt_models}

seen = []
def upstream(request):
    seen.append((request.method, str(request.url), dict(request.headers), request.content))
    if request.url.path.endswith('/models'):
        return httpx.Response(200, json={'models': mod.gateway.gpt_models})
    return httpx.Response(201, headers={'content-type':'text/event-stream', 'x-test-reply':'yes'}, stream=httpx.ByteStream(b'event: response.completed\ndata: {"ok":true}\n\n'))

class Event:
    def __init__(self, kind, data):
        self.type = kind
        self.data = data
    def model_dump(self, exclude_none=True):
        return self.data

claude_calls = []
async def fake_aresponses(**kwargs):
    claude_calls.append(kwargs)
    if kwargs.get('stream') is False:
        return SimpleNamespace(model_dump=lambda exclude_none=True: {'output':[{'type':'message','content':[{'type':'output_text','text':'The prior task created a file and verified its result.'}]}]})
    async def events():
        yield Event('response.created', {'type':'response.created','response':{'id':'resp_fake'}})
        yield Event('response.output_item.done', {'type':'response.output_item.done','item':{'type':'function_call','name':'shell','call_id':'call_fake','arguments':'{}'}})
        yield Event('response.completed', {'type':'response.completed','response':{'id':'resp_fake','output':[]}})
    return events()

async def run():
    old_client = mod.gateway.client
    mod.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    mod.litellm.aresponses = fake_aresponses
    await mod.startup()
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:42851') as client:
        resp = await client.get('/backend-api/codex/models')
        assert resp.status_code == 200, resp.text
        catalog = resp.json()['models']
        gpt = [m for m in catalog if not m['slug'].startswith('claude-')]
        claude = [m for m in catalog if m['slug'].startswith('claude-')]
        assert {x['slug'] for x in gpt} == original_gpt_slugs and len(claude) == 4
        assert min((m for m in catalog if m.get('visibility') == 'list'), key=lambda m:m['priority'])['slug'] == 'gpt-6.1-sol'
        assert {m['slug'] for m in claude} == {f'claude-{x}-latest' for x in ('fable','haiku','opus','sonnet')}
        for m in claude:
            assert [x['effort'] for x in m['supported_reasoning_levels']] == ['low','medium','high','xhigh','max']
            assert m['context_window'] == 1000000
        # Force a stale refresh independently of the Windows runner's uptime.
        mod.gateway.last_gpt_refresh = float('-inf')
        etag = resp.headers['etag']
        resp = await client.get('/backend-api/codex/models', headers={'if-none-match':etag,'authorization':'Bearer fake-gpt-token'})
        assert resp.status_code == 304
        if mod.gateway.gpt_task:
            await mod.gateway.gpt_task
        assert seen[-1][0] == 'GET' and 'if-none-match' not in seen[-1][2]
        payload = b'{"model":"gpt-6.1-sol","input":"hello","stream":true}'
        resp = await client.post('/backend-api/codex/responses?test=1', content=payload, headers={'authorization':'Bearer fake-gpt-token','ChatGPT-Account-ID':'fake-account','content-type':'application/json'})
        assert resp.status_code == 201 and resp.headers['x-test-reply'] == 'yes'
        assert resp.content.endswith(b'\n\n')
        method,url,headers,body = seen[-1]
        assert method == 'POST' and url.endswith('/responses?test=1') and body == payload
        assert headers['authorization'] == 'Bearer fake-gpt-token' and headers['chatgpt-account-id'] == 'fake-account'
        for effort in ('low','medium','high','xhigh','max'):
            body = {'model':'claude-fable-latest','input':[{'role':'user','content':'hello'}], 'reasoning':{'effort':effort}, 'tools':[{'type':'function','name':'shell','description':'fake','parameters':{'type':'object','properties':{}}}], 'stream':True}
            resp = await client.post('/backend-api/codex/responses', json=body, headers={'thread-id':'thread-old','authorization':'Bearer fake-gpt-token','ChatGPT-Account-ID':'fake-account'})
            assert resp.status_code == 200 and 'response.output_item.done' in resp.text, resp.text
            call = claude_calls[-1]
            assert call['model'] == 'anthropic/claude-fable-5-2' and call['api_key'] == 'fake-anthropic-key'
            assert call['extra_headers'] == {'anthropic-workspace-id':'fake-workspace'}
            assert call['reasoning'] == {'effort':effort} and 'authorization' not in call['extra_headers']
        next_fable = copy.deepcopy(mod.gateway.claude_models[0])
        assert next_fable['line'] == 'fable'
        next_fable['id'] = 'claude-fable-5-3'
        next_fable['created_at'] = '2026-12-01T00:00:00Z'
        mod.gateway.claude_models[0] = next_fable
        mod.gateway.known_models[next_fable['id']] = next_fable
        mod.save_json(mod.CLAUDE_CACHE, mod.gateway.claude_models)
        mod.save_json(mod.HISTORY_CACHE, mod.gateway.known_models)
        for thread, expected in (('thread-old','claude-fable-5-2'),('thread-new','claude-fable-5-3')):
            resp = await client.post('/backend-api/codex/responses', json={'model':'claude-fable-latest','input':'hi','stream':True}, headers={'thread-id':thread})
            assert resp.status_code == 200
            assert claude_calls[-1]['model'] == 'anthropic/' + expected
        await mod.gateway.client.aclose()
        mod.gateway = mod.GatewayState()
        await mod.gateway.client.aclose()
        mod.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        for thread, expected in (('thread-old','claude-fable-5-2'),('thread-new','claude-fable-5-3')):
            resp = await client.post('/backend-api/codex/responses', json={'model':'claude-fable-latest','input':'hi','stream':True}, headers={'thread-id':thread})
            assert resp.status_code == 200
            assert claude_calls[-1]['model'] == 'anthropic/' + expected
        compact_request = {'model':'claude-fable-latest','input':[{'role':'user','content':[{'type':'input_text','text':'Earlier work'}]},{'type':'compaction_trigger'}],'stream':True}
        resp = await client.post('/backend-api/codex/responses', json=compact_request, headers={'thread-id':'thread-compact'})
        assert resp.status_code == 200 and 'response.output_item.done' in resp.text and 'response.completed' in resp.text
        event_line = next(line for line in resp.text.splitlines() if line.startswith('data: {'))
        compact_item = json.loads(event_line[6:])['item']
        assert compact_item['type'] == 'compaction' and compact_item['encrypted_content'].startswith('gwcmp1.')
        await mod.gateway.client.aclose()
        mod.gateway = mod.GatewayState()
        await mod.gateway.client.aclose()
        mod.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        replay = {'model':'claude-fable-latest','input':[compact_item, {'role':'user','content':[{'type':'input_text','text':'Continue'}]}],'stream':True}
        resp = await client.post('/backend-api/codex/responses', json=replay, headers={'thread-id':'thread-compact'})
        assert resp.status_code == 200
        assert claude_calls[-1]['input'][0]['role'] == 'assistant'
        assert 'prior task created a file' in claude_calls[-1]['input'][0]['content'][0]['text']
        resp = await client.post('/backend-api/codex/responses', json={'model':'gpt-6.1-sol','input':replay['input'],'stream':True}, headers={'authorization':'Bearer fake-gpt-token'})
        assert resp.status_code == 201
        forwarded = next(row[3] for row in reversed(seen) if row[0] == 'POST')
        assert b'Earlier conversation summary' in forwarded and b'gwcmp1.' not in forwarded
        bad = {'model':'claude-fable-latest','input':[{'type':'compaction','encrypted_content':'openai_opaque'}],'stream':True}
        resp = await client.post('/backend-api/codex/responses', json=bad, headers={'thread-id':'thread-bad'})
        assert resp.status_code == 409
        import gzip
        compressed = gzip.compress(payload)
        resp = await client.post('/backend-api/codex/responses', content=compressed, headers={'content-encoding':'gzip','authorization':'Bearer fake-gpt-token'})
        assert resp.status_code == 201 and next(row[3] for row in reversed(seen) if row[0] == 'POST') == compressed
    await mod.gateway.client.aclose()
    await old_client.aclose()
    print('PASS: GPT catalog preserved + 4 Claude choices; GPT catalog default; five levels; ETag refresh; GPT forwarding; Claude routing and stream; old/new version pins survive restart; compressed GPT forwarded; DPAPI compaction and replay across providers.')

try:
    asyncio.run(run())
finally:
    task_temp.cleanup()


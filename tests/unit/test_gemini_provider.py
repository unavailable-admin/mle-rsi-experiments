import asyncio
import json
import httpx
import pytest
from dream_rsi.config import ModelConfig
from mle_rsi.provider import model_service
from mle_rsi.ledger import Ledger


@pytest.mark.asyncio
async def test_gemini_native_wire_billing_and_resume(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXTURE_KEY', 'test-only')
    model = {'provider': 'gemini', 'base_url': 'https://fixture.invalid', 'model': 'gemini-fixture', 'api_key_env': 'FIXTURE_KEY', 'context_tokens': 10000, 'max_output_tokens': 100}
    config = ModelConfig(**{**model, 'provider': 'external'})
    settings = {'model': model, 'reservation_rates': {'input_usd_per_million': 1, 'output_usd_per_million': 2}, 'billing_delays': [0]}
    posts = []
    def handler(request):
        if request.method == 'POST':
            posts.append(json.loads(request.content))
            assert request.url.path.endswith('/v1beta/models/gemini-fixture:generateContent')
            return httpx.Response(200, headers={'x-request-id': 'bill-1'}, json={'modelVersion': 'gemini-fixture',
                'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'thought': True, 'text': 'hidden'}, {'text': 'final'}]}}], 'usageMetadata': {'totalTokenCount': 9}})
        return httpx.Response(200, json={'amount': {'value': .001, 'currency': 'USD'}})
    factory = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)
    ledger = Ledger(tmp_path, {'total': {'api_usd': 1}})
    try:
        service = model_service(tmp_path, 'fixture', ledger, settings, asyncio.Semaphore(1), client_factory=factory)
        result = await service.generate(config, [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'hello'}], 'request-1', 'discovery', 'hash-1')
        assert result == {'status': 'completed', 'text': 'final', 'tokens': 9, 'cost_usd': .001}
        assert posts[0]['systemInstruction']['parts'][0]['text'] == 'system'
        assert await service.lookup('request-1', 'hash-1') == result
        assert len(posts) == 1
    finally:
        ledger.close()

"""TeamO GPT chat transport with durable accounting; retries only proven pre-send connection failures."""
import asyncio
import hashlib
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from .common import Budget, BudgetExhausted, LimitReached, read, write


class ConnectionNotSent(RuntimeError):
    """Connection setup failed before the HTTP request could be transmitted."""
    definitely_not_sent = True


class GPTTransport:
    def __init__(self, root, spec, task, phase, campaign_id):
        self.root, self.spec, self.task, self.phase, self.campaign_id = Path(root), spec, task, phase, campaign_id
        self.budget = Budget(root, spec['budget'])
        self.gate = asyncio.Semaphore(spec['model'].get('request_parallelism', 1))

    async def __call__(self, config, messages, request_id, role):
        async with self.gate:
            return await self._call(config, messages, request_id, role)

    async def _call(self, config, messages, request_id, role):
        ident = f'{self.campaign_id}:{request_id}'
        from .common import request_namespace
        namespace = request_namespace(self.spec, self.task)
        if namespace: ident=namespace+':'+ident
        directory = self.root/'api'/(namespace+'-'+self.campaign_id if namespace else self.campaign_id)/hashlib.sha256(request_id.encode()).hexdigest()[:24]
        payload = {'model': config.model, 'messages': messages, 'reasoning_effort': config.reasoning_effort,
                   'max_completion_tokens': config.max_output_tokens, 'stream': False}
        pricing = self.spec['model']['reservation_rates'][config.model]
        input_rate, output_rate = pricing['input_usd_per_million'], pricing['output_usd_per_million']
        if not all(math.isfinite(x) and x > 0 for x in (input_rate, output_rate)):
            raise ValueError('Invalid reservation prices')
        if (directory/'request.json').exists():
            payload['max_completion_tokens'] = read(directory/'request.json')['max_completion_tokens']
        else:
            with self.budget.state() as ledger:
                rows = [r for r in ledger['api'].values() if r['task'] == self.task]
                remaining = self.budget.limits(self.task)['api_usd_per_task'] - sum(r.get('charged_usd', r['reserved_usd']) for r in rows)
                if self.phase == 'development':
                    remaining = min(remaining, self.budget.limits(self.task)['development_api_usd_per_task'] -
                                    sum(r.get('charged_usd', r['reserved_usd']) for r in rows if r['phase'] == self.phase))
                if 'api_usd_total' in self.spec['budget']:
                    remaining = min(remaining, self.spec['budget']['api_usd_total'] -
                                    sum(r.get('charged_usd', r['reserved_usd']) for r in ledger['api'].values()))
            # UTF-8 bytes are a conservative token bound; include protocol overhead.
            input_reserve = (len(json.dumps(payload, ensure_ascii=False).encode()) + 8192) * input_rate / 1e6
            available = int((remaining - input_reserve - .001) * 1e6 / output_rate)
            minimum = min(config.max_output_tokens, self.spec['model'].get('minimum_output_tokens', 8192))
            if available < minimum:
                raise BudgetExhausted('Insufficient budget for minimum GPT output allowance')
            payload['max_completion_tokens'] = min(config.max_output_tokens, available)
        raw = json.dumps(payload, ensure_ascii=False).encode()
        request_hash = hashlib.sha256(raw).hexdigest()
        reserve = (len(raw) + 8192) * input_rate / 1e6 + payload['max_completion_tokens'] * output_rate / 1e6
        old = self.budget.reserve_api(ident, self.task, self.phase, reserve, request_hash)
        if old['request_hash'] != request_hash:
            raise RuntimeError('Request ID reused with different input')
        if old['state'] == 'completed':
            return read(directory/'adapted.json')
        if (directory/'request.json').exists() and old['state'] != 'not_sent':
            raise LimitReached('Incomplete prior request: never resend automatically')
        write(directory/'request.json', payload)
        self.budget.update_api(ident, state='pending')
        base = self.spec['model']['base_url'].rstrip('/')
        provider_id = None
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=20, pool=20), trust_env=self.spec['model'].get('trust_env', True),
                    headers={'Authorization': 'Bearer ' + os.environ[config.api_key_env]}) as client:
                response = await self._post_connected(client, base+'/v1/chat/completions', payload, directory, ident)
                provider_id = response.headers.get('x-request-id') or response.headers.get('request-id')
                self.budget.update_api(ident, provider_request_id=provider_id, role=role,
                                       response_seconds=time.monotonic()-started, http_status=response.status_code)
                body = response.json()
                write(directory/'response.json', body)
                if not provider_id:
                    raise RuntimeError('Provider returned no billing request ID')
                bill = None
                for wait in (0, 2, 4, 8, 16, 16):
                    if wait:
                        await asyncio.sleep(wait)
                    try:
                        billed = await client.get(base+'/v1/billing/requests/'+quote(provider_id, safe=''), timeout=30)
                        billed.raise_for_status()
                        candidate = billed.json()
                        amount = candidate['amount']
                        cost = float(amount['value'])
                        if amount['currency'] != 'USD' or not math.isfinite(cost) or cost < 0:
                            raise ValueError('Invalid provider bill')
                        bill = candidate
                        break
                    except (httpx.HTTPError, KeyError):
                        continue
                if bill is None:
                    raise RuntimeError('Billing unavailable; retain full reservation')
                write(directory/'billing.json', bill)
            choices = body.get('choices') or []
            finish = choices[0].get('finish_reason') if choices else None
            self.budget.update_api(ident, state='rejected', charged_usd=cost, response_model=body.get('model'),
                                   finish_reason=finish, finished_at=time.time())
            if cost > reserve:
                raise RuntimeError('Actual charge exceeded conservative reservation')
            response.raise_for_status()
            if body.get('model') != config.model:
                raise RuntimeError('Unexpected response model identity')
            if finish != 'stop':
                raise RuntimeError('Incomplete GPT response: '+str(finish))
            text = choices[0].get('message', {}).get('content')
            if not isinstance(text, str) or not text.strip():
                raise RuntimeError('Empty GPT output')
            adapted = {'choices': [{'message': {'content': text}}],
                       'usage': {'total_tokens': (body.get('usage') or {}).get('total_tokens'), 'cost_usd': cost}}
            write(directory/'adapted.json', adapted)
            self.budget.update_api(ident, state='completed')
            return adapted
        except BaseException as exc:
            with self.budget.state() as ledger:
                row = ledger['api'][ident]
                if isinstance(exc, ConnectionNotSent):
                    row.update(state='not_sent', provider_request_id=None)
                elif row['state'] == 'pending':
                    row.update(state='uncertain', provider_request_id=provider_id)
                row['error_type'] = type(exc).__name__
            raise

    async def _post_connected(self, client, url, payload, directory, ident):
        evidence = directory/'connection-attempts.json'
        attempts = read(evidence) if evidence.exists() else []
        for index in range(3):
            try:
                return await client.post(url, json=payload)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                attempts.append({'time': time.time(), 'error_type': type(exc).__name__,
                                 'request_not_sent': True})
                write(evidence, attempts)
                self.budget.update_api(ident, connection_failures=len(attempts))
                control = self.root/'control.json'
                stopping = control.exists() and read(control).get('action') in ('stop', 'pause')
                if index == 2 or stopping:
                    raise ConnectionNotSent(f'Connection setup failed after {index+1} attempts; request not sent') from exc
                await asyncio.sleep(2**index)
                if control.exists() and read(control).get('action') in ('stop', 'pause'):
                    raise ConnectionNotSent('Stopped during connection backoff; request not sent') from exc

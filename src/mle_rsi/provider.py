"""Compatible chat plus gateway billing; unknown requests are never blindly resent."""
import asyncio
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import quote
import httpx
from dream_rsi.errors import RequestNotSent, RequestSkipped, ExternalUnknown, BudgetExceeded, AgentStopped
from .io import read,write,digest
from .ledger import BudgetExhausted, EvidenceConflict


class ModelService:
    def __init__(self, root, task, ledger, settings, gate, control=None, client_factory=None):
        self.root=Path(root);self.task=task;self.ledger=ledger;self.settings=settings
        self.gate=gate;self.control=control
        self.client_factory=client_factory or httpx.AsyncClient
        self.backoff_path=self.root/'api-control'/(task+'.json')
        saved=read(self.backoff_path) if self.backoff_path.exists() else {}
        self.failures=saved.get('failures',0);self.retry_at=saved.get('retry_at',0)

    def directory(self, ident):
        return self.root/'api'/digest(ident)

    def client(self, config=None):
        model=config or self.settings['model']
        name=model.api_key_env if hasattr(model,'api_key_env') else model['api_key_env']
        if not os.environ.get(name):raise RequestNotSent('Missing configured model credential')
        return self.client_factory(headers={'Authorization':'Bearer '+os.environ[name]},
            timeout=httpx.Timeout(self.settings.get('timeout_seconds',600),connect=20),
            trust_env=self.settings.get('trust_env',True))

    async def lookup(self, request_id, input_hash):
        directory=self.directory(request_id)
        row=self.ledger.get(request_id)
        if row is None:return None
        if row['input_hash']!=input_hash:raise EvidenceConflict('Provider ID reused for another input')
        result=directory/'result.json'
        if result.exists():return read(result)
        if row['state']=='not_sent':return {'status':'not_sent'}
        if not (directory/'sending.json').exists():
            if row['state']=='reserved':return {'status':'not_sent'}
            raise ExternalUnknown('Missing send evidence for '+request_id)
        if (directory/'response.json').exists():
            try:
                async with self.client() as client:
                    return await self.finish(client,request_id,directory)
            except (httpx.HTTPError,RequestNotSent):
                pass
        return {'status':'unknown','reason':'No definitive response/billing evidence: '+request_id}

    async def bill(self,client,directory,provider_id):
        if (directory/'billing.json').exists():return read(directory/'billing.json')['cost_usd']
        if not provider_id:return None
        base=self.settings.get('billing_base_url', self.settings['model']['base_url']).rstrip('/')
        for delay in self.settings.get('billing_delays',[0,2,4]):
            if delay:await asyncio.sleep(delay)
            try:
                response=await client.get(base+self.settings.get('billing_path','/v1/billing/requests/')+quote(provider_id,safe=''))
                response.raise_for_status()
                bill=response.json();cost=float(bill['amount']['value'])
                if bill['amount']['currency']!='USD' or not math.isfinite(cost) or cost<0:raise ValueError('Invalid bill')
                write(directory/'billing.json',{'cost_usd':cost,'provider_bill':bill})
                return cost
            except (httpx.HTTPError,ValueError,KeyError,TypeError):continue
        return None

    async def finish(self,client,ident,directory):
        response=read(directory/'response.json')
        cost=await self.bill(client,directory,response.get('provider_id'))
        if cost is not None:
            self.ledger.record(ident,'completed',{'billing':str(directory/'billing.json')},{'api_usd':cost})
        if response['status']==504 and self.settings.get('continue_on_504',False):
            if cost is None:self.ledger.record(ident,'skipped',{'reason':'HTTP 504; unknown fee reservation retained'})
            result={'status':'skipped','reason':'HTTP 504; external accounting retained'}
        elif response['status']!=200 or cost is None:
            result={'status':'unknown','reason':'Response or billing unresolved'}
        else:
            result = self.decode(response['body'], cost)
        if result['status']!='unknown':write(directory/'result.json',result)
        return result

    def decode(self, body, cost):
        choice = (body.get('choices') or [{}])[0]
        text = choice.get('message', {}).get('content')
        if body.get('model') != self.settings['model']['model'] or choice.get('finish_reason') != 'stop' or not isinstance(text, str) or not text.strip():
            return {'status': 'unknown', 'reason': 'Incomplete or mismatched model response; fee retained'}
        return {'status': 'completed', 'text': text, 'tokens': body.get('usage', {}).get('total_tokens'), 'cost_usd': cost}

    def request(self, config, messages):
        payload = {'model': config.model, 'messages': messages, 'max_completion_tokens': config.max_output_tokens, 'stream': False}
        if config.reasoning_effort is not None:
            payload['reasoning_effort'] = config.reasoning_effort
        if config.temperature is not None:
            payload['temperature'] = config.temperature
        return config.base_url.rstrip('/') + self.settings.get('chat_path', '/v1/chat/completions'), payload

    async def generate(self,config,messages,request_id,role,input_hash):
        existing=await self.lookup(request_id,input_hash)
        if existing is not None and existing['status']!='not_sent':return existing
        while time.time()<self.retry_at:
            if self.control is not None:await self.control.check('provider_backoff')
            await asyncio.sleep(min(1,self.retry_at-time.time()))
        async with self.gate:
            # An admitted batch drains; pause does not interrupt its remaining requests.
            directory=self.directory(request_id)
            url, payload = self.request(config, messages)
            rates=self.settings['reservation_rates']
            reserve=((len(json.dumps(payload,ensure_ascii=False).encode())+8192)*rates['input_usd_per_million']+
                     config.max_output_tokens*rates['output_usd_per_million'])/1e6
            try:self.ledger.reserve(request_id,self.task,input_hash,{'api_usd':reserve})
            except BudgetExhausted as exc:raise BudgetExceeded(str(exc)) from exc
            write(directory/'request.json',{'id':request_id,'input_hash':input_hash,'role':role,'payload':payload})
            try:
                async with self.client(config) as client:
                    write(directory/'sending.json',{'at':time.time()})
                    self.ledger.record(request_id,'running')
                    response=await client.post(url,json=payload)
                    try:body=response.json()
                    except ValueError:body={'raw_body':response.text[:8192]}
                    write(directory/'response.json',{'status':response.status_code,'body':body,
                        'provider_id':response.headers.get('x-request-id') or response.headers.get('request-id')})
                    if response.status_code==504:
                        self.failures+=1
                        if self.failures>=5:self.retry_at=time.time()+min(300,30*2**min(4,(self.failures-5)//5))
                    else:self.failures=0;self.retry_at=0
                    write(self.backoff_path,{'failures':self.failures,'retry_at':self.retry_at})
                    return await self.finish(client,request_id,directory)
            except (httpx.ConnectError,httpx.ConnectTimeout,httpx.PoolTimeout,RequestNotSent) as exc:
                self.ledger.record(request_id,'not_sent',{'error':str(exc)})
                raise RequestNotSent(str(exc)) from exc
            except (asyncio.CancelledError,KeyboardInterrupt):
                self.ledger.record(request_id,'unknown')
                raise
            except Exception as exc:
                if self.ledger.get(request_id)['state']!='completed':
                    self.ledger.record(request_id,'unknown',{'error':str(exc)})
                raise ExternalUnknown(str(exc)) from exc


class GeminiService(ModelService):
    """Native generateContent wire format with the same durable gateway billing.

    Reference: https://ai.google.dev/api/generate-content
    Thought parts are excluded from candidate text. Token counts are evidence,
    never a substitute for the actual gateway USD bill.
    """
    def request(self, config, messages):
        system = []
        contents = []
        for message in messages:
            if not isinstance(message.get('content'), str):
                raise RequestNotSent('Gemini text adapter requires string content')
            if message['role'] == 'system':
                system.append({'text': message['content']})
            elif message['role'] in ('user', 'assistant'):
                contents.append({'role': 'model' if message['role'] == 'assistant' else 'user',
                                 'parts': [{'text': message['content']}]})
            else:
                raise RequestNotSent('Unsupported Gemini message role')
        generation = {'maxOutputTokens': config.max_output_tokens}
        if config.temperature is not None:
            generation['temperature'] = config.temperature
        if self.settings.get('thinking_config') is not None:
            generation['thinkingConfig'] = self.settings['thinking_config']
        payload = {'contents': contents, 'generationConfig': generation}
        if system:
            payload['systemInstruction'] = {'parts': system}
        url = config.base_url.rstrip('/') + '/v1beta/models/' + quote(config.model, safe='') + ':generateContent'
        return url, payload

    def decode(self, body, cost):
        candidates = body.get('candidates') or []
        versions = self.settings.get('accepted_model_versions', [self.settings['model']['model']])
        if len(candidates) != 1 or candidates[0].get('finishReason') != 'STOP' or body.get('modelVersion') not in versions:
            return {'status': 'unknown', 'reason': 'Incomplete, blocked or mismatched Gemini response; fee retained'}
        parts = candidates[0].get('content', {}).get('parts', [])
        text = ''.join(p.get('text', '') for p in parts if not p.get('thought', False))
        if not text.strip():
            return {'status': 'unknown', 'reason': 'Gemini returned no final text; fee retained'}
        return {'status': 'completed', 'text': text, 'tokens': body.get('usageMetadata', {}).get('totalTokenCount'), 'cost_usd': cost}


def model_service(root, task, ledger, settings, gate, control=None, client_factory=None):
    cls = GeminiService if settings['model']['provider'] == 'gemini' else ModelService
    return cls(root, task, ledger, settings, gate, control, client_factory)

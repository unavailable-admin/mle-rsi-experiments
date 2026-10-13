"""Gemini native API, durable request IDs, conservative reservations and actual billing."""
import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from .common import Budget, LimitReached, read, write


def load_key(spec):
    name=spec['model']['api_key_env']
    if not os.environ.get(name):
        # Operational credential loading; never put this file in prompts or run artifacts.
        if not spec['model'].get('credential_file'):
            raise RuntimeError('Set '+name+' or configure an explicit credential_file')
        content=Path(spec['model']['credential_file']).read_text()
        keys=re.findall(r'sk-[A-Za-z0-9_-]+',content)
        if len(keys)!=1:raise RuntimeError('Expected exactly one key in configured credential file')
        os.environ[name]=keys[0]


class GeminiTransport:
    def __init__(self, root, spec, task, phase, campaign_id):
        self.root,self.spec,self.task,self.phase,self.campaign_id=Path(root),spec,task,phase,campaign_id
        self.budget=Budget(root,spec['budget'])
        self.gate=asyncio.Lock()

    async def __call__(self, config, messages, request_id, role):
        async with self.gate:
            return await self._call(config,messages,request_id,role)

    async def _call(self, config, messages, request_id, role):
        base=self.spec['model']['base_url'].rstrip('/')
        key=os.environ[config.api_key_env]
        system='\n\n'.join(m['content'] for m in messages if m['role']=='system')
        contents=[{'role':'model' if m['role']=='assistant' else 'user','parts':[{'text':m['content']}]} for m in messages if m['role']!='system']
        payload={'contents':contents,'systemInstruction':{'parts':[{'text':system}]},
                 'generationConfig':{'temperature':1,'maxOutputTokens':config.max_output_tokens,
                                     'thinkingConfig':{'thinkingLevel':'HIGH'}}}
        ident=f'{self.campaign_id}:{request_id}'
        directory=self.root/'api'/self.campaign_id/hashlib.sha256(request_id.encode()).hexdigest()[:24]
        if (directory/'request.json').exists():
            payload['generationConfig']['maxOutputTokens']=read(directory/'request.json')['generationConfig']['maxOutputTokens']
        else:
            with self.budget.state() as ledger:
                rows=[r for r in ledger['api'].values() if r['task']==self.task]
                remaining=self.spec['budget']['api_usd_per_task']-sum(r.get('charged_usd',r['reserved_usd']) for r in rows)
                if self.phase=='development':
                    remaining=min(remaining,self.spec['budget']['development_api_usd_per_task']-sum(r.get('charged_usd',r['reserved_usd']) for r in rows if r['phase']==self.phase))
            input_reserve=(len(json.dumps(payload,ensure_ascii=False).encode())+8192)*8/1e6
            available=int((remaining-input_reserve-.001)*1e6/36)
            if available<min(config.max_output_tokens,32768):
                raise LimitReached('Insufficient budget for a complete response (minimum 32768 output-token allowance)')
            payload['generationConfig']['maxOutputTokens']=min(config.max_output_tokens,available)
        raw=json.dumps(payload,ensure_ascii=False).encode()
        request_hash=hashlib.sha256(raw).hexdigest()
        # Conservative reservation; settle each task's call before starting its next.
        reserve=(len(raw)+8192)*8/1e6+payload['generationConfig']['maxOutputTokens']*36/1e6
        old=self.budget.reserve_api(ident,self.task,self.phase,reserve,request_hash)
        if old['request_hash']!=request_hash:raise RuntimeError('Request ID reused with different input')
        if old['state']=='completed':return read(directory/'adapted.json')
        if (directory/'request.json').exists():raise LimitReached('Incomplete prior request: never resend automatically')
        write(directory/'request.json',payload)
        request_provider_id=None
        try:
            async with httpx.AsyncClient(timeout=600,trust_env=self.spec['model'].get('trust_env',True),headers={'Authorization':'Bearer '+key}) as client:
                response=await client.post(base+'/v1beta/models/'+config.model+':generateContent',json=payload)
                request_provider_id=response.headers.get('x-request-id') or response.headers.get('request-id')
                response.raise_for_status()
                body=response.json()
                write(directory/'response.json',body)
                if not request_provider_id:raise RuntimeError('Provider did not return billing request ID')
                bill=None
                for wait in (0,2,4,8,16,16):
                    if wait:await asyncio.sleep(wait)
                    try:
                        billed=await client.get(base+'/v1/billing/requests/'+quote(request_provider_id,safe=''),timeout=30)
                        billed.raise_for_status();candidate=billed.json()
                        amount=candidate['amount']
                        if amount['currency']!='USD':raise ValueError('Unexpected billing currency')
                        cost=float(amount['value'])
                        if not 0<=cost<100:raise ValueError('Invalid billed cost')
                        bill=candidate;break
                    except (httpx.HTTPError,KeyError):continue
                if bill is None:raise RuntimeError('Billing unavailable; keep full reservation')
                write(directory/'billing.json',bill)
            # Charge rejected/incomplete responses too; no silent retry.
            self.budget.update_api(ident,state='rejected',charged_usd=cost,provider_request_id=request_provider_id,
                                   role=role,response_model=body.get('modelVersion'),finish_reason=(body.get('candidates') or [{}])[0].get('finishReason'),finished_at=time.time())
            if cost>reserve:raise RuntimeError('Actual charge exceeded conservative reservation')
            if body.get('modelVersion')!=config.model:raise RuntimeError('Unexpected response model identity')
            candidates=body.get('candidates',[])
            if not candidates or candidates[0].get('finishReason')!='STOP':raise RuntimeError('Incomplete Gemini response: '+str(candidates[0].get('finishReason') if candidates else 'no candidate'))
            text=''.join(p.get('text','') for p in candidates[0]['content']['parts'] if not p.get('thought'))
            if not text.strip():raise RuntimeError('Empty Gemini output')
            adapted={'choices':[{'message':{'content':text}}],
                     'usage':{'total_tokens':body.get('usageMetadata',{}).get('totalTokenCount'),'cost_usd':cost}}
            write(directory/'adapted.json',adapted)
            self.budget.update_api(ident,state='completed')
            return adapted
        except BaseException as exc:
            with self.budget.state() as state:
                row=state['api'][ident]
                if row['state']=='pending':row.update(state='uncertain',provider_request_id=request_provider_id)
                row['error_type']=type(exc).__name__
            raise

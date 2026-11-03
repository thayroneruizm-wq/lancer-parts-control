"""Lancer Parts Control — first operational release.
FastAPI + SQLite, server-side access control, no external API integrations enabled.
"""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent
INSTANCE = Path(os.environ.get('LPC_INSTANCE', ROOT / 'instance'))
INSTANCE.mkdir(parents=True, exist_ok=True)
DB = INSTANCE / 'lancer_parts.db'
TZ = ZoneInfo('America/Sao_Paulo')
ROLES = ('admin','gerente','vendedor','logistica','estoque','financeiro')
SALES = ('admin','gerente','vendedor')
OPS = ('admin','gerente','vendedor','logistica')
STAGES = ('Novo contato','Cliente interessado','Produto definido','Orçamento enviado','Em negociação','Aguardando pagamento','Venda concluída','Venda perdida','Retomar futuramente')
OPERATIONS = ('Pedido recebido','Aguardando pagamento','Pagamento aprovado','Aguardando separação','Em produção','Em preparação/acabamento','Pronto para envio','Etiqueta gerada','Aguardando coleta/postagem','Enviado','Em trânsito','Saiu para entrega','Entregue','Ocorrência logística','Cancelado/devolvido')
PAYMENTS = ('Pendente','Aprovado','Parcial','Reembolsado','Cancelado')
SHIPPING = ('Não enviado','Etiqueta pendente','Aguardando coleta','Em trânsito','Saiu para entrega','Entregue','Atrasado','Ocorrência')
PRIORITIES = ('Baixa','Média','Alta','Urgente')
CATEGORIES = ('Retomar orçamento','Promoção','Estoque','Aguardar pagamento','Retorno futuro','Sem resposta','Outro')
VEHICLES = ('Lancer GT','Lancer CVT','Lancer MT','Lancer HL','Lancer HLE','Lancer HL-T','Ralliart','Evolution X','Outro')
PRODUCTION_STATES = ('Em produção','Finalizado','Vendido')
PRODUCTION_NEXT = {'Em produção':'Finalizado','Finalizado':'Vendido'}
PRODUCTION_ROLES = ('admin','gerente','estoque','vendedor','logistica')
PRODUCTS = ('Coletor Cat Delete',)
LOSS_REASONS = ('','Preço','Frete','Prazo','Falta de estoque','Desistência','Concorrência','Outro')
NAV = [
 ('dashboard','Dashboard','layout-dashboard','/'),('clients','CRM / Clientes','users','/clients'),
 ('pipeline','Funil de Vendas','columns-3','/opportunities'),('followups','Follow-ups','alarm-clock','/followups'),
 ('whatsapp','WhatsApp / Atendimento','message-circle','/modules/whatsapp'),
 ('orders','Pedidos','package','/orders'),('logistics','Logística / Rastreamento','truck','/modules/logistics'),
 ('stock','Estoque e Produtos','boxes','/modules/stock'),('production','Produção','wrench','/production'),
 ('finance','Financeiro','wallet','/modules/finance'),('marketing','Marketing / Campanhas','megaphone','/modules/marketing'),
 ('after-sales','Pós-venda','heart-handshake','/modules/after-sales'),('tasks','Tarefas / Planejamento','check-square','/tasks'),
 ('reports','Relatórios','chart-no-axes-column','/reports'),('insights','Insights Lancer Parts','lightbulb','/modules/insights'),
 ('integrations','Integrações','plug','/integrations'),('team','Equipe e Permissões','shield-check','/team'),
 ('settings','Configurações','settings','/settings')
]

# Stable local development secret; production should supply a strong env key.
secret_path = INSTANCE / '.session_secret'
if os.environ.get('LPC_SECRET_KEY'):
    SECRET = os.environ['LPC_SECRET_KEY']
elif secret_path.exists():
    SECRET = secret_path.read_text().strip()
else:
    SECRET = secrets.token_urlsafe(48)
    secret_path.write_text(SECRET)
    try: secret_path.chmod(0o600)
    except OSError: pass

app = FastAPI(title='Lancer Parts Control', docs_url=None, redoc_url=None)
_SAME_SITE = os.environ.get('LPC_COOKIE_SAMESITE', 'lax').lower()
if _SAME_SITE not in ('lax', 'strict', 'none'):
    _SAME_SITE = 'lax'
app.add_middleware(SessionMiddleware, secret_key=SECRET, https_only=os.environ.get('LPC_SECURE_COOKIES')=='1' or _SAME_SITE=='none', same_site=_SAME_SITE)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

@contextmanager
def db():
    cn = sqlite3.connect(DB, timeout=15)
    cn.row_factory = sqlite3.Row
    cn.execute('PRAGMA foreign_keys=ON')
    cn.execute('PRAGMA journal_mode=WAL')
    try:
        yield cn
        cn.commit()
    except BaseException:
        cn.rollback()
        raise
    finally:
        cn.close()

with db() as cn:
    cn.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))

jinja = Environment(loader=FileSystemLoader(ROOT/'templates'), autoescape=select_autoescape(['html','xml']))


def now(): return datetime.now(TZ)
def iso_now(): return now().isoformat(timespec='seconds')
def digits(v): return re.sub(r'\D','',str(v or ''))
def phone_key(v):
    d=digits(v)
    return d[2:] if d.startswith('55') and len(d) in (12,13) else d
def money_to_cents(v):
    s = str(v or '0').strip().replace('R$','').replace(' ','')
    if ',' in s: s = s.replace('.','').replace(',','.')
    try: n=round(float(s)*100)
    except (ValueError,OverflowError): raise ValueError('Informe um valor monetário válido.')
    if n<0 or n>10**12: raise ValueError('Valor fora do limite.')
    return n

def fmt_money(n): return f'R$ {int(n or 0)/100:,.2f}'.replace(',','@').replace('.',',').replace('@','.')
def fmt_date(s):
    if not s:return '—'
    try:return datetime.fromisoformat(s.replace('Z','+00:00')).strftime('%d/%m/%Y %H:%M' if 'T' in s or ' ' in s else '%d/%m/%Y')
    except ValueError:return s

def fmt_phone(s): return digits(s)
def wa_link(s):
    d=digits(s)
    if not d:return ''
    return 'https://wa.me/'+('55'+d if len(d) in (10,11) else d)
def qone(c,sql,args=()):return c.execute(sql,args).fetchone()
def qall(c,sql,args=()):return c.execute(sql,args).fetchall()
def record(row):return dict(row) if row else None

def hash_password(password):
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f'scrypt${salt.hex()}${derived.hex()}'

def verify_password(pw,stored):
    try:
        kind,salt,digest=stored.split('$')
        if kind!='scrypt':return False
        calc=hashlib.scrypt(pw.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1)
        return hmac.compare_digest(calc,bytes.fromhex(digest))
    except (ValueError,TypeError):return False


def csrf(request):
    if not request.session.get('csrf'):
        request.session['csrf']=secrets.token_urlsafe(24)
    return request.session['csrf']

def validate_csrf(request,data):
    token = data.get('csrf') or request.headers.get('x-csrf-token','')
    if not token or not hmac.compare_digest(str(token), str(request.session.get('csrf',''))):
        raise HTTPException(403, 'Token de segurança inválido. Atualize a página e tente novamente.')


def current_user(request):
    uid = request.session.get('uid')
    if not uid: return None
    with db() as c:
        row=qone(c,'SELECT id,name,email,role,active FROM users WHERE id=?',(uid,))
        return record(row) if row and row['active'] else None

def require(request, allowed=None):
    user=current_user(request)
    if not user:raise HTTPException(401,'Sessão expirada. Entre novamente.')
    if allowed and user['role'] not in allowed:raise HTTPException(403,'Você não tem permissão para esta ação.')
    return user

def owner_clause(user, field='owner_id'):
    return (f' AND {field}=?',[user['id']]) if user['role']=='vendedor' else ('',[])

def client_allowed(c,user,client_id):
    row=qone(c,'SELECT * FROM clients WHERE id=? AND archived=0',(client_id,))
    if not row:raise HTTPException(404,'Cliente não encontrado.')
    if user['role']=='vendedor' and row['owner_id']!=user['id']:
        raise HTTPException(403,'Cliente atribuído a outro vendedor.')
    return row

def audit(c,user,entity,entity_id,action,before=None,after=None):
    c.execute('INSERT INTO audit_logs (user_id,entity,entity_id,action,before_json,after_json) VALUES (?,?,?,?,?,?)',
        (user['id'],entity,entity_id,action,json.dumps(before,ensure_ascii=False,default=str) if before else None,
         json.dumps(after,ensure_ascii=False,default=str) if after else None))

def activity(c,user,client_id,kind,description):
    c.execute('INSERT INTO activities (client_id,kind,description,user_id) VALUES (?,?,?,?)',(client_id,kind,description,user['id']))
    c.execute('UPDATE clients SET last_contact_at=?,updated_at=? WHERE id=?',(iso_now(),iso_now(),client_id))

def redirect(url):return RedirectResponse(url,status_code=303)
def val(data,key,limit=2000):return str(data.get(key,'') or '').strip()[:limit]
def intval(v, default=0):
    try:return int(v)
    except (ValueError,TypeError):return default

def clean_date(v):
    s=str(v or '').strip()
    if not s:return None
    try:return datetime.fromisoformat(s).isoformat(timespec='minutes')
    except ValueError:raise ValueError('Data e horário inválidos.')

def clean_day(v):
    s=str(v or '').strip()
    if not s:return None
    try:return datetime.strptime(s,'%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:raise ValueError('Data inválida.')

def assignee(c,user,p):
    if user['role']=='vendedor':return user['id']
    uid=intval(p)
    if uid and qone(c,'SELECT id FROM users WHERE id=? AND active=1',(uid,)):return uid
    return user['id']

def render(request, template, **kwargs):
    user=current_user(request)
    if not user and template!='login.html':return redirect('/login')
    ctx = dict(request=request,user=user,nav=NAV,csrf=csrf(request),fmt_money=fmt_money,fmt_date=fmt_date,
      fmt_phone=fmt_phone,wa_link=wa_link,stages=STAGES,operations=OPERATIONS,payments=PAYMENTS,shipping_states=SHIPPING,
      priorities=PRIORITIES,vehicles=VEHICLES,categories=CATEGORIES,loss_reasons=LOSS_REASONS,
      now=now(), success=request.query_params.get('success'), error=request.query_params.get('error'))
    ctx.update(kwargs)
    return HTMLResponse(jinja.get_template(template).render(**ctx))

@app.exception_handler(HTTPException)
async def http_error(request, exc):
    if exc.status_code==401:return redirect('/login')
    if request.url.path.startswith('/api/'):
        return JSONResponse({'error':exc.detail},status_code=exc.status_code)
    response=render(request,'error.html',code=exc.status_code,detail=exc.detail,section='')
    response.status_code=exc.status_code
    return response

@app.middleware('http')
async def headers(request,call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    response.headers['Cache-Control']='no-store'
    return response

@app.get('/login')
def login(request:Request):
    if current_user(request):return redirect('/')
    return render(request,'login.html',section='login')

@app.post('/login')
async def login_post(request:Request):
    data=await request.form()
    validate_csrf(request,data)
    email=val(data,'email',250).lower()
    password=val(data,'password',500)
    with db() as c:
        u=qone(c,'SELECT * FROM users WHERE email=? AND active=1',(email,))
    if not u or not verify_password(password,u['password_hash']):
        return render(request,'login.html',section='login',login_error='E-mail ou senha inválidos.')
    request.session.clear()
    request.session['uid']=u['id']
    request.session['csrf']=secrets.token_urlsafe(24)
    return redirect('/')

@app.post('/logout')
async def logout(request:Request):
    data=await request.form();validate_csrf(request,data)
    request.session.clear();return redirect('/login')

@app.get('/')
def dashboard(request:Request):
    u=require(request)
    today=now().date().isoformat()
    monday=(now().date()-timedelta(days=now().weekday())).isoformat()
    first=now().date().replace(day=1).isoformat()
    scope,scopeargs=owner_clause(u,'owner_id')
    with db() as c:
        # Only paid, non-cancelled orders. Amounts are sale values, not cash receipt or profit.
        summary={}
        for key,start in [('today',today),('week',monday),('month',first)]:
            row=qone(c,"SELECT COUNT(*) count,COALESCE(SUM(total_cents),0) total FROM orders WHERE payment_status='Aprovado' AND operational_status!='Cancelado/devolvido' AND substr(created_at,1,10)>=?"+scope,(start,*scopeargs))
            summary[key]=dict(row)
        pending_orders=qone(c,"SELECT COUNT(*) n FROM orders WHERE payment_status='Pendente'"+scope,scopeargs)['n']
        open_opps=qone(c,"SELECT COUNT(*) n FROM opportunities WHERE stage NOT IN ('Venda concluída','Venda perdida')"+scope,scopeargs)['n']
        overdue=qone(c,"SELECT COUNT(*) n FROM followups WHERE status='Pendente' AND due_at<?"+scope,(iso_now(),*scopeargs))['n']
        today_tasks=qone(c,"SELECT COUNT(*) n FROM tasks WHERE status='Pendente' AND substr(due_at,1,10)<=?"+ (' AND assignee_id=?' if u['role']=='vendedor' else ''),(today,*([u['id']] if u['role']=='vendedor' else [])))['n']
        at_risk=qall(c,"SELECT id,order_number,operational_status,due_at FROM orders WHERE due_at<? AND operational_status NOT IN ('Entregue','Cancelado/devolvido')"+scope+" ORDER BY due_at LIMIT 5",(iso_now(),*scopeargs))
        urgent=qall(c,"SELECT f.*,cl.name client_name FROM followups f JOIN clients cl ON cl.id=f.client_id WHERE f.status='Pendente' AND f.due_at<=?"+(' AND f.owner_id=?' if u['role']=='vendedor' else '')+" ORDER BY f.due_at LIMIT 7",(iso_now(),*([u['id']] if u['role']=='vendedor' else [])))
        recent=qall(c,"SELECT o.*,cl.name client_name FROM orders o JOIN clients cl ON o.client_id=cl.id WHERE 1=1"+ (' AND o.owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY o.created_at DESC LIMIT 7',([u['id']] if u['role']=='vendedor' else []))
    return render(request,'dashboard.html',section='dashboard',summary=summary,pending_orders=pending_orders,open_opps=open_opps,overdue=overdue,today_tasks=today_tasks,at_risk=at_risk,urgent=urgent,recent=recent)

@app.get('/clients')
def clients(request:Request,q:str='',origin:str='',page:int=1):
    u=require(request,SALES)
    page=max(1,min(page,99999));filters=['archived=0'];args=[]
    if q:
        filters.append('(name LIKE ? OR phone_normalized LIKE ? OR email LIKE ?)')
        args += ['%'+q+'%','%'+phone_key(q)+'%','%'+q+'%']
    if origin:filters.append('origin=?');args.append(origin)
    if u['role']=='vendedor':filters.append('owner_id=?');args.append(u['id'])
    where=' AND '.join(filters)
    with db() as c:
        total=qone(c,'SELECT COUNT(*) n FROM clients WHERE '+where,args)['n']
        rows=qall(c,'SELECT cl.*,u.name owner FROM clients cl LEFT JOIN users u ON u.id=cl.owner_id WHERE '+where+' ORDER BY cl.created_at DESC LIMIT 25 OFFSET ?',(*args,(page-1)*25))
    return render(request,'clients.html',section='clients',rows=rows,q=q,origin=origin,page=page,total=total)

@app.get('/clients/new')
def client_new(request:Request):
    u=require(request,SALES)
    with db() as c: users=qall(c,'SELECT id,name FROM users WHERE active=1 AND role IN (\'admin\',\'gerente\',\'vendedor\') ORDER BY name')
    return render(request,'client_form.html',section='clients',item={},users=users,is_edit=False)

async def save_client(request, client_id=None):
    u=require(request,SALES);data=await request.form();validate_csrf(request,data)
    name=val(data,'name',150)
    if len(name)<2:return redirect(request.url.path+'?error=Informe um nome válido')
    phone=val(data,'phone',30); normalized=phone_key(phone)
    if not normalized:return redirect(request.url.path+'?error='+quote('Informe o número de WhatsApp do cliente'))
    if len(normalized)<10 or len(normalized)>13:return redirect(request.url.path+'?error='+quote('WhatsApp inválido. Use DDD + número, ex.: (62) 99999-9999'))
    fields=['name','phone','email','document','city','state','address','vehicle_model','modifications','interests','notes','tags','origin']
    values={f:val(data,f,3000 if f in ['notes','modifications','interests'] else 255) for f in fields}
    values['name']=name;values['phone_normalized']=normalized or None
    values['vehicle_year']=intval(data.get('vehicle_year')) or None
    if values['vehicle_year'] and not 1980<=values['vehicle_year']<=2100:
        return redirect(request.url.path+'?error=Ano inválido')
    with db() as c:
        old=record(client_allowed(c,u,client_id)) if client_id else None
        if normalized:
            found=qone(c,'SELECT id,name FROM clients WHERE phone_normalized=? AND archived=0 AND id!=?',(normalized,client_id or -1))
            if found:return redirect(request.url.path+'?error='+quote(f'Possível duplicidade: {found["name"]} (cliente #{found["id"]}). Confira antes de salvar.'))
        if values['document']:
            found=qone(c,'SELECT id FROM clients WHERE document=? AND archived=0 AND id!=?',(values['document'],client_id or -1))
            if found:return redirect(request.url.path+'?error=CPF/CNPJ já cadastrado')
        values['owner_id']=assignee(c,u,data.get('owner_id'))
        if client_id:
            cols=','.join(f'{k}=?' for k in values)
            c.execute(f'UPDATE clients SET {cols},updated_at=? WHERE id=?',(*values.values(),iso_now(),client_id))
            audit(c,u,'clients',client_id,'update',old,values)
            msg='Cliente atualizado'
        else:
            cols=','.join(values)
            cur=c.execute(f'INSERT INTO clients ({cols}) VALUES ({",".join("?" for _ in values)})',list(values.values()))
            client_id=cur.lastrowid;audit(c,u,'clients',client_id,'create',None,values)
            msg='Cliente cadastrado'
    return redirect(f'/clients/{client_id}?success='+quote(msg))

@app.post('/clients/new')
async def create_client(request:Request):return await save_client(request)
@app.get('/clients/{client_id}/edit')
def client_edit(request:Request,client_id:int):
    u=require(request,SALES)
    with db() as c:
        item=client_allowed(c,u,client_id)
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor') ORDER BY name")
    return render(request,'client_form.html',section='clients',item=item,users=users,is_edit=True)
@app.post('/clients/{client_id}/edit')
async def update_client(request:Request,client_id:int):return await save_client(request,client_id)
@app.get('/clients/{client_id}')
def client_detail(request:Request,client_id:int):
    u=require(request,SALES)
    with db() as c:
        item=client_allowed(c,u,client_id)
        timeline=qall(c,'SELECT a.*,u.name author FROM activities a LEFT JOIN users u ON u.id=a.user_id WHERE a.client_id=? ORDER BY a.created_at DESC',(client_id,))
        opps=qall(c,'SELECT * FROM opportunities WHERE client_id=? ORDER BY created_at DESC',(client_id,))
        followups=qall(c,'SELECT * FROM followups WHERE client_id=? ORDER BY due_at DESC',(client_id,))
        orders=qall(c,'SELECT * FROM orders WHERE client_id=? ORDER BY created_at DESC',(client_id,))
    return render(request,'client_detail.html',section='clients',item=item,timeline=timeline,opps=opps,followups=followups,orders=orders)
@app.post('/clients/{client_id}/activity')
async def client_activity(request:Request,client_id:int):
    u=require(request,SALES);data=await request.form();validate_csrf(request,data)
    description=val(data,'description',2000)
    if not description:return redirect(f'/clients/{client_id}?error=Descrição obrigatória')
    with db() as c:
        client_allowed(c,u,client_id);activity(c,u,client_id,'Contato',description)
        audit(c,u,'activities',client_id,'create',None,{'description':description})
    return redirect(f'/clients/{client_id}?success=Contato registrado')
@app.post('/clients/{client_id}/archive')
async def client_archive(request:Request,client_id:int):
    u=require(request,('admin','gerente'));data=await request.form();validate_csrf(request,data)
    with db() as c:
        old=record(client_allowed(c,u,client_id))
        c.execute('UPDATE clients SET archived=1,updated_at=? WHERE id=?',(iso_now(),client_id))
        audit(c,u,'clients',client_id,'archive',old,{'archived':1})
    return redirect('/clients?success=Cliente arquivado')

@app.get('/opportunities')
def opportunities(request:Request,q:str='',owner:int=0):
    u=require(request,SALES)
    filters=[];args=[]
    if q:filters.append('(cl.name LIKE ? OR o.product LIKE ?)');args.extend(('%'+q+'%',)*2)
    if u['role']=='vendedor':filters.append('o.owner_id=?');args.append(u['id'])
    elif owner:filters.append('o.owner_id=?');args.append(owner)
    where='WHERE '+' AND '.join(filters) if filters else ''
    with db() as c:
        rows=qall(c,f'SELECT o.*,cl.name client_name,cl.phone,u.name owner FROM opportunities o JOIN clients cl ON o.client_id=cl.id LEFT JOIN users u ON o.owner_id=u.id {where} ORDER BY o.updated_at DESC',args)
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor')")
    cols={s:[dict(r) for r in rows if r['stage']==s] for s in STAGES}
    return render(request,'opportunities.html',section='pipeline',columns=cols,q=q,owner=owner,users=users)

@app.get('/opportunities/new')
def opp_new(request:Request,client_id:int=0):
    u=require(request,SALES)
    with db() as c:
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor')")
    return render(request,'opportunity_form.html',section='pipeline',item={'client_id':client_id,'probability':30,'stage':STAGES[0]},clients=cl,users=users,is_edit=False)
@app.get('/opportunities/{oid}/edit')
def opp_edit(request:Request,oid:int):
    u=require(request,SALES)
    with db() as c:
        row=qone(c,'SELECT * FROM opportunities WHERE id=?',(oid,))
        if not row:raise HTTPException(404,'Oportunidade não encontrada')
        client_allowed(c,u,row['client_id'])
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor')")
    return render(request,'opportunity_form.html',section='pipeline',item=row,clients=cl,users=users,is_edit=True)

async def save_opportunity(request,oid=None):
    u=require(request,SALES);data=await request.form();validate_csrf(request,data)
    product=val(data,'product',255);client_id=intval(data.get('client_id'))
    if not product or not client_id:return redirect(request.url.path+'?error=Cliente e produto são obrigatórios')
    try:amount=money_to_cents(data.get('value'))
    except ValueError as exc:return redirect(request.url.path+'?error='+quote(str(exc)))
    stage=val(data,'stage',100)
    if stage not in STAGES:stage=STAGES[0]
    probability=max(0,min(100,intval(data.get('probability'),30)))
    with db() as c:
        client_allowed(c,u,client_id)
        old=None
        if oid:
            oldrow=qone(c,'SELECT * FROM opportunities WHERE id=?',(oid,))
            if not oldrow:raise HTTPException(404,'Oportunidade não encontrada')
            client_allowed(c,u,oldrow['client_id']);old=record(oldrow)
        values=dict(client_id=client_id,product=product,value_cents=amount,probability=probability,stage=stage,
          expected_close=clean_day(data.get('expected_close')),source=val(data,'source',80),
          owner_id=assignee(c,u,data.get('owner_id')),notes=val(data,'notes',3000),loss_reason=val(data,'loss_reason',100) if stage=='Venda perdida' else '')
        if oid:
            c.execute('UPDATE opportunities SET '+','.join(f'{k}=?' for k in values)+',updated_at=? WHERE id=?',(*values.values(),iso_now(),oid))
            audit(c,u,'opportunities',oid,'update',old,values)
            if old['stage']!=stage:activity(c,u,client_id,'Funil',f"Oportunidade #{oid}: {old['stage']} → {stage}")
        else:
            cur=c.execute('INSERT INTO opportunities ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
            oid=cur.lastrowid;audit(c,u,'opportunities',oid,'create',None,values)
            activity(c,u,client_id,'Funil',f'Oportunidade #{oid} criada para {product}')
    return redirect('/opportunities?success=Oportunidade salva')

@app.post('/opportunities/new')
async def opp_create(request:Request):return await save_opportunity(request)
@app.post('/opportunities/{oid}/edit')
async def opp_update(request:Request,oid:int):return await save_opportunity(request,oid)
@app.patch('/api/opportunities/{oid}/stage')
async def opp_stage(request:Request,oid:int):
    u=require(request,SALES)
    data=await request.json();validate_csrf(request,data)
    stage=data.get('stage')
    if stage not in STAGES:return JSONResponse({'error':'Etapa inválida'},status_code=400)
    with db() as c:
        old=qone(c,'SELECT * FROM opportunities WHERE id=?',(oid,))
        if not old:raise HTTPException(404,'Oportunidade não encontrada')
        client_allowed(c,u,old['client_id'])
        if stage=='Venda perdida':return JSONResponse({'error':'Para registrar uma perda, abra o card e informe o motivo.'},status_code=400)
        c.execute('UPDATE opportunities SET stage=?,updated_at=? WHERE id=?',(stage,iso_now(),oid))
        audit(c,u,'opportunities',oid,'stage_change',{'stage':old['stage']},{'stage':stage})
        activity(c,u,old['client_id'],'Funil',f"Oportunidade #{oid}: {old['stage']} → {stage}")
    return JSONResponse({'ok':True,'stage':stage})

@app.get('/followups')
def followups(request:Request,filter:str='todos',q:str=''):
    u=require(request,SALES)
    today=now().date();tomorrow=(today+timedelta(days=1)).isoformat();week=(today+timedelta(days=7)).isoformat()
    conditions=['1=1'];args=[]
    if u['role']=='vendedor':conditions.append('f.owner_id=?');args.append(u['id'])
    if q:conditions.append('(cl.name LIKE ? OR f.product LIKE ?)');args.extend(['%'+q+'%']*2)
    if filter=='hoje':conditions.extend(["f.status='Pendente'",'substr(f.due_at,1,10)=?']);args.append(today.isoformat())
    elif filter=='atrasados':conditions.extend(["f.status='Pendente'",'f.due_at<?']);args.append(iso_now())
    elif filter=='amanha':conditions.extend(["f.status='Pendente'",'substr(f.due_at,1,10)=?']);args.append(tomorrow)
    elif filter=='semana':conditions.extend(["f.status='Pendente'",'substr(f.due_at,1,10) BETWEEN ? AND ?']);args.extend([today.isoformat(),week])
    elif filter=='pendentes':conditions.append("f.status='Pendente'")
    elif filter=='concluidos':conditions.append("f.status='Concluído'")
    elif filter in ['Promoção','Estoque','Aguardar pagamento','Retorno futuro','Sem resposta']:
        conditions.append('f.category=?');args.append(filter)
    with db() as c:
        rows=qall(c,'SELECT f.*,cl.name client_name,cl.phone phone,u.name owner FROM followups f JOIN clients cl ON cl.id=f.client_id LEFT JOIN users u ON u.id=f.owner_id WHERE '+' AND '.join(conditions)+' ORDER BY CASE WHEN f.status=\'Pendente\' THEN 0 ELSE 1 END,f.due_at LIMIT 300',args)
    return render(request,'followups.html',section='followups',rows=rows,filter=filter,q=q,today=today.isoformat())

@app.get('/followups/new')
def followup_new(request:Request,client_id:int=0):
    u=require(request,SALES)
    with db() as c:
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor')")
    return render(request,'followup_form.html',section='followups',item={'client_id':client_id,'priority':'Média','status':'Pendente'},clients=cl,users=users,is_edit=False)
@app.get('/followups/{fid}/edit')
def followup_edit(request:Request,fid:int):
    u=require(request,SALES)
    with db() as c:
        item=qone(c,'SELECT * FROM followups WHERE id=?',(fid,))
        if not item:raise HTTPException(404,'Follow-up não encontrado')
        client_allowed(c,u,item['client_id'])
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,"SELECT id,name FROM users WHERE active=1 AND role IN ('admin','gerente','vendedor')")
    return render(request,'followup_form.html',section='followups',item=item,clients=cl,users=users,is_edit=True)

async def save_followup(request,fid=None):
    u=require(request,SALES);data=await request.form();validate_csrf(request,data)
    client_id=intval(data.get('client_id'));product=val(data,'product',255)
    try:due=clean_date(data.get('due_at'));quoted=money_to_cents(data.get('quoted_value'))
    except ValueError as exc:return redirect(request.url.path+'?error='+quote(str(exc)))
    if not due or not product or not client_id:return redirect(request.url.path+'?error=Preencha cliente, produto e prazo')
    with db() as c:
        client_allowed(c,u,client_id);old=None
        if fid:
            oldrow=qone(c,'SELECT * FROM followups WHERE id=?',(fid,))
            if not oldrow:raise HTTPException(404,'Follow-up não encontrado')
            client_allowed(c,u,oldrow['client_id']);old=record(oldrow)
        values=dict(client_id=client_id,product=product,quoted_value_cents=quoted,objection=val(data,'objection',500),
            due_at=due,priority=val(data,'priority',60) if val(data,'priority',60) in PRIORITIES else 'Média',
            owner_id=assignee(c,u,data.get('owner_id')),status=val(data,'status',30) if val(data,'status',30) in ('Pendente','Concluído') else 'Pendente',
            notes=val(data,'notes',3000),category=val(data,'category',100),recurring=val(data,'recurring',80))
        values['completed_at']=iso_now() if values['status']=='Concluído' else None
        if fid:
            c.execute('UPDATE followups SET '+','.join(f'{k}=?' for k in values)+',updated_at=? WHERE id=?',(*values.values(),iso_now(),fid))
            audit(c,u,'followups',fid,'update',old,values)
        else:
            cur=c.execute('INSERT INTO followups ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
            fid=cur.lastrowid;audit(c,u,'followups',fid,'create',None,values)
            activity(c,u,client_id,'Follow-up',f'Agendado retorno sobre {product} para {due}')
    return redirect('/followups?success=Follow-up salvo')

@app.post('/followups/new')
async def followup_create(request:Request):return await save_followup(request)
@app.post('/followups/{fid}/edit')
async def followup_update(request:Request,fid:int):return await save_followup(request,fid)
@app.post('/followups/{fid}/complete')
async def followup_complete(request:Request,fid:int):
    u=require(request,SALES);data=await request.form();validate_csrf(request,data)
    with db() as c:
        old=qone(c,'SELECT * FROM followups WHERE id=?',(fid,))
        if not old:raise HTTPException(404,'Follow-up não encontrado')
        client_allowed(c,u,old['client_id'])
        c.execute("UPDATE followups SET status='Concluído',completed_at=?,updated_at=? WHERE id=?",(iso_now(),iso_now(),fid))
        audit(c,u,'followups',fid,'complete',{'status':old['status']},{'status':'Concluído'})
        activity(c,u,old['client_id'],'Follow-up',f'Retorno sobre {old["product"]} concluído')
    return redirect('/followups?success=Follow-up concluído')

@app.get('/orders')
def orders(request:Request,q:str='',status:str='',page:int=1):
    u=require(request,OPS)
    page=max(1,page);where=['1=1'];args=[]
    if q:where.append('(o.order_number LIKE ? OR cl.name LIKE ? OR o.tracking_code LIKE ?)');args.extend(['%'+q+'%']*3)
    if status:where.append('o.operational_status=?');args.append(status)
    if u['role']=='vendedor':where.append('o.owner_id=?');args.append(u['id'])
    with db() as c:
        count=qone(c,'SELECT COUNT(*) n FROM orders o JOIN clients cl ON o.client_id=cl.id WHERE '+' AND '.join(where),args)['n']
        rows=qall(c,'SELECT o.*,cl.name client_name FROM orders o JOIN clients cl ON o.client_id=cl.id WHERE '+' AND '.join(where)+' ORDER BY o.created_at DESC LIMIT 25 OFFSET ?',(*args,(page-1)*25))
    return render(request,'orders.html',section='orders',rows=rows,total=count,page=page,q=q,status=status)
@app.get('/orders/new')
def order_new(request:Request,client_id:int=0):
    u=require(request,OPS)
    with db() as c:
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,'SELECT id,name FROM users WHERE active=1 ORDER BY name')
    return render(request,'order_form.html',section='orders',item={'client_id':client_id,'payment_status':'Pendente','operational_status':'Pedido recebido','shipping_status':'Não enviado'},clients=cl,users=users,is_edit=False,items=[])
@app.get('/orders/{oid}')
def order_detail(request:Request,oid:int):
    u=require(request,OPS)
    with db() as c:
        item=qone(c,'SELECT o.*,cl.name client_name,cl.phone,cl.email FROM orders o JOIN clients cl ON o.client_id=cl.id WHERE o.id=?',(oid,))
        if not item:raise HTTPException(404,'Pedido não encontrado')
        if u['role']=='vendedor' and item['owner_id']!=u['id']:raise HTTPException(403,'Pedido atribuído a outro vendedor')
        products=qall(c,'SELECT * FROM order_items WHERE order_id=?',(oid,))
        logs=qall(c,"SELECT a.*,u.name author FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id WHERE a.entity='orders' AND a.entity_id=? ORDER BY a.created_at DESC LIMIT 30",(oid,))
    return render(request,'order_detail.html',section='orders',item=item,products=products,logs=logs)
@app.get('/orders/{oid}/edit')
def order_edit(request:Request,oid:int):
    u=require(request,OPS)
    with db() as c:
        item=qone(c,'SELECT * FROM orders WHERE id=?',(oid,))
        if not item:raise HTTPException(404,'Pedido não encontrado')
        if u['role']=='vendedor' and item['owner_id']!=u['id']:raise HTTPException(403,'Pedido atribuído a outro vendedor')
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name',([u['id']] if u['role']=='vendedor' else []))
        users=qall(c,'SELECT id,name FROM users WHERE active=1 ORDER BY name')
        items=qall(c,'SELECT * FROM order_items WHERE order_id=?',(oid,))
    return render(request,'order_form.html',section='orders',item=item,clients=cl,users=users,is_edit=True,items=items)

async def save_order(request,oid=None):
    u=require(request,OPS);data=await request.form();validate_csrf(request,data)
    client_id=intval(data.get('client_id'));number=val(data,'order_number',80)
    if not client_id or not number:return redirect(request.url.path+'?error=Cliente e número do pedido são obrigatórios')
    try:
        total=money_to_cents(data.get('total'));shipcost=money_to_cents(data.get('shipping'));discount=money_to_cents(data.get('discount'))
    except ValueError as exc:return redirect(request.url.path+'?error='+quote(str(exc)))
    with db() as c:
        client_allowed(c,u,client_id) if u['role']=='vendedor' else None
        if not qone(c,'SELECT 1 FROM clients WHERE id=? AND archived=0',(client_id,)):
            return redirect(request.url.path+'?error=Cliente inválido')
        old=None
        if oid:
            row=qone(c,'SELECT * FROM orders WHERE id=?',(oid,))
            if not row:raise HTTPException(404,'Pedido não encontrado')
            if u['role']=='vendedor' and row['owner_id']!=u['id']:raise HTTPException(403,'Pedido atribuído a outro vendedor')
            old=record(row)
        if qone(c,'SELECT id FROM orders WHERE order_number=? AND id!=?',(number,oid or -1)):
            return redirect(request.url.path+'?error=Número do pedido já existe')
        values=dict(client_id=client_id,order_number=number,total_cents=total,shipping_cents=shipcost,discount_cents=discount,
          payment_status=val(data,'payment_status',60) if val(data,'payment_status',60) in PAYMENTS else 'Pendente',
          operational_status=val(data,'operational_status',100) if val(data,'operational_status',100) in OPERATIONS else OPERATIONS[0],
          shipping_status=val(data,'shipping_status',100) if val(data,'shipping_status',100) in SHIPPING else SHIPPING[0],
          carrier=val(data,'carrier',100),tracking_code=val(data,'tracking_code',100),
          destination_address=val(data,'destination_address',500),due_at=clean_date(data.get('due_at')),
          owner_id=assignee(c,u,data.get('owner_id')),notes=val(data,'notes',3000))
        if oid:
            c.execute('UPDATE orders SET '+','.join(f'{k}=?' for k in values)+',updated_at=? WHERE id=?',(*values.values(),iso_now(),oid))
            audit(c,u,'orders',oid,'update',old,values)
        else:
            values['source']='Manual'
            cur=c.execute('INSERT INTO orders ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
            oid=cur.lastrowid;audit(c,u,'orders',oid,'create',None,values)
            activity(c,u,client_id,'Pedido',f'Pedido manual {number} cadastrado')
        # Item rows are managed in this form, transactionally. No stock movements.
        product=val(data,'product',255)
        if product:
            sku=val(data,'sku',100);qty=intval(data.get('quantity'),1)
            if qty<1:qty=1
            unit=money_to_cents(data.get('unit_price'))
            c.execute('DELETE FROM order_items WHERE order_id=?',(oid,))
            c.execute('INSERT INTO order_items (order_id,product,sku,quantity,unit_cents) VALUES (?,?,?,?,?)',(oid,product,sku,qty,unit))
        elif oid:
            c.execute('DELETE FROM order_items WHERE order_id=?',(oid,))
    return redirect(f'/orders/{oid}?success=Pedido salvo')

@app.post('/orders/new')
async def order_create(request:Request):return await save_order(request)
@app.post('/orders/{oid}/edit')
async def order_update(request:Request,oid:int):return await save_order(request,oid)

@app.get('/tasks')
def tasks(request:Request,filter:str='pendentes'):
    u=require(request)
    clauses=['1=1'];args=[]
    if filter=='pendentes':clauses.append("t.status='Pendente'")
    elif filter=='concluidas':clauses.append("t.status='Concluída'")
    if u['role'] not in ('admin','gerente'):clauses.append('t.assignee_id=?');args.append(u['id'])
    with db() as c:
        rows=qall(c,'SELECT t.*,u.name assignee,cl.name client_name FROM tasks t LEFT JOIN users u ON t.assignee_id=u.id LEFT JOIN clients cl ON cl.id=t.client_id WHERE '+' AND '.join(clauses)+' ORDER BY CASE WHEN t.status=\'Pendente\' THEN 0 ELSE 1 END,t.due_at LIMIT 200',args)
    return render(request,'tasks.html',section='tasks',rows=rows,filter=filter)
@app.get('/tasks/new')
def task_new(request:Request,client_id:int=0,order_id:int=0):
    u=require(request)
    with db() as c:
        users=qall(c,'SELECT id,name FROM users WHERE active=1 ORDER BY name')
        cl=qall(c,'SELECT id,name FROM clients WHERE archived=0 ORDER BY name LIMIT 500') if u['role'] in SALES else []
        orders=qall(c,'SELECT id,order_number FROM orders ORDER BY created_at DESC LIMIT 100') if u['role'] in OPS else []
    return render(request,'task_form.html',section='tasks',users=users,clients=cl,orders=orders,item={'client_id':client_id,'order_id':order_id,'priority':'Média'})
@app.post('/tasks/new')
async def task_create(request:Request):
    u=require(request);data=await request.form();validate_csrf(request,data)
    title=val(data,'title',180)
    if not title:return redirect('/tasks/new?error=Título obrigatório')
    with db() as c:
        client_id=intval(data.get('client_id')) or None
        order_id=intval(data.get('order_id')) or None
        if client_id and u['role'] not in SALES:raise HTTPException(403,'Sem acesso a clientes')
        if client_id and u['role']=='vendedor':client_allowed(c,u,client_id)
        if order_id and u['role'] not in OPS:raise HTTPException(403,'Sem acesso a pedidos')
        if order_id and u['role']=='vendedor':
            o=qone(c,'SELECT owner_id FROM orders WHERE id=?',(order_id,))
            if not o or o['owner_id']!=u['id']:raise HTTPException(403,'Sem acesso ao pedido')
        values=dict(title=title,description=val(data,'description',2000),due_at=clean_date(data.get('due_at')),
            priority=val(data,'priority',50) if val(data,'priority',50) in PRIORITIES else 'Média',
            assignee_id=assignee(c,u,data.get('assignee_id')) if u['role'] in ('admin','gerente','vendedor') else u['id'],
            client_id=client_id,order_id=order_id)
        cur=c.execute('INSERT INTO tasks ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
        audit(c,u,'tasks',cur.lastrowid,'create',None,values)
    return redirect('/tasks?success=Tarefa criada')
@app.post('/tasks/{tid}/toggle')
async def task_toggle(request:Request,tid:int):
    u=require(request);data=await request.form();validate_csrf(request,data)
    with db() as c:
        old=qone(c,'SELECT * FROM tasks WHERE id=?',(tid,))
        if not old:raise HTTPException(404,'Tarefa não encontrada')
        if u['role'] not in ('admin','gerente') and old['assignee_id']!=u['id']:
            raise HTTPException(403,'Tarefa atribuída a outro usuário')
        new='Concluída' if old['status']=='Pendente' else 'Pendente'
        c.execute('UPDATE tasks SET status=?,updated_at=? WHERE id=?',(new,iso_now(),tid))
        audit(c,u,'tasks',tid,'status_change',{'status':old['status']},{'status':new})
    return redirect('/tasks?success=Tarefa atualizada')

@app.get('/integrations')
def integrations(request:Request):
    u=require(request,('admin','gerente'))
    with db() as c: rows=qall(c,'SELECT * FROM integrations ORDER BY service')
    return render(request,'integrations.html',section='integrations',rows=rows)

@app.get('/reports')
def reports(request:Request):
    u=require(request,('admin','gerente','financeiro'))
    with db() as c:
        totals=qone(c,"SELECT COUNT(*) count,COALESCE(SUM(total_cents),0) total FROM orders WHERE payment_status='Aprovado' AND operational_status!='Cancelado/devolvido'")
        by_payment=qall(c,'SELECT payment_status,COUNT(*) count,COALESCE(SUM(total_cents),0) total FROM orders GROUP BY payment_status')
    return render(request,'reports.html',section='reports',totals=totals,by_payment=by_payment)

@app.get('/export/{entity}')
def export(request:Request,entity:str):
    u=require(request,('admin','gerente','financeiro'))
    sqls={
      'orders':'SELECT order_number,source,total_cents,payment_status,operational_status,shipping_status,created_at FROM orders ORDER BY created_at DESC',
      'clients':'SELECT name,phone,email,city,state,vehicle_model,origin,created_at FROM clients WHERE archived=0 ORDER BY created_at DESC',
      'followups':'SELECT id,client_id,product,quoted_value_cents,due_at,status,priority,category FROM followups ORDER BY due_at DESC'
    }
    if entity not in sqls:raise HTTPException(404,'Exportação não disponível')
    if entity=='clients' and u['role']=='financeiro':raise HTTPException(403,'Sem acesso à lista de clientes')
    with db() as c:
        cur=c.execute(sqls[entity]);headers=[d[0] for d in cur.description];rows=cur.fetchall()
    buffer=io.StringIO();buffer.write('\ufeff');writer=csv.writer(buffer,delimiter=';');writer.writerow(headers)
    # Prevent CSV formula injection when opening exports in spreadsheets.
    def safe_csv(v):
        if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')): return "'"+v
        return v
    for r in rows:writer.writerow([safe_csv(v) for v in r])
    return StreamingResponse(iter([buffer.getvalue()]),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="lancer_{entity}.csv"'})

@app.get('/team')
def team(request:Request):
    u=require(request,('admin','gerente'))
    with db() as c: rows=qall(c,'SELECT id,name,email,role,active,created_at FROM users ORDER BY created_at DESC')
    return render(request,'team.html',section='team',rows=rows)
@app.post('/team')
async def team_create(request:Request):
    u=require(request,('admin',));data=await request.form();validate_csrf(request,data)
    email=val(data,'email',250).lower();name=val(data,'name',150);role=val(data,'role',40);pw=val(data,'password',255)
    if len(name)<2 or '@' not in email or role not in ROLES or len(pw)<12:
        return redirect('/team?error=Nome, e-mail, perfil e senha com 12 ou mais caracteres são obrigatórios')
    with db() as c:
        if qone(c,'SELECT id FROM users WHERE email=?',(email,)):return redirect('/team?error=E-mail já cadastrado')
        cur=c.execute('INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)',(name,email,hash_password(pw),role))
        audit(c,u,'users',cur.lastrowid,'create',None,{'name':name,'email':email,'role':role})
    return redirect('/team?success=Usuário criado')

@app.get('/settings')
def settings(request:Request):
    u=require(request,('admin','gerente'))
    return render(request,'settings.html',section='settings',db_location='instance/lancer_parts.db')

def production_clients(c,u):
    sql="SELECT id,name,phone FROM clients WHERE archived=0"+(' AND owner_id=?' if u['role']=='vendedor' else '')+' ORDER BY name LIMIT 500'
    return qall(c,sql,([u['id']] if u['role']=='vendedor' else []))

@app.get('/production')
def production(request:Request,status:str='',product:str=''):
    u=require(request,PRODUCTION_ROLES+('financeiro',))
    filters=['1=1'];args=[]
    if status in PRODUCTION_STATES:filters.append('p.status=?');args.append(status)
    if product:filters.append('p.product=?');args.append(product[:120])
    where=' AND '.join(filters)
    with db() as c:
        totals={s:0 for s in PRODUCTION_STATES}
        sargs=[product[:120]] if product else []
        for r in qall(c,'SELECT status,COALESCE(SUM(quantity),0) n FROM production_batches'+(' WHERE product=?' if product else '')+' GROUP BY status',sargs):
            totals[r['status']]=r['n']
        month=now().date().replace(day=1).isoformat()
        month_done=qone(c,"SELECT COALESCE(SUM(quantity),0) n FROM production_batches WHERE status IN ('Finalizado','Vendido') AND substr(finished_at,1,10)>=?"+(' AND product=?' if product else ''),(month,*sargs))['n']
        month_sold=qone(c,"SELECT COALESCE(SUM(quantity),0) n FROM production_batches WHERE status='Vendido' AND substr(sold_at,1,10)>=?"+(' AND product=?' if product else ''),(month,*sargs))['n']
        by_model=qall(c,"SELECT COALESCE(NULLIF(vehicle_model,''),'Não informado') model,SUM(CASE WHEN status='Em produção' THEN quantity ELSE 0 END) prod,SUM(CASE WHEN status='Finalizado' THEN quantity ELSE 0 END) done,SUM(CASE WHEN status='Vendido' THEN quantity ELSE 0 END) sold FROM production_batches"+(' WHERE product=?' if product else '')+" GROUP BY model ORDER BY model",sargs)
        rows=qall(c,'SELECT p.*,cl.name client_name,cl.phone client_phone,us.name owner FROM production_batches p LEFT JOIN clients cl ON cl.id=p.client_id LEFT JOIN users us ON us.id=p.owner_id WHERE '+where+" ORDER BY CASE p.status WHEN 'Em produção' THEN 0 WHEN 'Finalizado' THEN 1 ELSE 2 END, p.updated_at DESC LIMIT 300",args)
        products=[r['product'] for r in qall(c,'SELECT DISTINCT product FROM production_batches ORDER BY product')]
        clients_list=production_clients(c,u) if u['role'] in PRODUCTION_ROLES else []
    return render(request,'production.html',section='production',title='Produção',rows=rows,totals=totals,status=status,product=product,
        products=sorted(set(products)|set(PRODUCTS)),month_done=month_done,month_sold=month_sold,by_model=by_model,clients_list=clients_list,
        production_states=PRODUCTION_STATES,can_edit=u['role'] in PRODUCTION_ROLES)

@app.get('/production/new')
def production_new(request:Request):
    u=require(request,PRODUCTION_ROLES)
    return render(request,'production_form.html',section='production',title='Nova produção',products=PRODUCTS)

@app.post('/production/new')
async def production_create(request:Request):
    u=require(request,PRODUCTION_ROLES);data=await request.form();validate_csrf(request,data)
    product=val(data,'product',120) or PRODUCTS[0]
    qty=intval(data.get('quantity'))
    if not 1<=qty<=10000:return redirect('/production/new?error='+quote('Quantidade deve estar entre 1 e 10.000'))
    model=val(data,'vehicle_model',60)
    if model and model not in VEHICLES:model=''
    values=dict(product=product,vehicle_model=model or None,batch_code=val(data,'batch_code',60) or None,quantity=qty,
        status='Em produção',owner_id=u['id'],notes=val(data,'notes',2000) or None,started_at=iso_now())
    with db() as c:
        cur=c.execute(f'INSERT INTO production_batches ({",".join(values)}) VALUES ({",".join("?" for _ in values)})',list(values.values()))
        audit(c,u,'production_batches',cur.lastrowid,'create',None,values)
    return redirect('/production?success='+quote(f'{qty} unidade(s) de {product} em produção'))

@app.post('/production/{pid}/advance')
async def production_advance(request:Request,pid:int):
    u=require(request,PRODUCTION_ROLES);data=await request.form();validate_csrf(request,data)
    with db() as c:
        batch=record(qone(c,'SELECT * FROM production_batches WHERE id=?',(pid,)))
        if not batch:raise HTTPException(404,'Lote não encontrado')
        nxt=PRODUCTION_NEXT.get(batch['status'])
        if not nxt:return redirect('/production?error='+quote('Este lote já foi vendido'))
        qty=intval(data.get('quantity'),batch['quantity'])
        if not 1<=qty<=batch['quantity']:return redirect('/production?error='+quote(f'Quantidade deve estar entre 1 e {batch["quantity"]}'))
        client_id=None
        if nxt=='Vendido':
            client_id=intval(data.get('client_id')) or None
            if client_id:
                if u['role'] in SALES:client_allowed(c,u,client_id)
                elif not qone(c,'SELECT id FROM clients WHERE id=? AND archived=0',(client_id,)):raise HTTPException(404,'Cliente não encontrado.')
        stamp=iso_now()
        changes={'status':nxt,'updated_at':stamp}
        if nxt=='Finalizado':changes['finished_at']=stamp
        else:changes.update(sold_at=stamp,client_id=client_id)
        if qty<batch['quantity']:
            c.execute('UPDATE production_batches SET quantity=?,updated_at=? WHERE id=?',(batch['quantity']-qty,stamp,pid))
            new=dict(batch);new.pop('id');new.pop('created_at');new.update(changes);new['quantity']=qty
            cur=c.execute(f'INSERT INTO production_batches ({",".join(new)}) VALUES ({",".join("?" for _ in new)})',list(new.values()))
            audit(c,u,'production_batches',cur.lastrowid,'split',{'from':pid},new)
        else:
            c.execute(f'UPDATE production_batches SET {",".join(k+"=?" for k in changes)} WHERE id=?',(*changes.values(),pid))
            audit(c,u,'production_batches',pid,'status_change',{'status':batch['status']},changes)
        if client_id:activity(c,u,client_id,'Venda produção',f'{qty}x {batch["product"]} vendido(s)')
    label='finalizada(s)' if nxt=='Finalizado' else 'vendida(s)'
    return redirect('/production?success='+quote(f'{qty} unidade(s) {label}'))

@app.post('/production/{pid}/delete')
async def production_delete(request:Request,pid:int):
    u=require(request,('admin','gerente'));data=await request.form();validate_csrf(request,data)
    with db() as c:
        batch=record(qone(c,'SELECT * FROM production_batches WHERE id=?',(pid,)))
        if not batch:raise HTTPException(404,'Lote não encontrado')
        c.execute('DELETE FROM production_batches WHERE id=?',(pid,))
        audit(c,u,'production_batches',pid,'delete',batch,None)
    return redirect('/production?success=Registro removido')

MODULES={
 'whatsapp':('WhatsApp / Atendimento','Conectar a API oficial da Meta ou provedor aprovado, com webhooks assinados, caixa de entrada e atribuição.'),
 'logistics':('Logística / Rastreamento','Conectar rastreamento oficial, eventos de transportadoras e fluxo operacional de expedição.'),
 'stock':('Estoque e Produtos','Produtos, SKUs, reservas, movimentos e sincronização a partir da Nuvemshop.'),
 'finance':('Financeiro','Margens apenas com custos reais, contas a pagar e a receber, taxas e conciliação.'),
 'marketing':('Marketing / Campanhas','Campanhas, listas segmentadas e mensuração com consentimento de contato.'),
 'after-sales':('Pós-venda','Rotinas de satisfação, garantia, devoluções e relacionamento pós-entrega.'),
 'insights':('Insights Lancer Parts','Insights baseados em métricas verificáveis do banco, sem sugerir números fictícios.')
}
@app.get('/modules/{slug}')
def module_page(request:Request,slug:str):
    require(request)
    if slug not in MODULES:raise HTTPException(404,'Módulo não encontrado')
    title,description=MODULES[slug]
    return render(request,'module.html',section=slug,title=title,description=description)

@app.get('/search')
def search(request:Request,q:str=''):
    u=require(request)
    query=q.strip()[:100]
    clients=[];orders=[];opps=[];tasks=[]
    if query:
        with db() as c:
            if u['role'] in SALES:
                clients=qall(c,'SELECT id,name,phone FROM clients WHERE archived=0 AND (name LIKE ? OR phone_normalized LIKE ?)'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' LIMIT 15',('%'+query+'%','%'+phone_key(query)+'%',*([u['id']] if u['role']=='vendedor' else [])))
                opps=qall(c,'SELECT id,product,stage FROM opportunities WHERE product LIKE ?'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' LIMIT 12',('%'+query+'%',*([u['id']] if u['role']=='vendedor' else [])))
            if u['role'] in OPS:
                orders=qall(c,'SELECT id,order_number,operational_status FROM orders WHERE (order_number LIKE ? OR tracking_code LIKE ?)'+(' AND owner_id=?' if u['role']=='vendedor' else '')+' LIMIT 15',('%'+query+'%','%'+query+'%',*([u['id']] if u['role']=='vendedor' else [])))
            tasks=qall(c,'SELECT id,title,status FROM tasks WHERE title LIKE ?'+(' AND assignee_id=?' if u['role'] not in ('admin','gerente') else '')+' LIMIT 15',('%'+query+'%',*([u['id']] if u['role'] not in ('admin','gerente') else [])))
    return render(request,'search.html',section='',q=query,clients=clients,orders=orders,opps=opps,tasks=tasks)

# Explicit, audited local bootstrap. Never ships with a default account/password.
def bootstrap(name,email,password):
    if len(password)<12:raise ValueError('Senha inicial deve ter no mínimo 12 caracteres')
    with db() as c:
        if qone(c,'SELECT 1 FROM users LIMIT 1'):raise ValueError('Já existem usuários. Adicione outros pelo painel Equipe.')
        c.execute('INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)',(name,email.lower(),hash_password(password),'admin'))
    print('Administrador criado. Proteja a senha informada e configure HTTPS antes de publicar.')

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='bootstrap':
        if len(sys.argv)<4:
            print('Uso: python main.py bootstrap "Nome" email@exemplo.com (senha será solicitada sem eco)')
            sys.exit(2)
        import getpass
        bootstrap(sys.argv[2],sys.argv[3],getpass.getpass('Senha inicial (mín. 12 caracteres): '))
    else:
        print('Servidor: uvicorn main:app --reload --host 127.0.0.1 --port 8000')
